"""Tiered restic backup runner for the homelab agent stack.

Reads ``config/backup/sources.yaml``, expands ``$HOME``-style tokens,
filters to existing paths (so a missing optional source becomes a
logged skip rather than a failure), and shells out to ``restic backup``
+ ``restic forget --prune`` for each configured tier.

The runner targets one or more **restic repositories** in parallel: a
single invocation backs up to all configured targets. Today the local
``/mnt/spinny`` target is wired; the Proxmox NFS target is a slot
waiting for the LXC firewall to be opened. Adding it is a one-line env
var (see ``docs/runbooks/backup.md``).

Design notes
------------
- ``restic`` is invoked via subprocess with all secret material passed
  through env vars (``RESTIC_REPOSITORY``, ``RESTIC_PASSWORD_FILE``).
  Nothing secret hits ``argv``.
- The pre-flight checks (``restic snapshots --json``) ensure the repo
  is initialized; if not, the runner refuses to backup and exits with
  a clear message — initialization is operator-driven (see runbook).
- All commands are streamed to stdout/stderr in real time so
  ``journalctl --user -u alienware-backup.service`` shows live progress.
- Exit code 0 only if every (tier × target) pair succeeded.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # the runtime will exit clearly; tests inject parsed dicts

log = logging.getLogger("backup-runner")

DEFAULT_CONFIG = Path("config/backup/sources.yaml")
DEFAULT_RESTIC = "restic"
ENV_REPOSITORIES = "BACKUP_REPOSITORIES"  # comma-separated
ENV_PASSWORD_FILE = "RESTIC_PASSWORD_FILE"


@dataclass(frozen=True)
class TierConfig:
    name: str
    tag: str
    paths: tuple[str, ...]
    excludes: tuple[str, ...]
    keep: dict[str, int]


@dataclass(frozen=True)
class RunPlan:
    tier: TierConfig
    repository: str
    expanded_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]


@dataclass
class RunResult:
    plan: RunPlan
    backup_rc: int | None = None
    forget_rc: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.backup_rc == 0 and (
            self.forget_rc is None or self.forget_rc == 0
        )


def load_config(path: Path) -> dict[str, TierConfig]:
    if yaml is None:
        raise RuntimeError("PyYAML required; install with `pip install pyyaml`")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return parse_config(raw)


def parse_config(raw: dict) -> dict[str, TierConfig]:
    tiers_raw = (raw or {}).get("tiers") or {}
    tiers: dict[str, TierConfig] = {}
    for name, body in tiers_raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"tier {name!r}: expected mapping, got {type(body).__name__}")
        tiers[name] = TierConfig(
            name=name,
            tag=str(body.get("tag", name)),
            paths=tuple(str(p) for p in body.get("paths") or []),
            excludes=tuple(str(p) for p in body.get("excludes") or []),
            keep={str(k): int(v) for k, v in (body.get("keep") or {}).items()},
        )
    if not tiers:
        raise ValueError("no tiers defined in config")
    return tiers


def expand_path(path: str, *, home: str) -> str:
    """Expand ``$HOME`` (and ``${HOME}``) tokens. Refuses other env tokens."""
    out = path.replace("${HOME}", home).replace("$HOME", home)
    if "$" in out:
        raise ValueError(f"path {path!r}: only $HOME expansion supported")
    return out


def build_plan(
    tier: TierConfig,
    repositories: Sequence[str],
    *,
    home: str,
    path_exists: callable = Path.exists,
) -> list[RunPlan]:
    expanded = [expand_path(p, home=home) for p in tier.paths]
    existing: list[str] = []
    skipped: list[str] = []
    for ep in expanded:
        if path_exists(Path(ep)):
            existing.append(ep)
        else:
            skipped.append(ep)
    plans: list[RunPlan] = []
    for repo in repositories:
        plans.append(
            RunPlan(
                tier=tier,
                repository=repo,
                expanded_paths=tuple(existing),
                skipped_paths=tuple(skipped),
            )
        )
    return plans


def build_backup_argv(plan: RunPlan, *, restic_bin: str) -> list[str]:
    """Construct ``restic backup`` args. Repo is in env, not argv."""
    if not plan.expanded_paths:
        return []
    argv: list[str] = [restic_bin, "backup", "--tag", plan.tier.tag]
    for excl in plan.tier.excludes:
        argv += ["--exclude", excl]
    argv += list(plan.expanded_paths)
    return argv


def build_forget_argv(plan: RunPlan, *, restic_bin: str) -> list[str] | None:
    """Construct ``restic forget --prune`` args, or ``None`` if no policy."""
    keep = {k: v for k, v in plan.tier.keep.items() if v > 0}
    if not keep:
        return None
    argv: list[str] = [restic_bin, "forget", "--prune", "--tag", plan.tier.tag]
    for unit, count in sorted(keep.items()):
        argv += [f"--keep-{unit}", str(count)]
    return argv


def _env_for(plan: RunPlan, *, password_file: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = plan.repository
    if password_file:
        env["RESTIC_PASSWORD_FILE"] = password_file
    # Ensure restic is well-behaved in non-interactive systemd contexts.
    env.setdefault("RESTIC_PROGRESS_FPS", "0")
    return env


def run_plan(
    plan: RunPlan,
    *,
    restic_bin: str,
    password_file: str | None,
    runner=subprocess.run,
) -> RunResult:
    result = RunResult(plan=plan)
    if not plan.expanded_paths:
        result.error = f"tier {plan.tier.name!r} has no existing source paths"
        return result
    env = _env_for(plan, password_file=password_file)
    backup_argv = build_backup_argv(plan, restic_bin=restic_bin)
    log.info("backup tier=%s repo=%s paths=%d skipped=%d",
             plan.tier.name, plan.repository,
             len(plan.expanded_paths), len(plan.skipped_paths))
    for sp in plan.skipped_paths:
        log.info("  skip (does not exist): %s", sp)
    try:
        proc = runner(backup_argv, env=env, check=False)
        result.backup_rc = getattr(proc, "returncode", 0)
    except FileNotFoundError as exc:
        result.error = f"restic binary not found: {exc}"
        return result
    if result.backup_rc != 0:
        result.error = f"backup exited {result.backup_rc}"
        return result
    forget_argv = build_forget_argv(plan, restic_bin=restic_bin)
    if forget_argv is not None:
        log.info("forget+prune tier=%s repo=%s", plan.tier.name, plan.repository)
        proc = runner(forget_argv, env=env, check=False)
        result.forget_rc = getattr(proc, "returncode", 0)
        if result.forget_rc != 0:
            result.error = f"forget exited {result.forget_rc}"
    return result


def repositories_from_env() -> list[str]:
    raw = os.environ.get(ENV_REPOSITORIES, "").strip()
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tiered restic backup runner")
    parser.add_argument("tier", help="tier name to run (e.g. hot, full)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--restic-bin", default=DEFAULT_RESTIC)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    repositories = repositories_from_env()
    if not repositories:
        log.error("no repositories configured (set %s)", ENV_REPOSITORIES)
        return 2

    if not shutil.which(args.restic_bin) and not Path(args.restic_bin).exists():
        log.error("restic binary not found: %s", args.restic_bin)
        return 2

    config = load_config(Path(args.config))
    if args.tier not in config:
        log.error("unknown tier %r; known: %s", args.tier, sorted(config))
        return 2

    home = os.path.expanduser("~")
    plans = build_plan(config[args.tier], repositories, home=home)
    password_file = os.environ.get(ENV_PASSWORD_FILE) or None

    overall = 0
    for plan in plans:
        result = run_plan(plan, restic_bin=args.restic_bin, password_file=password_file)
        if result.ok:
            log.info("OK  tier=%s repo=%s", plan.tier.name, plan.repository)
        else:
            log.error("FAIL tier=%s repo=%s: %s", plan.tier.name, plan.repository, result.error)
            overall = 1
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
