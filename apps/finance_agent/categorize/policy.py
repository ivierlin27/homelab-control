"""Load categorization policy YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

DEFAULT_POLICY_PATH = Path(__file__).with_name("policy.yaml")
_POLICY_RE_FLAGS = re.IGNORECASE


def _compile_policy_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a policy regex; strip redundant inline ``(?i)`` (flags are always IGNORECASE)."""
    normalized = str(pattern).replace("(?i)", "")
    return re.compile(normalized, _POLICY_RE_FLAGS)


@dataclass(frozen=True)
class CategoryRule:
    id: str
    pattern: re.Pattern[str]
    category: str
    confidence: float


@dataclass(frozen=True)
class ContextRule:
    """Account- and flow-aware rule (evaluated before payee-only rules)."""

    id: str
    description_pattern: re.Pattern[str]
    source_roles: frozenset[str]
    flow: str  # inflow | outflow | any
    category: str
    confidence: float
    reason: str
    counterparty_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class CategorizePolicy:
    threshold: float
    max_verifier_rounds: int
    account_roles: dict[str, tuple[str, ...]]
    context_rules: tuple[ContextRule, ...]
    rules: tuple[CategoryRule, ...]
    default_category: str
    default_confidence: float


def load_policy(path: Path | str | None = None) -> CategorizePolicy:
    policy_path = Path(path or DEFAULT_POLICY_PATH)
    if yaml is None:
        raise RuntimeError("PyYAML is required to load categorization policy")
    raw: dict[str, Any] = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}

    account_roles: dict[str, tuple[str, ...]] = {}
    for role_id, fragments in (raw.get("account_roles") or {}).items():
        account_roles[str(role_id)] = tuple(str(f) for f in fragments)

    context_rules: list[ContextRule] = []
    for item in raw.get("context_rules") or []:
        cp = item.get("counterparty_pattern")
        context_rules.append(
            ContextRule(
                id=str(item["id"]),
                description_pattern=_compile_policy_pattern(
                    str(item["description_pattern"])
                ),
                source_roles=frozenset(str(r) for r in (item.get("source_roles") or [])),
                flow=str(item.get("flow", "any")),
                category=str(item["category"]),
                confidence=float(item["confidence"]),
                reason=str(item.get("reason", "")),
                counterparty_pattern=(
                    _compile_policy_pattern(str(cp)) if cp else None
                ),
            )
        )

    rules: list[CategoryRule] = []
    for item in raw.get("rules") or []:
        rules.append(
            CategoryRule(
                id=str(item["id"]),
                pattern=_compile_policy_pattern(str(item["pattern"])),
                category=str(item["category"]),
                confidence=float(item["confidence"]),
            )
        )

    return CategorizePolicy(
        threshold=float(raw.get("threshold", 0.85)),
        max_verifier_rounds=int(raw.get("max_verifier_rounds", 2)),
        account_roles=account_roles,
        context_rules=tuple(context_rules),
        rules=tuple(rules),
        default_category=str(raw.get("default_category", "Expenses:Misc")),
        default_confidence=float(raw.get("default_confidence", 0.55)),
    )
