"""CLI for the skill registry.

    python3 -m apps._shared.skills list
    python3 -m apps._shared.skills show <skill-id>
    python3 -m apps._shared.skills validate
    python3 -m apps._shared.skills load --principal agent:executive
    python3 -m apps._shared.skills load --principal agent:executive --route cloud-frontier
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apps._shared.registry import RegistryError, load_registry

from .loader import (
    SkillError,
    default_skills_dir,
    load_skill_registry,
    skills_for_agent,
)


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        registry = load_skill_registry(Path(args.skills_dir) if args.skills_dir else None)
    except SkillError as exc:
        print(f"skill error: {exc}", file=sys.stderr)
        return 2
    width = max((len(s.id) for s in registry.skills.values()), default=4)
    for sid in registry.ids():
        skill = registry.get(sid)
        local = "local" if skill.local_only else "any"
        print(f"{sid.ljust(width)}  {local:5s}  {skill.description}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        registry = load_skill_registry(Path(args.skills_dir) if args.skills_dir else None)
        skill = registry.get(args.id)
    except SkillError as exc:
        print(f"skill error: {exc}", file=sys.stderr)
        return 2
    print(f"id:                    {skill.id}")
    print(f"name:                  {skill.name}")
    print(f"description:           {skill.description}")
    print(f"local_only:            {skill.local_only}")
    print(f"required_tools:        {list(skill.required_tools)}")
    print(f"required_task_classes: {list(skill.required_task_classes)}")
    print(f"version:               {skill.version}")
    print(f"source_path:           {skill.source_path}")
    if args.body:
        print()
        print("--- body ---")
        print(skill.body)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        skill_registry = load_skill_registry(Path(args.skills_dir) if args.skills_dir else None)
    except SkillError as exc:
        print(f"skill error: {exc}", file=sys.stderr)
        return 2
    try:
        agent_registry = load_registry()
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    for principal in agent_registry.list_principals():
        manifest = agent_registry.get(principal)
        try:
            skills_for_agent(manifest, registry=skill_registry)
        except SkillError as exc:
            errors.append(str(exc))

    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    print(
        f"ok: {len(skill_registry.skills)} skills, "
        f"validated against {len(agent_registry.list_principals())} agents"
    )
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    try:
        agent_registry = load_registry()
        skill_registry = load_skill_registry(Path(args.skills_dir) if args.skills_dir else None)
        manifest = agent_registry.get(args.principal)
        skills = skills_for_agent(manifest, registry=skill_registry, route=args.route)
    except (SkillError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not skills:
        print(f"(no skills loaded for {args.principal} on route={args.route})")
        return 0
    width = max(len(s.id) for s in skills)
    for skill in skills:
        local = "local" if skill.local_only else "any"
        print(f"{skill.id.ljust(width)}  {local:5s}  {skill.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apps._shared.skills")
    parser.add_argument(
        "--skills-dir",
        default=None,
        help=f"override skills root (default: {default_skills_dir()})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all skills on disk")

    show = sub.add_parser("show", help="show one skill's metadata")
    show.add_argument("id")
    show.add_argument("--body", action="store_true", help="also print the body")

    sub.add_parser(
        "validate",
        help="check every SKILL.md and that every manifest skill resolves and has its tools granted",
    )

    load_p = sub.add_parser("load", help="show the skill set for one agent (with optional route gate)")
    load_p.add_argument("--principal", required=True)
    load_p.add_argument("--route", default=None, help="e.g. local-fast, cloud-frontier")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        return _cmd_list(args)
    if args.cmd == "show":
        return _cmd_show(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    if args.cmd == "load":
        return _cmd_load(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
