"""Generate the gateway's skill-policy snapshot.

Walks ``config/skills/`` via the canonical skills loader and writes a
flat JSON map keyed by skill id to a target path. Designed to run as
``ExecStartPre`` for the LiteLLM gateway systemd unit so the gateway
always sees a fresh policy snapshot when it starts.

Usage::

    python3 -m apps._shared.litellm_callbacks.generate_skill_policy \\
        --output /home/kenns/.local/state/homelab-control/llm-calls/skill-policy.json

The snapshot schema is documented in ``local_only_policy.load_snapshot``.

The script is deliberately small (no argparse subcommands, no fanciness)
because it runs in the systemd startup path and any error here delays the
gateway coming up. On loader error, the script writes a snapshot with an
empty ``skills`` map and exits 0 — the gateway's per-call ``check_call``
treats unknown skills as fail-open, which is preferable to wedging the
gateway. The error is still surfaced via stderr for the journal.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from apps._shared.skills import default_skills_dir, load_skill_registry

SCHEMA_VERSION = 1


def build_snapshot_dict(skills_dir: Path | None = None) -> dict:
    skills_dir = skills_dir or default_skills_dir()
    out: dict[str, dict] = {}
    try:
        reg = load_skill_registry(skills_dir)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[skill-policy] WARN: skill loader failed: {exc}; "
            f"writing empty snapshot",
            file=sys.stderr,
            flush=True,
        )
        return {
            "schema": SCHEMA_VERSION,
            "generated_at_epoch": int(time.time()),
            "skills_dir": str(skills_dir),
            "skills": {},
            "load_error": str(exc),
        }
    for skill_id in reg.ids():
        skill = reg.get(skill_id)
        out[skill_id] = {
            "local_only": bool(skill.local_only),
            "version": int(skill.version),
        }
    return {
        "schema": SCHEMA_VERSION,
        "generated_at_epoch": int(time.time()),
        "skills_dir": str(skills_dir),
        "skills": out,
    }


def write_snapshot(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generate_skill_policy")
    parser.add_argument(
        "--output",
        required=True,
        help="path to write the JSON snapshot",
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="override config/skills/ root (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    skills_dir = Path(args.skills_dir).expanduser() if args.skills_dir else None
    payload = build_snapshot_dict(skills_dir)
    write_snapshot(Path(args.output).expanduser(), payload)
    counted = len(payload["skills"])
    local_only = sum(1 for s in payload["skills"].values() if s["local_only"])
    print(
        f"[skill-policy] wrote {args.output}: "
        f"{counted} skills, {local_only} marked local_only",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
