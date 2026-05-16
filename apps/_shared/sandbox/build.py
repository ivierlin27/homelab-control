"""Build per-agent sandbox images.

Reads the registry to know which agents need an image, then runs
`podman build` for each. The base image is built first; per-agent images
inherit from it.

CLI:

    python3 -m apps._shared.sandbox build --all
    python3 -m apps._shared.sandbox build --principal agent:homelab-maintainer
    python3 -m apps._shared.sandbox build --print-only --principal agent:executive
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from apps._shared.registry import load_registry

THIS_DIR = Path(__file__).resolve().parent
IMAGES_DIR = THIS_DIR / "images"
BASE_IMAGE_TAG = "agent-base:latest"
BASE_CONTAINERFILE = IMAGES_DIR / "_base.Containerfile"


class BuildError(Exception):
    """Raised when a podman build command fails."""


def build_base(*, podman: str = "podman", print_only: bool = False) -> None:
    cmd = [
        podman,
        "build",
        "--build-arg",
        f"AGENT_UID={os.getuid()}",
        "--build-arg",
        f"AGENT_GID={os.getgid()}",
        "-t",
        BASE_IMAGE_TAG,
        "-f",
        str(BASE_CONTAINERFILE),
        str(IMAGES_DIR),
    ]
    _run(cmd, print_only=print_only)


def build_agent(principal: str, *, podman: str = "podman", print_only: bool = False) -> None:
    registry = load_registry()
    manifest = registry.get(principal)
    image_name = manifest.get("sandbox", "base_image") or principal.replace("agent:", "agent-")
    tag = f"{image_name}:latest"
    containerfile = IMAGES_DIR / f"{image_name}.Containerfile"
    if not containerfile.is_file():
        raise BuildError(
            f"no Containerfile for {principal}: expected {containerfile.relative_to(THIS_DIR.parent.parent.parent)}"
        )
    cmd = [
        podman,
        "build",
        "-t",
        tag,
        "-f",
        str(containerfile),
        str(IMAGES_DIR),
    ]
    _run(cmd, print_only=print_only)


def build_all(*, podman: str = "podman", print_only: bool = False) -> None:
    build_base(podman=podman, print_only=print_only)
    registry = load_registry()
    for principal in registry.list_principals():
        build_agent(principal, podman=podman, print_only=print_only)


def _run(cmd: list[str], *, print_only: bool) -> None:
    quoted = " ".join(_shell_quote(part) for part in cmd)
    print(f"$ {quoted}", flush=True)
    if print_only:
        return
    if not shutil.which(cmd[0]):
        raise BuildError(f"binary not on PATH: {cmd[0]}")
    proc = subprocess.run(cmd, check=False)  # noqa: S603 - controlled argv
    if proc.returncode != 0:
        raise BuildError(f"command failed (exit {proc.returncode}): {quoted}")


def _shell_quote(part: str) -> str:
    if not part or any(c in part for c in ' "\'\\$`()|&;<>'):
        return "'" + part.replace("'", "'\"'\"'") + "'"
    return part


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apps._shared.sandbox.build")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="build base + every agent image")
    g.add_argument("--principal", help="build one agent image (and base first)")
    g.add_argument("--base-only", action="store_true", help="build only the base image")
    parser.add_argument("--podman", default="podman", help="podman binary (default: %(default)s)")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="print the podman commands without executing them",
    )
    args = parser.parse_args(argv)

    try:
        if args.all:
            build_all(podman=args.podman, print_only=args.print_only)
        elif args.base_only:
            build_base(podman=args.podman, print_only=args.print_only)
        else:
            build_base(podman=args.podman, print_only=args.print_only)
            build_agent(args.principal, podman=args.podman, print_only=args.print_only)
    except BuildError as exc:
        print(f"build error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
