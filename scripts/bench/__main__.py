"""CLI entrypoint for the homelab benchmark harness.

Usage:
    python -m scripts.bench micro
    python -m scripts.bench serve-sweep
    python -m scripts.bench ruler
    python -m scripts.bench bfcl
    python -m scripts.bench code
    python -m scripts.bench soak
    python -m scripts.bench aggregate <root> [<root2> ...]

All runners read configuration from environment variables; see the
docstrings in each runner module.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "micro":
        from .runners import micro

        return micro.main()
    if cmd in {"serve-sweep", "serve_sweep"}:
        from .runners import serve_sweep

        return serve_sweep.main()
    if cmd == "ruler":
        from .runners import ruler

        return ruler.main()
    if cmd == "bfcl":
        from .runners import bfcl

        return bfcl.main()
    if cmd == "code":
        from .runners import code

        return code.main()
    if cmd == "soak":
        from .runners import soak

        return soak.main()
    if cmd == "aggregate":
        from .runners import aggregate

        return aggregate.main(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
