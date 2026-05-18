"""Tests for the escalation policy loader + override resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from .policy import (
    DEFAULT_TASK_CLASS,
    EscalationConfig,
    EscalationConfigError,
    TierBudgets,
    load_config,
    resolve_budgets,
)


# ---- TierBudgets ---------------------------------------------------


def test_tier_budgets_field_defaults():
    b = TierBudgets()
    assert b.tier1_budget_seconds == 300
    assert b.tier1_max_attempts == 3
    assert b.tier2_budget_seconds == 1800
    assert b.tier3_dm_after_seconds == 14400
    assert b.skip_tier2 is False


def test_with_overrides_applies_only_provided_fields():
    b = TierBudgets()
    out = b.with_overrides({"tier1_budget_seconds": 60})
    assert out.tier1_budget_seconds == 60
    assert out.tier2_budget_seconds == b.tier2_budget_seconds


def test_with_overrides_rejects_negative_int():
    with pytest.raises(EscalationConfigError):
        TierBudgets().with_overrides({"tier1_budget_seconds": -1})


def test_with_overrides_rejects_non_int():
    with pytest.raises(EscalationConfigError):
        TierBudgets().with_overrides({"tier2_budget_seconds": "fast"})


def test_with_overrides_ignores_unknown_keys():
    b = TierBudgets()
    out = b.with_overrides({"random_thing": 42})
    assert out == b


def test_with_overrides_handles_skip_tier2():
    b = TierBudgets().with_overrides({"skip_tier2": True})
    assert b.skip_tier2 is True


# ---- load_config ----------------------------------------------------


def test_load_config_missing_file_yields_defaults(tmp_path):
    config = load_config(tmp_path / "absent.yaml")
    assert config.default == TierBudgets()
    assert config.by_task_class == {}
    assert config.source_path is None


def test_load_config_invalid_yaml_raises(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("not: valid: yaml: ::", encoding="utf-8")
    with pytest.raises(EscalationConfigError):
        load_config(f)


def test_load_config_non_mapping_raises(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(EscalationConfigError):
        load_config(f)


def test_load_config_wrong_schema_version_raises(tmp_path):
    f = tmp_path / "v2.yaml"
    f.write_text(yaml.safe_dump({"schema_version": 999, "default": {}}), encoding="utf-8")
    with pytest.raises(EscalationConfigError, match="schema_version"):
        load_config(f)


def test_load_config_happy_path(tmp_path):
    f = tmp_path / "esc.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "default": {"tier1_budget_seconds": 600},
                "by_task_class": {
                    "homelab.deploy": {"tier1_budget_seconds": 120},
                    "finance.advise": {"skip_tier2": True, "tier1_budget_seconds": 30},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(f)
    assert config.default.tier1_budget_seconds == 600
    assert config.for_class("homelab.deploy").tier1_budget_seconds == 120
    # by-class entries inherit from `default`
    assert config.for_class("homelab.deploy").tier2_budget_seconds == 1800
    assert config.for_class("finance.advise").skip_tier2 is True
    assert config.for_class("unknown") == config.default


def test_load_real_repo_config_smoke():
    """The shipped config/escalation.yaml must load cleanly."""
    config = load_config()
    assert config.default.tier1_budget_seconds == 300
    finance = config.for_class("finance.categorize")
    assert finance.skip_tier2 is True


# ---- resolve_budgets ------------------------------------------------


def _config_with(by_class: dict) -> EscalationConfig:
    return EscalationConfig(
        default=TierBudgets(tier1_budget_seconds=300),
        by_task_class={k: TierBudgets().with_overrides(v) for k, v in by_class.items()},
    )


def test_resolve_budgets_falls_through_to_default():
    config = _config_with({})
    b = resolve_budgets(task_class="anything", config=config)
    assert b == config.default


def test_resolve_budgets_uses_by_class():
    config = _config_with({"homelab.deploy": {"tier1_budget_seconds": 120}})
    b = resolve_budgets(task_class="homelab.deploy", config=config)
    assert b.tier1_budget_seconds == 120


def test_resolve_budgets_manifest_top_level_overrides_by_class():
    config = _config_with({"finance.categorize": {"tier1_budget_seconds": 60}})
    b = resolve_budgets(
        task_class="finance.categorize",
        config=config,
        manifest_overrides={"tier1_budget_seconds": 30},
    )
    assert b.tier1_budget_seconds == 30


def test_resolve_budgets_manifest_per_class_wins_over_top_level():
    config = _config_with({"finance.categorize": {"tier1_budget_seconds": 60}})
    b = resolve_budgets(
        task_class="finance.categorize",
        config=config,
        manifest_overrides={
            "tier1_budget_seconds": 200,   # ignored for this class
            "by_task_class": {
                "finance.categorize": {"tier1_budget_seconds": 15},
            },
        },
    )
    assert b.tier1_budget_seconds == 15


def test_resolve_budgets_per_class_only_applies_to_matching_class():
    config = _config_with({})
    b = resolve_budgets(
        task_class="other.class",
        config=config,
        manifest_overrides={
            "by_task_class": {
                "homelab.deploy": {"tier1_budget_seconds": 999},
            },
        },
    )
    assert b.tier1_budget_seconds == 300


def test_resolve_budgets_with_skip_tier2_from_manifest():
    config = _config_with({})
    b = resolve_budgets(
        task_class="finance.advise",
        config=config,
        manifest_overrides={"skip_tier2": True},
    )
    assert b.skip_tier2 is True
