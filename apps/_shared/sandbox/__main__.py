"""CLI entrypoint for the sandbox package.

Subcommands:

    build       build sandbox images (base + per-agent)
    run         run a one-shot command in an agent's sandbox
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from apps._shared.registry import load_registry

from . import build as build_mod
from .runner import BranchStrategy, SandboxError, SandboxRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apps._shared.sandbox")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build_p = sub.add_parser("build", help="build sandbox images")
    g = build_p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--principal")
    g.add_argument("--base-only", action="store_true")
    build_p.add_argument("--podman", default="podman")
    build_p.add_argument("--print-only", action="store_true")

    run_p = sub.add_parser("run", help="run a command in an agent sandbox")
    run_p.add_argument("--principal", required=True)
    run_p.add_argument(
        "--worktree",
        required=True,
        help="host path to mount as /work inside the sandbox",
    )
    run_p.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="wall-clock timeout in seconds (default: %(default)s)",
    )
    run_p.add_argument(
        "--capture-session",
        default=None,
        help="path to append a session JSONL record to",
    )
    run_p.add_argument(
        "--",
        dest="dashes",
        action="store_true",
        help="separator before the command",
    )
    run_p.add_argument("command", nargs=argparse.REMAINDER, help="command argv to run")
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        if args.all:
            build_mod.build_all(podman=args.podman, print_only=args.print_only)
        elif args.base_only:
            build_mod.build_base(podman=args.podman, print_only=args.print_only)
        else:
            build_mod.build_base(podman=args.podman, print_only=args.print_only)
            build_mod.build_agent(
                args.principal,
                podman=args.podman,
                print_only=args.print_only,
            )
    except build_mod.BuildError as exc:
        print(f"build error: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("error: command required after --", file=sys.stderr)
        return 64
    registry = load_registry()
    try:
        manifest = registry.get(args.principal)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    image_name = manifest.get("sandbox", "base_image") or args.principal.replace("agent:", "agent-")
    image_tag = f"{image_name}:latest"
    allowed_hosts = manifest.get("sandbox", "network", "allowed_hosts", default=[]) or []

    runner = SandboxRunner(
        principal=args.principal,
        image=image_tag,
        worktree_path=Path(args.worktree),
        allowed_hosts=tuple(allowed_hosts),
    )
    try:
        result = runner.run(
            command=tuple(args.command),
            timeout_seconds=args.timeout,
            capture_session_to=Path(args.capture_session) if args.capture_session else None,
        )
    except SandboxError as exc:
        print(f"sandbox error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    print(
        f"\n[sandbox] principal={args.principal} image={image_tag} "
        f"network={result.network_mode} egress={list(result.egress_allowed)} "
        f"exit={result.exit_code} dur={result.duration_seconds:.2f}s "
        f"correlation_id={result.correlation_id}",
        file=sys.stderr,
    )
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "build":
        return _cmd_build(args)
    if args.cmd == "run":
        return _cmd_run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
