"""Small CLI for running RLM orchestrations from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import AuditLog
from .harness import Budget, Harness, ScriptedRoot, GatewayRoot
from .sandbox import Sandbox
from .subcall import SubCallInvoker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--root-prompt", required=True)
    run.add_argument("--audit-path", required=True)
    run.add_argument("--scripted-probes", help="Path to JSON file with a list of probe directives")
    run.add_argument("--gateway-root", action="store_true", help="Use the live gateway as the root planner")
    run.add_argument("--handle", action="append", default=[], help="handle_id=path[:kind] (kind defaults to lines)")
    run.add_argument("--budget-root-tokens", type=int, default=4096)
    run.add_argument("--budget-subcalls", type=int, default=12)
    run.add_argument("--budget-total-tokens", type=int, default=200_000)
    run.add_argument("--budget-wall-seconds", type=int, default=600)
    run.add_argument("--max-steps", type=int, default=24)

    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    parser.error("unknown command")
    return 2


def _run(args: argparse.Namespace) -> int:
    sandbox = Sandbox()
    for entry in args.handle:
        if "=" not in entry:
            raise SystemExit(f"--handle expects handle_id=path[:kind], got {entry}")
        handle_id, _, location = entry.partition("=")
        path_part, _, kind_part = location.partition(":")
        kind = kind_part or "lines"
        sandbox.add_from_path(handle_id, Path(path_part).expanduser(), kind=kind)

    audit = AuditLog(Path(args.audit_path))
    invoker = SubCallInvoker()
    budget = Budget(
        max_root_tokens=args.budget_root_tokens,
        max_subcalls=args.budget_subcalls,
        max_total_tokens=args.budget_total_tokens,
        max_wall_seconds=args.budget_wall_seconds,
    )
    harness = Harness(sandbox=sandbox, invoker=invoker, audit=audit, budget=budget, max_steps=args.max_steps)

    if args.scripted_probes:
        probes = json.loads(Path(args.scripted_probes).expanduser().read_text())
        root = ScriptedRoot(probes)
    else:
        if not args.gateway_root:
            raise SystemExit("either --scripted-probes or --gateway-root is required")
        root = GatewayRoot(invoker=invoker)

    result = harness.run(root=root, root_prompt=args.root_prompt)
    payload = {
        "orchestration_id": result.orchestration_id,
        "totals": result.totals,
        "aborted_reason": result.aborted_reason,
        "aborted_step": result.aborted_step,
        "audit_path": str(result.audit_path),
        "final": result.final.as_dict() if result.final else None,
        "notes": result.notes,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
