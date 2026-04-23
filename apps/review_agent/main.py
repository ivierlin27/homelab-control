#!/usr/bin/env python3
"""Review-agent policy evaluator for agent-authored PRs/MRs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) or {}


def starts_with_any(value: str, prefixes: list[str]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def evaluate(policy: dict[str, Any], pr: dict[str, Any]) -> dict[str, Any]:
    labels = set(pr.get("labels", []))
    changed_files = pr.get("changed_files", [])
    checks_passed = bool(pr.get("checks_passed", False))
    reasons: list[str] = []

    if policy["request_changes"].get("if_checks_failed", False) and not checks_passed:
        reasons.append("required checks are not green")
    if policy["request_changes"].get("if_missing_plan_link", False) and not pr.get("has_plan_link", False):
        reasons.append("missing plan link")
    if policy["request_changes"].get("if_missing_planka_card", False) and not pr.get("has_planka_card", False):
        reasons.append("missing Planka card")
    if policy["request_changes"].get("if_missing_risk_marker", False) and not labels:
        reasons.append("missing risk label")
    if reasons:
        return {"decision": "request_changes", "reasons": reasons}

    human = policy["human_review"]
    if labels.intersection(human.get("required_labels", [])):
        return {
            "decision": "needs_human_review",
            "reasons": [f"matched human-review label(s): {sorted(labels.intersection(human['required_labels']))}"],
        }

    for path in changed_files:
        if starts_with_any(path, human.get("required_path_prefixes", [])):
            return {
                "decision": "needs_human_review",
                "reasons": [f"path requires human review: {path}"],
            }
        if Path(path).name in human.get("new_service_files", []):
            return {
                "decision": "needs_human_review",
                "reasons": [f"new service manifest touched: {path}"],
            }

    auto = policy["auto_merge"]
    forbidden = auto.get("forbidden_path_prefixes", [])
    if any(starts_with_any(path, forbidden) for path in changed_files):
        return {
            "decision": "needs_human_review",
            "reasons": ["forbidden path for auto-merge touched"],
        }

    allowed_prefixes = auto.get("allowed_path_prefixes", [])
    if changed_files and all(starts_with_any(path, allowed_prefixes) for path in changed_files):
        if labels.intersection(auto.get("allowed_labels", [])) or all(
            starts_with_any(path, allowed_prefixes) for path in changed_files
        ):
            return {
                "decision": "approve_and_merge",
                "reasons": ["all changed files are inside auto-merge-safe prefixes and checks passed"],
            }

    return {
        "decision": policy["defaults"].get("on_ambiguity", "needs_human_review"),
        "reasons": ["change did not match any explicit safe auto-merge policy"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default=str(ROOT / "config" / "policies" / "review-policy.yaml"))
    parser.add_argument("--input", required=True, help="JSON file describing the PR/MR")
    args = parser.parse_args()

    policy = load_yaml(Path(args.policy))
    pr = json.loads(Path(args.input).read_text())
    print(json.dumps(evaluate(policy, pr), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
