"""Escalation policy: load + resolve per-task-class tier budgets.

Schema of ``config/escalation.yaml``::

    schema_version: 1

    default:
      tier1_budget_seconds: 300       # in-loop self-recovery wall-clock
      tier1_max_attempts: 3           # cap iterations regardless of budget
      tier2_budget_seconds: 1800      # executive reroute wall-clock
      tier3_dm_after_seconds: 14400   # if #approvals ping not ack'd in this
                                      # many seconds, DM the human

    by_task_class:
      homelab.deploy:
        tier1_budget_seconds: 120
        tier3_dm_after_seconds: 600
      knowledge.synthesize:
        tier1_budget_seconds: 1800
      finance.advise:
        skip_tier2: true              # advisory-only domains shouldn't reroute
        tier1_budget_seconds: 30
        tier3_dm_after_seconds: 300

Resolution order for a given (task_class, manifest):

  1. start from ``default``
  2. overlay ``by_task_class[<class>]`` if present
  3. overlay ``manifest.escalation_overrides`` (top-level fields)
  4. overlay ``manifest.escalation_overrides.by_task_class[<class>]`` if present

Each step is a partial-update (missing keys fall through to the previous
layer). The dispatcher consumes the final ``TierBudgets`` instance only.

This module has **no agent / queue / Discord dependencies**. It's pure
data + a YAML loader. The dispatcher in ``dispatcher.py`` is what runs
the actual loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

SCHEMA_VERSION = 1
DEFAULT_TASK_CLASS = "default"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "escalation.yaml"


class EscalationConfigError(Exception):
    """Raised on invalid escalation YAML or invalid override shape."""


@dataclass(frozen=True)
class TierBudgets:
    """All four numeric levers for one (task_class, principal) combo."""

    tier1_budget_seconds: int = 300
    tier1_max_attempts: int = 3
    tier2_budget_seconds: int = 1800
    tier3_dm_after_seconds: int = 14400
    skip_tier2: bool = False

    def with_overrides(self, overrides: Mapping[str, Any]) -> "TierBudgets":
        """Return a new instance with non-null overrides applied."""
        if not isinstance(overrides, Mapping):
            return self
        updates: dict[str, Any] = {}
        for key in (
            "tier1_budget_seconds",
            "tier1_max_attempts",
            "tier2_budget_seconds",
            "tier3_dm_after_seconds",
        ):
            value = overrides.get(key)
            if value is None:
                continue
            if not isinstance(value, int) or value < 0:
                raise EscalationConfigError(
                    f"{key} must be a non-negative int, got {value!r}"
                )
            updates[key] = value
        if "skip_tier2" in overrides:
            updates["skip_tier2"] = bool(overrides["skip_tier2"])
        return replace(self, **updates)


@dataclass(frozen=True)
class EscalationConfig:
    """Loaded ``config/escalation.yaml`` (or synthetic default)."""

    default: TierBudgets
    by_task_class: Mapping[str, TierBudgets] = field(default_factory=dict)
    source_path: Path | None = None

    def for_class(self, task_class: str) -> TierBudgets:
        return self.by_task_class.get(task_class, self.default)


def load_config(
    path: str | os.PathLike[str] | None = None,
) -> EscalationConfig:
    """Read ``config/escalation.yaml`` and return an :class:`EscalationConfig`.

    A missing file is **NOT an error**: this primitive ships with
    sensible defaults baked in (see ``TierBudgets`` field defaults), so
    a brand-new install works without operator action. Operators add the
    YAML when they need per-task-class overrides.

    Raises :class:`EscalationConfigError` on syntactically invalid YAML
    or a missing required field once the file is present.
    """
    config_path = Path(os.fspath(path)) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        return EscalationConfig(default=TierBudgets(), source_path=None)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EscalationConfigError(f"{config_path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise EscalationConfigError(f"{config_path}: top-level must be a mapping")
    schema_version = raw.get("schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        raise EscalationConfigError(
            f"{config_path}: schema_version must be {SCHEMA_VERSION}, "
            f"got {schema_version!r}"
        )
    base = TierBudgets().with_overrides(raw.get("default") or {})
    by_class_raw = raw.get("by_task_class") or {}
    if not isinstance(by_class_raw, Mapping):
        raise EscalationConfigError(
            f"{config_path}: by_task_class must be a mapping"
        )
    by_class: dict[str, TierBudgets] = {}
    for task_class, override in by_class_raw.items():
        if not isinstance(task_class, str):
            raise EscalationConfigError(
                f"{config_path}: by_task_class keys must be strings, "
                f"got {task_class!r}"
            )
        if not isinstance(override, Mapping):
            raise EscalationConfigError(
                f"{config_path}: by_task_class[{task_class!r}] must be a mapping"
            )
        by_class[task_class] = base.with_overrides(override)
    return EscalationConfig(
        default=base,
        by_task_class=by_class,
        source_path=config_path,
    )


def resolve_budgets(
    *,
    task_class: str,
    config: EscalationConfig,
    manifest_overrides: Mapping[str, Any] | None = None,
) -> TierBudgets:
    """Compose the four-layer override stack into the final ``TierBudgets``.

    See module docstring for the resolution order.

    ``manifest_overrides`` is the manifest's ``escalation_overrides`` block
    (e.g. agent-finance.yaml carries ``tier1_budget_seconds: 30``). It can
    also include ``by_task_class: { ... }`` for per-task overrides scoped
    to that one agent.
    """
    budgets = config.for_class(task_class)
    if manifest_overrides:
        budgets = budgets.with_overrides(manifest_overrides)
        per_class = manifest_overrides.get("by_task_class") or {}
        if isinstance(per_class, Mapping):
            class_specific = per_class.get(task_class)
            if isinstance(class_specific, Mapping):
                budgets = budgets.with_overrides(class_specific)
    return budgets
