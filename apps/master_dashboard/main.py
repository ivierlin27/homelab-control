"""Master dashboard for the homelab agent fleet (Phase 0.12 MVP).

A FastAPI + Jinja2 + HTMX app that surfaces, on one page:

- Cost / latency from the memory-engine ``llm_calls`` table
  (fetched via the n8n read endpoint — Alienware has no direct PG path
  into the memory-engine LXC).
- Live audit tail across every per-agent ``trust-ledger.jsonl`` under
  ``~/.local/state/homelab-control/``, streamed via Server-Sent Events.
- Restic backup status across every reachable repo (local /mnt/spinny,
  sftp to Proxmox, and the inbound mirror from Proxmox).
- Per-agent / per-service systemd presence (active / failed / inactive).

Design notes
------------
- Server-rendered. No JS build. HTMX + Tailwind via CDN. Authentik / SSO
  is deferred — bind to the LAN address only.
- Every external read is **cached** (5 min for cost + backup; 30 s for
  presence; audit tail is read-once + tailed) so a refresh storm cannot
  hammer PG, restic, or systemctl.
- Failures degrade per-tile: a broken n8n endpoint does not blank the
  whole page; the cost tile shows an error band and the other three
  keep working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger("master-dashboard")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ----- config (env-driven; sensible local defaults) ----------------------

AUDIT_ROOT = Path(
    os.environ.get(
        "DASHBOARD_AUDIT_ROOT",
        str(Path.home() / ".local/state/homelab-control"),
    )
)
COST_SUMMARY_URL = os.environ.get("DASHBOARD_COST_SUMMARY_URL", "")
COST_SUMMARY_TOKEN = os.environ.get("DASHBOARD_COST_SUMMARY_TOKEN", "")

# Comma-separated restic repos. Same env shape as the backup runner.
BACKUP_REPOSITORIES = [
    r.strip() for r in os.environ.get("DASHBOARD_BACKUP_REPOSITORIES", "").split(",") if r.strip()
]
RESTIC_PASSWORD_FILE = os.environ.get("DASHBOARD_RESTIC_PASSWORD_FILE", "")
RESTIC_BIN = os.environ.get("RESTIC_BIN", "restic")

# Units to surface on the presence tile. Group label → list of unit names.
PRESENCE_UNITS: dict[str, list[str]] = {
    "Gateway + relay": [
        "homelab-model-gateway.service",
        "alienware-litellm-cost-relay.service",
        "alienware-vllm-strong-long.service",
    ],
    "Agents (core)": [
        "alienware-executive-agent.service",
        "alienware-author-agent.service",
        "alienware-review-agent.service",
        "alienware-homelab-maintainer-agent.service",
    ],
    "Agents (Discord)": [
        "alienware-executive-discord.service",
        "alienware-agent-homelab-discord.service",
        "alienware-agent-homelab-maintainer-discord.service",
        "alienware-agent-review-discord.service",
    ],
    "Glue": [
        "alienware-agent-event-dispatcher.service",
        "alienware-agent-activity.service",
        "alienware-executive-chat.service",
    ],
    "Backup timers": [
        "alienware-backup-hot.timer",
        "alienware-backup-full.timer",
        "alienware-backup-dr-drill.timer",
    ],
}

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ----- cache primitives --------------------------------------------------

@dataclass
class _Cached:
    value: Any = None
    fetched_at: float = 0.0
    error: str | None = None


class TTLCache:
    """Single-flight TTL cache. Coalesces concurrent refreshes."""

    def __init__(self, ttl_seconds: float, fetcher) -> None:
        self.ttl = ttl_seconds
        self.fetcher = fetcher
        self._state = _Cached()
        self._lock = asyncio.Lock()

    async def get(self) -> _Cached:
        now = time.monotonic()
        if now - self._state.fetched_at < self.ttl and self._state.value is not None:
            return self._state
        async with self._lock:
            now = time.monotonic()
            if now - self._state.fetched_at < self.ttl and self._state.value is not None:
                return self._state
            try:
                value = await self.fetcher()
                self._state = _Cached(value=value, fetched_at=now, error=None)
            except Exception as exc:  # noqa: BLE001 — degrade per tile
                log.warning("cache fetch failed: %s", exc)
                self._state = _Cached(
                    value=self._state.value,
                    fetched_at=now,
                    error=str(exc)[:200],
                )
            return self._state


# ----- fetchers ----------------------------------------------------------

async def _fetch_cost_summary() -> dict[str, Any]:
    if not COST_SUMMARY_URL:
        raise RuntimeError("DASHBOARD_COST_SUMMARY_URL not configured")
    headers = {}
    if COST_SUMMARY_TOKEN:
        headers["Authorization"] = f"Bearer {COST_SUMMARY_TOKEN}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(COST_SUMMARY_URL, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def _fetch_backup_status() -> list[dict[str, Any]]:
    if not BACKUP_REPOSITORIES:
        return []
    env = {
        **os.environ,
        "RESTIC_PASSWORD_FILE": RESTIC_PASSWORD_FILE,
        "XDG_CACHE_HOME": os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")),
    }
    out: list[dict[str, Any]] = []
    for repo in BACKUP_REPOSITORIES:
        entry: dict[str, Any] = {"repo": repo, "snapshots": [], "error": None}
        try:
            proc = await asyncio.create_subprocess_exec(
                RESTIC_BIN, "snapshots", "--json", "--no-lock",
                env={**env, "RESTIC_REPOSITORY": repo},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                entry["error"] = stderr.decode(errors="replace").strip()[:200]
            else:
                snaps = json.loads(stdout.decode())
                # Group by (hostname, tags) → most recent
                by_key: dict[tuple, dict[str, Any]] = {}
                for s in snaps:
                    key = (s.get("hostname"), tuple(sorted(s.get("tags") or [])))
                    prev = by_key.get(key)
                    if prev is None or s.get("time", "") > prev.get("time", ""):
                        by_key[key] = s
                entry["snapshots"] = sorted(
                    by_key.values(), key=lambda x: x.get("time", ""), reverse=True
                )
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:200]
        out.append(entry)
    return out


async def _fetch_presence() -> list[dict[str, Any]]:
    """systemctl --user is-active <unit> + last invocation result, per group."""
    groups: list[dict[str, Any]] = []
    for label, units in PRESENCE_UNITS.items():
        rows: list[dict[str, Any]] = []
        for unit in units:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "--user", "show", unit,
                "--property=ActiveState,SubState,Result,ActiveEnterTimestamp,Description",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            props = dict(
                line.split("=", 1)
                for line in stdout.decode().splitlines()
                if "=" in line
            )
            rows.append(
                {
                    "unit": unit,
                    "active": props.get("ActiveState", "unknown"),
                    "sub": props.get("SubState", ""),
                    "result": props.get("Result", ""),
                    "since": props.get("ActiveEnterTimestamp", ""),
                    "description": props.get("Description", ""),
                }
            )
        groups.append({"label": label, "rows": rows})
    return groups


# Remote hosts (host, ssh-target, label). Schedule tile pulls timers from each.
# ssh-target must be batch-mode-reachable from the dashboard host's account.
REMOTE_SCHEDULE_HOSTS = [
    t.strip() for t in
    os.environ.get("DASHBOARD_REMOTE_TIMER_HOSTS", "root@proxmox.dev-path.org").split(",")
    if t.strip()
]


def _parse_timer_json(blob: str, host: str) -> list[dict[str, Any]]:
    """systemd ts fields are microseconds since epoch, 0 == never/unscheduled."""
    try:
        items = json.loads(blob)
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        next_us = it.get("next") or 0
        last_us = it.get("last") or 0
        out.append({
            "host": host,
            "unit": it.get("unit", "?"),
            "activates": it.get("activates", "") or "",
            "next_epoch": next_us / 1_000_000.0 if next_us else None,
            "last_epoch": last_us / 1_000_000.0 if last_us else None,
        })
    return out


async def _list_timers_local() -> list[dict[str, Any]]:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "list-timers", "--all", "--output=json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
    if proc.returncode != 0:
        return []
    return _parse_timer_json(stdout.decode(), host="alienware")


async def _list_timers_ssh(ssh_target: str) -> list[dict[str, Any]]:
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
        ssh_target,
        "systemctl list-timers --all --output=json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return [{"host": ssh_target, "unit": "<ssh timeout>", "activates": "",
                 "next_epoch": None, "last_epoch": None}]
    if proc.returncode != 0:
        return []
    # Host label is the short name after the @
    host = ssh_target.split("@", 1)[-1].split(".", 1)[0]
    return _parse_timer_json(stdout.decode(), host=host)


async def _fetch_schedule() -> list[dict[str, Any]]:
    tasks: list[Any] = [_list_timers_local()]
    for tgt in REMOTE_SCHEDULE_HOSTS:
        tasks.append(_list_timers_ssh(tgt))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    rows: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("schedule fetch leg failed: %s", r)
            continue
        rows.extend(r)
    # Hide systemd-internal timers; they aren't ours and just add noise
    rows = [
        r for r in rows
        if not r["unit"].startswith(("systemd-", "dnf-", "logrotate", "grub-"))
    ]
    # Sort: scheduled (next_epoch != None) first by soonest, then unscheduled
    now = time.time()
    rows.sort(key=lambda r: (r["next_epoch"] is None, r["next_epoch"] or 0))
    for r in rows:
        r["next_in_seconds"] = (r["next_epoch"] - now) if r["next_epoch"] else None
        r["last_ago_seconds"] = (now - r["last_epoch"]) if r["last_epoch"] else None
    return rows


_cost_cache = TTLCache(ttl_seconds=300, fetcher=_fetch_cost_summary)
_backup_cache = TTLCache(ttl_seconds=300, fetcher=_fetch_backup_status)
_presence_cache = TTLCache(ttl_seconds=30, fetcher=_fetch_presence)
_schedule_cache = TTLCache(ttl_seconds=60, fetcher=_fetch_schedule)


# ----- audit tail (file iteration + SSE) --------------------------------

def _list_ledgers() -> list[Path]:
    if not AUDIT_ROOT.exists():
        return []
    return sorted(AUDIT_ROOT.glob("agent-*/trust-ledger.jsonl"))


def _read_tail(path: Path, max_lines: int = 50) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            # Read the last ~64KB; cheaper than scanning the whole file
            chunk = max(0, size - 64 * 1024)
            fh.seek(chunk)
            tail_bytes = fh.read()
    except OSError:
        return []
    lines = tail_bytes.splitlines()[-max_lines:]
    out: list[dict[str, Any]] = []
    for raw in lines:
        try:
            rec = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        rec["_source"] = path.parent.name
        out.append(rec)
    return out


def _read_recent_events(per_ledger: int = 25) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for ledger in _list_ledgers():
        events.extend(_read_tail(ledger, max_lines=per_ledger))
    # Sort newest first by timestamp (string ISO is sortable; fall back to 0)
    events.sort(key=lambda e: e.get("ts") or e.get("timestamp") or "", reverse=True)
    return events[:100]


async def _sse_audit_stream() -> AsyncIterator[bytes]:
    """Tail every known ledger, emit new lines as SSE events."""
    offsets: dict[Path, int] = {}
    # Prime offsets to EOF so we only stream NEW events; the initial render
    # already loaded the recent history.
    for ledger in _list_ledgers():
        try:
            offsets[ledger] = ledger.stat().st_size
        except OSError:
            offsets[ledger] = 0
    keepalive_at = time.monotonic()
    while True:
        emitted = False
        for ledger in _list_ledgers():
            try:
                size = ledger.stat().st_size
            except OSError:
                continue
            start = offsets.get(ledger, 0)
            if size < start:
                # truncated or rotated; reset
                start = 0
            if size == start:
                offsets[ledger] = size
                continue
            try:
                with ledger.open("rb") as fh:
                    fh.seek(start)
                    chunk = fh.read(size - start)
            except OSError:
                continue
            offsets[ledger] = size
            for raw in chunk.splitlines():
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                rec["_source"] = ledger.parent.name
                payload = json.dumps(rec, default=str)
                yield f"event: audit\ndata: {payload}\n\n".encode()
                emitted = True
        if not emitted and time.monotonic() - keepalive_at > 15:
            yield b": keepalive\n\n"
            keepalive_at = time.monotonic()
        await asyncio.sleep(1.0)


# ----- app ---------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("master-dashboard starting; audit root=%s ledgers=%d backup repos=%d",
             AUDIT_ROOT, len(_list_ledgers()), len(BACKUP_REPOSITORIES))
    yield


app = FastAPI(title="Homelab control · master dashboard", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cost = await _cost_cache.get()
    backup = await _backup_cache.get()
    presence = await _presence_cache.get()
    schedule = await _schedule_cache.get()
    audit_events = _read_recent_events()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "cost": cost,
            "backup": backup,
            "presence": presence,
            "schedule": schedule,
            "audit_events": audit_events,
            "audit_event_count": sum(1 for _ in _list_ledgers()),
        },
    )


@app.get("/tiles/cost", response_class=HTMLResponse)
async def tile_cost(request: Request):
    cost = await _cost_cache.get()
    return templates.TemplateResponse(request, "_tile_cost.html", {"cost": cost})


@app.get("/tiles/backup", response_class=HTMLResponse)
async def tile_backup(request: Request):
    backup = await _backup_cache.get()
    return templates.TemplateResponse(request, "_tile_backup.html", {"backup": backup})


@app.get("/tiles/presence", response_class=HTMLResponse)
async def tile_presence(request: Request):
    presence = await _presence_cache.get()
    return templates.TemplateResponse(
        request, "_tile_presence.html", {"presence": presence}
    )


@app.get("/tiles/schedule", response_class=HTMLResponse)
async def tile_schedule(request: Request):
    schedule = await _schedule_cache.get()
    return templates.TemplateResponse(
        request, "_tile_schedule.html", {"schedule": schedule}
    )


@app.get("/tiles/audit", response_class=HTMLResponse)
async def tile_audit(request: Request):
    return templates.TemplateResponse(
        request, "_tile_audit.html", {"audit_events": _read_recent_events()},
    )


@app.get("/sse/audit")
async def sse_audit():
    return StreamingResponse(_sse_audit_stream(), media_type="text/event-stream")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
