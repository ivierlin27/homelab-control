"""CLI for the capability registry.

Examples:

    python3 -m apps._shared.registry validate
    python3 -m apps._shared.registry list
    python3 -m apps._shared.registry show agent:homelab-maintainer
    python3 -m apps._shared.registry show agent:executive --field discord
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .loader import DEFAULT_REGISTRY_PATH, RegistryError, load_registry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apps._shared.registry")
    parser.add_argument(
        "--registry",
        default=str(DEFAULT_REGISTRY_PATH),
        help="path to registry.yaml (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="validate the registry; exit non-zero on any error")
    sub.add_parser("list", help="list registered principals")

    show = sub.add_parser("show", help="dump a manifest")
    show.add_argument("principal")
    show.add_argument(
        "--field",
        help="dot-path into the manifest (e.g. discord.channels)",
    )
    show.add_argument(
        "--format",
        choices=("yaml", "json"),
        default="yaml",
        help="output format (default: yaml)",
    )
    return parser


def _resolve_field(data: object, dotted: str) -> object:
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            raise SystemExit(f"field path {dotted!r} hit a non-mapping at {part!r}")
        if part not in cur:
            raise SystemExit(f"field path {dotted!r} missing key {part!r}")
        cur = cur[part]
    return cur


def _emit(value: object, fmt: str) -> None:
    if fmt == "json":
        json.dump(value, sys.stdout, indent=2, default=str, sort_keys=True)
        sys.stdout.write("\n")
    else:
        yaml.safe_dump(value, sys.stdout, sort_keys=False, default_flow_style=False)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry_path = Path(args.registry)

    try:
        registry = load_registry(registry_path)
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "validate":
        print(f"ok: {len(registry.agents)} agent(s) loaded from {registry_path}")
        return 0

    if args.cmd == "list":
        for principal in registry.list_principals():
            manifest = registry.agents[principal]
            print(f"{principal}\t{manifest.domain}\t{manifest.display_name}")
        return 0

    if args.cmd == "show":
        try:
            manifest = registry.get(args.principal)
        except RegistryError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        value: object = manifest.data
        if args.field:
            value = _resolve_field(value, args.field)
        _emit(value, args.format)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
