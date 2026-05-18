"""Health monitor orchestrator.

Runs every check, applies the state-transition engine, posts a Discord
message for each healthy↔unhealthy flip (one message per check, batched
into a single post per run), and writes an audit-ledger row per
transition. Steady-state silence.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from _shared.audit import AuditLog  # noqa: E402
from maintenance.lock import DEFAULT_LOCK_PATH as MAINTENANCE_LOCK  # noqa: E402
from maintenance.lock import load_lock as load_maintenance_lock  # noqa: E402

from .checks import ALL_CHECKS  # noqa: E402
from .core import CheckResult, StateStore, Status, Transition  # noqa: E402

log = logging.getLogger("health-monitor")

PRINCIPAL = "agent:health-monitor"
DEFAULT_STATE = Path.home() / ".local/state/homelab-control/health-monitor/state.json"
DEFAULT_AUDIT = Path.home() / ".local/state/homelab-control/health-monitor/audit.jsonl"

DEFAULT_DISCORD = os.environ.get("HEALTH_MONITOR_DISCORD_WEBHOOK", "")

# Severity emoji used in Discord posts
ARROW = {
    (Status.HEALTHY, Status.UNHEALTHY): "🔴",
    (Status.UNHEALTHY, Status.HEALTHY): "🟢",
    (Status.UNKNOWN, Status.UNHEALTHY): "🔴",
    (Status.UNKNOWN, Status.HEALTHY): "🟢",
}


def collect_all(checks: Iterable | None = None) -> list[CheckResult]:
    # Resolve at call time via module globals so tests can monkeypatch
    # ``ALL_CHECKS`` on this module to swap in fakes.
    if checks is None:
        checks = globals()["ALL_CHECKS"]
    out: list[CheckResult] = []
    for fn in checks:
        try:
            out.extend(fn())
        except Exception as exc:  # noqa: BLE001 — defensive: one broken check shouldn't kill the run
            log.warning("check %s raised: %s", fn.__name__, exc)
            out.append(CheckResult(
                name=f"check_fn:{fn.__name__}", status=Status.UNKNOWN,
                detail=f"{type(exc).__name__}: {exc!s}"[:200],
            ))
    return out


def format_discord(transitions: list[Transition]) -> str:
    if not transitions:
        return ""
    lines = ["**Health monitor transitions**"]
    for t in transitions:
        emoji = ARROW.get((t.previous, t.current), "⚪")
        runbook = f"  _see_ `{t.runbook}`" if t.runbook else ""
        lines.append(f"{emoji} `{t.name}` {t.previous.value} → **{t.current.value}** — {t.detail[:180]}{runbook}")
    return "\n".join(lines)


def post_discord(webhook_url: str, content: str) -> None:
    if not content:
        return
    if len(content) > 1900:
        content = content[:1880] + "\n…truncated."
    try:
        r = httpx.post(webhook_url, json={"content": content}, timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("discord post failed: %s", exc)


def run(
    *,
    state_path: Path = DEFAULT_STATE,
    audit_path: Path = DEFAULT_AUDIT,
    discord_webhook: str = DEFAULT_DISCORD,
    dry_run: bool = False,
    checks: Iterable | None = None,
    notifier=None,
    maintenance_lock_path: Path | None = None,
) -> dict:
    """Run one health-check pass.

    ``notifier`` is a ``(webhook_url, content) -> None`` callable; defaults
    to :func:`post_discord`. Tests inject a recorder. Pass-through value
    ``False`` disables posting entirely (used by ``--dry-run``).
    """
    notify = notifier if notifier is not None else post_discord
    started = time.monotonic()
    results = collect_all(checks)
    log.info("collected %d check results", len(results))

    store = StateStore(state_path)
    transitions = store.transitions(results)
    log.info("transitions to alert: %d", len(transitions))

    # Consult the maintenance lock (if any) — alerts for in-scope checks
    # are suppressed, but state and audit are still recorded so we have
    # a complete trail of what happened during the window.
    lock = load_maintenance_lock(maintenance_lock_path or MAINTENANCE_LOCK)
    suppressed: list[Transition] = []
    alertable: list[Transition] = transitions
    if lock is not None:
        suppressed = [t for t in transitions if lock.covers(t.name)]
        alertable = [t for t in transitions if not lock.covers(t.name)]
        if suppressed:
            log.info("maintenance mode active (reason=%r); suppressing %d/%d alerts",
                     lock.reason, len(suppressed), len(transitions))

    if not dry_run:
        store.save()
        audit = AuditLog(str(audit_path))
        for t in transitions:
            audit.append({
                "principal": PRINCIPAL, "event": "health_transition",
                "alert_suppressed": lock is not None and lock.covers(t.name),
                **t.as_dict(),
            })
        audit.append({
            "principal": PRINCIPAL, "event": "health_run",
            "checks": len(results),
            "transitions": len(transitions),
            "transitions_suppressed": len(suppressed),
            "unhealthy_now": sum(1 for r in results if r.status is Status.UNHEALTHY),
            "maintenance_active": lock is not None,
            "duration_s": round(time.monotonic() - started, 2),
        })
        if discord_webhook and alertable and notify:
            notify(discord_webhook, format_discord(alertable))

    summary = {
        "checks": len(results),
        "healthy": sum(1 for r in results if r.status is Status.HEALTHY),
        "unhealthy": sum(1 for r in results if r.status is Status.UNHEALTHY),
        "unknown": sum(1 for r in results if r.status is Status.UNKNOWN),
        "transitions": len(transitions),
        "transitions_suppressed": len(suppressed),
        "maintenance_active": lock is not None,
        "duration_s": round(time.monotonic() - started, 2),
    }
    return summary


def _cli() -> int:
    p = argparse.ArgumentParser(description="Run a health-monitor pass.")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE)
    p.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT)
    p.add_argument("--discord-webhook", default=DEFAULT_DISCORD)
    p.add_argument("--dry-run", action="store_true",
                   help="don't write state or audit; useful for testing checks")
    p.add_argument("--show-results", action="store_true",
                   help="print every check result, not just transitions")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.show_results:
        for r in collect_all():
            print(f"  {r.status.value:9s} {r.name:48s} {r.detail[:80]}")
        return 0
    summary = run(
        state_path=args.state, audit_path=args.audit_log,
        discord_webhook=args.discord_webhook, dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
