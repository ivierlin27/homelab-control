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


@dataclass(frozen=True)
class CategoryRule:
    id: str
    pattern: re.Pattern[str]
    category: str
    confidence: float


@dataclass(frozen=True)
class CategorizePolicy:
    threshold: float
    max_verifier_rounds: int
    rules: tuple[CategoryRule, ...]
    default_category: str
    default_confidence: float


def load_policy(path: Path | str | None = None) -> CategorizePolicy:
    policy_path = Path(path or DEFAULT_POLICY_PATH)
    if yaml is None:
        raise RuntimeError("PyYAML is required to load categorization policy")
    raw: dict[str, Any] = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    rules: list[CategoryRule] = []
    for item in raw.get("rules") or []:
        rules.append(
            CategoryRule(
                id=str(item["id"]),
                pattern=re.compile(str(item["pattern"])),
                category=str(item["category"]),
                confidence=float(item["confidence"]),
            )
        )
    return CategorizePolicy(
        threshold=float(raw.get("threshold", 0.85)),
        max_verifier_rounds=int(raw.get("max_verifier_rounds", 2)),
        rules=tuple(rules),
        default_category=str(raw.get("default_category", "Expenses:Misc")),
        default_confidence=float(raw.get("default_confidence", 0.55)),
    )
