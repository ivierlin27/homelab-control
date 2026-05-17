"""LiteLLM proxy custom callback: per-call cost + latency to JSONL.

Loaded by the homelab model gateway via:

    litellm_settings:
      callbacks: custom_callbacks.proxy_handler_instance

The handler appends one JSON line per completed call to
``$LLM_CALLS_JSONL`` (default ``/var/log/llm-calls/llm-calls.jsonl``).

Design notes
------------
- Writes are best-effort and never raise into the request path. A logging
  failure here must never break an LLM call. The proxy will catch
  exceptions, but we belt-and-suspenders it with broad try/except.
- The record schema is the contract with ``apps/litellm_cost_relay/``;
  evolve via ``schema`` field bumps, not silent column renames.
- ``agent_principal`` is sourced from the ``x-agent-principal`` header set
  by ``apps/_shared/rlm/subcall.py``. Falls back to ``"unknown"``.
- ``request_id`` from the ``x-litellm-call-id`` header (proxy-assigned) so
  the same call can be cross-referenced in agent ledgers later.
- We extract ``response_cost`` opportunistically: LiteLLM auto-computes
  it for catalogued models, but our self-hosted vLLM models will yield
  ``0.0``. That is fine — token counts are the real signal locally.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

DEFAULT_PATH = "/var/log/llm-calls/llm-calls.jsonl"
SCHEMA_VERSION = 1

_write_lock = threading.Lock()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(ts: Any) -> float | None:
    """Coerce LiteLLM's datetime-ish ``start_time``/``end_time`` to epoch seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    timestamp = getattr(ts, "timestamp", None)
    if callable(timestamp):
        try:
            return float(timestamp())
        except Exception:
            return None
    return None


def _proxy_request(kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
    params = kwargs.get("litellm_params") or {}
    request_obj = params.get("proxy_server_request") if isinstance(params, Mapping) else None
    return request_obj if isinstance(request_obj, Mapping) else {}


def _header(req: Mapping[str, Any], name: str) -> str | None:
    headers = req.get("headers") if isinstance(req, Mapping) else None
    if not isinstance(headers, Mapping):
        return None
    lowered = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == lowered and isinstance(value, str):
            return value
    return None


def _usage(response_obj: Any) -> dict[str, Any]:
    usage = None
    if isinstance(response_obj, Mapping):
        usage = response_obj.get("usage")
    else:
        usage = getattr(response_obj, "usage", None)
    if usage is None:
        return {}
    if isinstance(usage, Mapping):
        return dict(usage)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def build_record(
    kwargs: Mapping[str, Any],
    response_obj: Any,
    start_time: Any,
    end_time: Any,
    *,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Pure-function builder of the JSONL record. Easy to unit test."""
    start = _epoch(start_time)
    end = _epoch(end_time)
    latency_ms = None
    if start is not None and end is not None:
        latency_ms = max(0, int(round((end - start) * 1000)))

    req = _proxy_request(kwargs)
    usage = _usage(response_obj)

    response_id = None
    if isinstance(response_obj, Mapping):
        response_id = response_obj.get("id")
    else:
        response_id = getattr(response_obj, "id", None)

    record: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "ts": end if end is not None else time.time(),
        "status": status,
        "model": kwargs.get("model"),
        "agent_principal": _header(req, "x-agent-principal") or "unknown",
        "request_id": _header(req, "x-litellm-call-id")
        or _header(req, "x-request-id")
        or response_id,
        "prompt_tokens": _safe_int(usage.get("prompt_tokens")),
        "completion_tokens": _safe_int(usage.get("completion_tokens")),
        "total_tokens": _safe_int(usage.get("total_tokens")),
        "cost_usd": _safe_float(kwargs.get("response_cost")),
        "latency_ms": latency_ms,
        "user": kwargs.get("user"),
    }
    if error is not None:
        record["error"] = error[:512]
    return record


def _output_path() -> Path:
    return Path(os.environ.get("LLM_CALLS_JSONL", DEFAULT_PATH))


def _append(record: Mapping[str, Any]) -> None:
    path = _output_path()
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as exc:
        print(f"[litellm-cost-jsonl] write failed: {exc}", file=sys.stderr, flush=True)


try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore
except Exception:
    CustomLogger = object  # type: ignore[assignment]


class CostJsonlHandler(CustomLogger):  # type: ignore[misc]
    """Append a JSON record per LLM call to ``$LLM_CALLS_JSONL``."""

    async def async_log_success_event(  # type: ignore[override]
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            record = build_record(kwargs, response_obj, start_time, end_time, status="success")
            _append(record)
        except Exception as exc:
            print(f"[litellm-cost-jsonl] success handler failed: {exc}", file=sys.stderr, flush=True)

    async def async_log_failure_event(  # type: ignore[override]
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            err = kwargs.get("exception") or kwargs.get("error")
            record = build_record(
                kwargs,
                response_obj,
                start_time,
                end_time,
                status="failure",
                error=str(err) if err is not None else None,
            )
            _append(record)
        except Exception as exc:
            print(f"[litellm-cost-jsonl] failure handler failed: {exc}", file=sys.stderr, flush=True)

    def log_success_event(  # type: ignore[override]
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            record = build_record(kwargs, response_obj, start_time, end_time, status="success")
            _append(record)
        except Exception as exc:
            print(f"[litellm-cost-jsonl] sync success handler failed: {exc}", file=sys.stderr, flush=True)

    def log_failure_event(  # type: ignore[override]
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            err = kwargs.get("exception") or kwargs.get("error")
            record = build_record(
                kwargs,
                response_obj,
                start_time,
                end_time,
                status="failure",
                error=str(err) if err is not None else None,
            )
            _append(record)
        except Exception as exc:
            print(f"[litellm-cost-jsonl] sync failure handler failed: {exc}", file=sys.stderr, flush=True)


proxy_handler_instance = CostJsonlHandler()
