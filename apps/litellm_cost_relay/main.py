"""Ships LiteLLM cost/latency JSONL records to memory-engine.

Reads the JSONL written by ``apps/_shared/litellm_callbacks/`` and
POSTs batches to ``$LLM_COST_RELAY_URL`` (typically an n8n webhook on
the memory-engine LXC that inserts into ``llm_calls`` in Postgres).

Design:

- **Durable offset**: ``$LLM_COST_RELAY_STATE`` holds the byte offset
  through which we have successfully shipped. On crash/restart we
  resume from there. Offset is only advanced after a successful POST.
- **Dry-run safe**: if ``LLM_COST_RELAY_URL`` is unset or empty, the
  relay logs would-ship counts and tracks offsets normally. This is
  useful as a smoke test before the webhook is wired up.
- **Backoff with cap**: failures back off exponentially (1s → 60s) and
  the relay keeps the loop alive — never crashes on a single bad batch.
- **Never blocks the callback**: the gateway only writes the JSONL; this
  daemon is the only network-touching component.

The HTTP payload shape is::

    {"schema": 1, "records": [<record>, <record>, ...]}

The receiver (n8n workflow) is responsible for inserting each record
into ``llm_calls`` and acking with HTTP 2xx.

See ``docs/runbooks/litellm-cost-relay.md`` for the n8n setup.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

log = logging.getLogger("litellm-cost-relay")

DEFAULT_JSONL = "/var/log/llm-calls/llm-calls.jsonl"
DEFAULT_STATE = "/var/log/llm-calls/.relay-offset"
DEFAULT_INTERVAL_S = 30.0
DEFAULT_BATCH = 200
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_BACKOFF_INITIAL_S = 1.0
DEFAULT_BACKOFF_MAX_S = 60.0


class RelayConfig:
    def __init__(
        self,
        *,
        jsonl_path: Path,
        state_path: Path,
        url: str | None,
        interval_s: float,
        batch: int,
        timeout_s: float,
        bearer_token: str | None = None,
    ) -> None:
        self.jsonl_path = jsonl_path
        self.state_path = state_path
        self.url = url or None
        self.interval_s = interval_s
        self.batch = batch
        self.timeout_s = timeout_s
        self.bearer_token = bearer_token or None

    @classmethod
    def from_env(cls) -> RelayConfig:
        return cls(
            jsonl_path=Path(os.environ.get("LLM_CALLS_JSONL", DEFAULT_JSONL)),
            state_path=Path(os.environ.get("LLM_COST_RELAY_STATE", DEFAULT_STATE)),
            url=os.environ.get("LLM_COST_RELAY_URL", "").strip() or None,
            interval_s=float(os.environ.get("LLM_COST_RELAY_INTERVAL_S", DEFAULT_INTERVAL_S)),
            batch=int(os.environ.get("LLM_COST_RELAY_BATCH", DEFAULT_BATCH)),
            timeout_s=float(os.environ.get("LLM_COST_RELAY_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
            bearer_token=os.environ.get("LLM_COST_RELAY_TOKEN") or None,
        )


def read_offset(state_path: Path) -> int:
    try:
        return int(state_path.read_text(encoding="utf-8").strip() or "0")
    except FileNotFoundError:
        return 0
    except (ValueError, OSError) as exc:
        log.warning("offset read failed (%s); restarting from 0", exc)
        return 0


def write_offset(state_path: Path, offset: int) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(str(offset), encoding="utf-8")
    os.replace(tmp, state_path)


def read_batch(jsonl_path: Path, offset: int, batch: int) -> tuple[list[dict], int]:
    """Return (records, new_offset). ``new_offset`` points at end of the last
    fully-read record, or ``offset`` if nothing new was available."""
    if not jsonl_path.exists():
        return [], offset
    size = jsonl_path.stat().st_size
    if size < offset:
        log.warning("jsonl shrank (size=%d < offset=%d); restarting from 0", size, offset)
        offset = 0
    records: list[dict] = []
    cursor = offset
    try:
        with jsonl_path.open("rb") as fh:
            fh.seek(offset)
            while len(records) < batch:
                line = fh.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    break
                cursor = fh.tell()
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(json.loads(stripped))
                except json.JSONDecodeError as exc:
                    log.warning("skipping malformed record at offset %d: %s", cursor, exc)
    except OSError as exc:
        log.error("read failed: %s", exc)
        return [], offset
    return records, cursor


def post_batch(url: str, records: list[dict], *, timeout_s: float, bearer: str | None) -> None:
    body = json.dumps({"schema": 1, "records": records}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        if status >= 300:
            raise RuntimeError(f"relay POST returned {status}")


class _StopRequested(Exception):
    pass


def _install_signals(stop: dict) -> None:
    def handler(signum, frame):  # noqa: ARG001
        stop["set"] = True
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def run_loop(config: RelayConfig, *, max_iterations: int | None = None) -> int:
    """Main daemon loop. Returns the number of records shipped this run."""
    shipped = 0
    backoff = DEFAULT_BACKOFF_INITIAL_S
    iteration = 0
    stop = {"set": False}
    _install_signals(stop)
    log.info(
        "relay starting: src=%s state=%s url=%s interval=%.1fs batch=%d",
        config.jsonl_path, config.state_path,
        config.url or "<dry-run>", config.interval_s, config.batch,
    )
    while not stop["set"]:
        iteration += 1
        offset = read_offset(config.state_path)
        records, new_offset = read_batch(config.jsonl_path, offset, config.batch)
        if not records:
            log.debug("no new records (offset=%d)", offset)
        elif config.url is None:
            log.info("dry-run: would ship %d records (offset %d -> %d)",
                     len(records), offset, new_offset)
            write_offset(config.state_path, new_offset)
            shipped += len(records)
            backoff = DEFAULT_BACKOFF_INITIAL_S
        else:
            try:
                post_batch(config.url, records, timeout_s=config.timeout_s, bearer=config.bearer_token)
                write_offset(config.state_path, new_offset)
                shipped += len(records)
                log.info("shipped %d records (offset %d -> %d)",
                         len(records), offset, new_offset)
                backoff = DEFAULT_BACKOFF_INITIAL_S
            except (urllib.error.URLError, RuntimeError, OSError) as exc:
                log.warning("ship failed: %s (backoff %.1fs)", exc, backoff)
                _sleep_interruptible(backoff, stop)
                backoff = min(backoff * 2, DEFAULT_BACKOFF_MAX_S)
                if max_iterations is not None and iteration >= max_iterations:
                    break
                continue
        if max_iterations is not None and iteration >= max_iterations:
            break
        _sleep_interruptible(config.interval_s, stop)
    log.info("relay stopped after %d iterations; shipped %d total", iteration, shipped)
    return shipped


def _sleep_interruptible(seconds: float, stop: dict) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not stop["set"]:
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LiteLLM cost JSONL relay")
    parser.add_argument("--once", action="store_true", help="run one iteration and exit (for cron/timer)")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config = RelayConfig.from_env()
    shipped = run_loop(config, max_iterations=1 if args.once else None)
    return 0 if shipped >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
