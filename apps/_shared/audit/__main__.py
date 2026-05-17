"""CLI for the audit ledger.

    python3 -m apps._shared.audit verify  /path/to/trust-ledger.jsonl
    python3 -m apps._shared.audit verify  --glob '~/.local/state/homelab-control/**/trust-ledger.jsonl'
    python3 -m apps._shared.audit info    /path/to/trust-ledger.jsonl
    python3 -m apps._shared.audit tail    /path/to/trust-ledger.jsonl --lines 5
    python3 -m apps._shared.audit anchor  /path/to/trust-ledger.jsonl --to anchor.jsonl --note "daily 2026-05-17"
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import sys
from pathlib import Path

from .ledger import AuditLog, AuditLogError


def _expand(targets: list[str], glob_mode: bool) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        if glob_mode:
            for m in sorted(_glob.glob(Path(t).expanduser().as_posix(), recursive=True)):
                out.append(Path(m))
        else:
            out.append(Path(t).expanduser())
    if not out:
        print("no files matched", file=sys.stderr)
    return out


def _cmd_verify(args: argparse.Namespace) -> int:
    paths = _expand(args.paths, args.glob)
    if not paths:
        return 2
    failures = 0
    for p in paths:
        log = AuditLog(p)
        report = log.verify_chain()
        print(report.summary())
        if not report.ok:
            failures += 1
    return 1 if failures else 0


def _cmd_info(args: argparse.Namespace) -> int:
    paths = _expand(args.paths, args.glob)
    if not paths:
        return 2
    for p in paths:
        log = AuditLog(p)
        report = log.verify_chain()
        print(f"path:                {p}")
        print(f"total_lines:         {report.total_lines}")
        print(f"legacy_prefix_lines: {report.legacy_prefix_lines}")
        print(f"chained_lines:       {report.chained_lines}")
        print(f"head_hash:           {report.head_hash}")
        print(f"chain_ok:            {report.ok}")
        if not report.ok:
            print(f"break_at_line:       {report.first_break_line}")
            print(f"error:               {report.error}")
        print()
    return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    p = Path(args.path).expanduser()
    log = AuditLog(p)
    records = list(log.iter_records())
    for item in records[-args.lines:]:
        if hasattr(item, "raw_line"):
            print(item.raw_line)
        else:
            print(json.dumps(item, sort_keys=True))
    return 0


def _cmd_anchor(args: argparse.Namespace) -> int:
    p = Path(args.path).expanduser()
    log = AuditLog(p)
    try:
        record = log.anchor(Path(args.to).expanduser(), note=args.note or "")
    except AuditLogError as exc:
        print(f"anchor error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(record, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apps._shared.audit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    verify = sub.add_parser("verify", help="walk the chain, exit 1 on tamper")
    verify.add_argument("paths", nargs="+")
    verify.add_argument("--glob", action="store_true", help="treat paths as shell globs")

    info = sub.add_parser("info", help="print line counts + chain head")
    info.add_argument("paths", nargs="+")
    info.add_argument("--glob", action="store_true")

    tail = sub.add_parser("tail", help="print the last N lines")
    tail.add_argument("path")
    tail.add_argument("--lines", "-n", type=int, default=10)

    anchor = sub.add_parser("anchor", help="append current chain head to an anchor file")
    anchor.add_argument("path", help="audit log to anchor")
    anchor.add_argument("--to", required=True, help="anchor file to append to")
    anchor.add_argument("--note", default="", help="free-form note")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "verify":
            return _cmd_verify(args)
        if args.cmd == "info":
            return _cmd_info(args)
        if args.cmd == "tail":
            return _cmd_tail(args)
        if args.cmd == "anchor":
            return _cmd_anchor(args)
    except AuditLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
