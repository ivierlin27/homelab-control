"""CLI for per-agent identity issuance.

Examples:

    python3 -m apps._shared.identity issue   --principal agent:homelab-maintainer
    python3 -m apps._shared.identity status  --principal agent:homelab-maintainer
    python3 -m apps._shared.identity status  --all
    python3 -m apps._shared.identity verify  --principal agent:homelab-maintainer
    python3 -m apps._shared.identity confirm --principal agent:homelab-maintainer \\
        --component discord_bot
    python3 -m apps._shared.identity revoke  --principal agent:homelab-maintainer \\
        --component discord_bot
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apps._shared.registry import RegistryError, load_registry

from .issuer import (
    IssuerError,
    issue_principal,
    revoke_component,
    verify_principal,
)
from .state import (
    ALL_COMPONENTS,
    Component,
    ComponentStatus,
    IdentityState,
    StateStore,
    default_state_dir,
)

_STATUS_GLYPH = {
    ComponentStatus.NOT_REQUIRED: "—",
    ComponentStatus.PENDING: "·",
    ComponentStatus.ISSUED: "✓",
    ComponentStatus.OPERATOR_TODO: "!",
    ComponentStatus.REVOKED: "✗",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apps._shared.identity")
    parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="state directory (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    issue = sub.add_parser("issue", help="run all eligible issuance steps for a principal")
    issue.add_argument("--principal", required=True)
    issue.add_argument(
        "--ssh-dir",
        default=None,
        help="override ~/.ssh/homelab-agents output directory",
    )

    status = sub.add_parser("status", help="show issuance state")
    g = status.add_mutually_exclusive_group(required=True)
    g.add_argument("--principal")
    g.add_argument("--all", action="store_true")

    verify = sub.add_parser(
        "verify",
        help="locally verify what we can (SSH file perms, Containerfile present)",
    )
    verify.add_argument("--principal", required=True)

    confirm = sub.add_parser(
        "confirm",
        help="mark a component as ISSUED after the operator has finished its checklist",
    )
    confirm.add_argument("--principal", required=True)
    confirm.add_argument("--component", required=True, choices=[c.value for c in ALL_COMPONENTS])
    confirm.add_argument(
        "--note",
        default=None,
        help="optional note to record alongside the confirmation",
    )

    revoke = sub.add_parser("revoke", help="mark a component as revoked")
    revoke.add_argument("--principal", required=True)
    revoke.add_argument("--component", required=True, choices=[c.value for c in ALL_COMPONENTS])
    revoke.add_argument(
        "--delete-local",
        action="store_true",
        help="for ssh_key only: also delete the local key files",
    )

    return parser


def _cmd_issue(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.state_dir))
    try:
        registry = load_registry()
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    try:
        report = issue_principal(
            args.principal,
            registry=registry,
            state_store=store,
            ssh_dir=Path(args.ssh_dir) if args.ssh_dir else None,
        )
    except (IssuerError, RegistryError) as exc:
        print(f"issue error: {exc}", file=sys.stderr)
        return 2

    print(f"principal: {report.principal}")
    print(f"state:     {report.state_path}")
    print()
    _print_status_table(report.principal, store)
    if report.needs_operator_action():
        print()
        print("Operator action required for components marked '!'. Run:")
        print(f"  python3 -m apps._shared.identity status --principal {report.principal}")
        print("to see the per-component checklist.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.state_dir))
    if args.all:
        principals = store.list_principals()
        if not principals:
            print(f"no state files in {store.root}")
            return 0
        for principal in principals:
            print(f"=== {principal} ===")
            _print_status_table(principal, store, with_steps=False)
            print()
        return 0
    _print_status_table(args.principal, store, with_steps=True)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.state_dir))
    try:
        results = verify_principal(args.principal, state_store=store)
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    width = max(len(c.value) for c in ALL_COMPONENTS)
    for comp in ALL_COMPONENTS:
        status, note = results[comp]
        glyph = _STATUS_GLYPH[status]
        print(f"{glyph} {comp.value.ljust(width)}  {status.value:14s}  {note}")
    return 0


def _cmd_confirm(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.state_dir))
    state = store.load(args.principal)
    component = Component(args.component)
    details = state.get_details(component)
    if args.note:
        details["operator_note"] = args.note
    state.set_component(
        component,
        status=ComponentStatus.ISSUED,
        details=details,
        next_steps=[],
    )
    path = store.save(state)
    print(f"confirmed: {args.principal} {component.value} -> ISSUED ({path})")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.state_dir))
    component = Component(args.component)
    revoke_component(
        args.principal,
        component,
        state_store=store,
        delete_local_artifacts=args.delete_local,
    )
    print(f"revoked: {args.principal} {component.value}")
    return 0


def _print_status_table(
    principal: str,
    store: StateStore,
    *,
    with_steps: bool = True,
) -> None:
    state = store.load(principal)
    width = max(len(c.value) for c in ALL_COMPONENTS)
    for comp in ALL_COMPONENTS:
        status = state.status(comp)
        glyph = _STATUS_GLYPH[status]
        details = state.get_details(comp)
        # one-line detail summary
        summary = ""
        if comp == Component.SSH_KEY and "fingerprint" in details:
            summary = details["fingerprint"]
        elif comp == Component.SANDBOX_IMAGE and "image_tag" in details:
            summary = details["image_tag"]
        print(f"{glyph} {comp.value.ljust(width)}  {status.value:14s}  {summary}")
        if with_steps:
            for step in state.get_next_steps(comp):
                print(f"     {step}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "issue":
        return _cmd_issue(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "verify":
        return _cmd_verify(args)
    if args.cmd == "confirm":
        return _cmd_confirm(args)
    if args.cmd == "revoke":
        return _cmd_revoke(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
