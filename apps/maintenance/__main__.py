"""CLI: ``python -m apps.maintenance start|end|status``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from _shared.audit import AuditLog  # noqa: E402

from .lock import (  # noqa: E402
    DEFAULT_LOCK_PATH,
    end_maintenance,
    load_lock,
    start_maintenance,
)


PRINCIPAL = "agent:maintenance-mode"
DEFAULT_AUDIT = Path.home() / ".local/state/homelab-control/health-monitor/audit.jsonl"


def _audit(event: str, extra: dict, audit_path: Path) -> None:
    AuditLog(str(audit_path)).append({"principal": PRINCIPAL, "event": event, **extra})


def _cmd_start(args: argparse.Namespace) -> int:
    scope = [s.strip() for s in (args.scope or "").split(",") if s.strip()]
    lock = start_maintenance(
        duration_hours=args.hours,
        reason=args.reason,
        scope=scope or None,
        path=args.lock,
    )
    _audit("maintenance_start", lock.as_dict(), args.audit_log)
    print(f"maintenance window started by {lock.started_by}")
    print(f"  until : {lock.as_dict()['until_iso']} ({lock.remaining()} remaining)")
    print(f"  reason: {lock.reason}")
    print(f"  scope : {lock.scope or '[global]'}")
    print(f"  alerts suppressed for any check whose name matches the scope")
    return 0


def _cmd_end(args: argparse.Namespace) -> int:
    lock = end_maintenance(path=args.lock)
    if lock is None:
        print("no active maintenance window; nothing to end")
        return 0
    _audit("maintenance_end", lock.as_dict(), args.audit_log)
    print(f"maintenance window ended (was: {lock.reason!r}, scope={lock.scope or '[global]'})")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    lock = load_lock(args.lock)
    if lock is None:
        print("maintenance mode: NOT ACTIVE")
        return 0
    print("maintenance mode: ACTIVE")
    print(f"  started_at: {lock.as_dict()['started_at_iso']}")
    print(f"  until     : {lock.as_dict()['until_iso']}  (remaining: {lock.remaining()})")
    print(f"  started_by: {lock.started_by}")
    print(f"  reason    : {lock.reason}")
    print(f"  scope     : {lock.scope or '[global]'}")
    if args.json:
        print(json.dumps(lock.as_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m apps.maintenance",
                                description="Manage the homelab maintenance-mode lock.")
    p.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH,
                   help="path to the lock file (default: %(default)s)")
    p.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT,
                   help="audit ledger for start/end events")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="enter maintenance mode")
    s.add_argument("--hours", type=float, required=True, help="window length (1-168h)")
    s.add_argument("--reason", required=True, help="why; required for the audit log")
    s.add_argument("--scope", default="",
                   help="comma-separated prefixes (e.g. 'health:memory-engine,timer:alienware-backup'); "
                        "empty = global. Match is by check name prefix.")
    s.set_defaults(func=_cmd_start)

    e = sub.add_parser("end", help="end maintenance mode early (removes lock)")
    e.set_defaults(func=_cmd_end)

    st = sub.add_parser("status", help="show current maintenance lock")
    st.add_argument("--json", action="store_true", help="also print the raw lock JSON")
    st.set_defaults(func=_cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
