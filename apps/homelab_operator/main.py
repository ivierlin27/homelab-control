#!/usr/bin/env python3
"""Homelab operator utilities for inventory, capacity, observability, and plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) or {}


def capacity_report(hardware_path: Path, usage_path: Path | None) -> list[dict[str, Any]]:
    hardware = load_yaml(hardware_path).get("hardware", [])
    usage = {}
    if usage_path and usage_path.exists():
        usage = load_yaml(usage_path)

    findings: list[dict[str, Any]] = []
    for host in hardware:
        host_usage = usage.get(host["id"], {})
        thresholds = host.get("thresholds", {})
        for metric, warn_key, crit_key in [
            ("ram_percent", "ram_warn_percent", "ram_critical_percent"),
            ("disk_percent", "disk_warn_percent", "disk_critical_percent"),
            ("gpu_memory_percent", "gpu_memory_warn_percent", "gpu_memory_critical_percent"),
        ]:
            if metric not in host_usage or warn_key not in thresholds:
                continue
            value = float(host_usage[metric])
            warn = float(thresholds[warn_key])
            critical = float(thresholds.get(crit_key, 100))
            if value >= critical:
                severity = "critical"
            elif value >= warn:
                severity = "warning"
            else:
                continue
            findings.append(
                {
                    "host": host["id"],
                    "metric": metric,
                    "value": value,
                    "severity": severity,
                    "message": f"{host['id']} {metric} is at {value:.1f}%",
                }
            )
    return findings


def observability_report(services_path: Path, observability_path: Path) -> list[dict[str, Any]]:
    services = load_yaml(services_path).get("services", [])
    observability = load_yaml(observability_path)
    profiles = observability.get("profiles", {})
    checks = {item["service"]: item for item in observability.get("checks", [])}

    findings: list[dict[str, Any]] = []
    for service in services:
        profile_name = service.get("observability_profile")
        required = profiles.get(profile_name, {}).get("required", [])
        declared = checks.get(service["id"], {})
        missing = []
        for requirement in required:
            key = f"has_{requirement}"
            if not declared.get(key, False):
                missing.append(requirement)
        if missing:
            findings.append(
                {
                    "service": service["id"],
                    "profile": profile_name,
                    "missing": missing,
                    "message": f"{service['id']} is missing observability items: {', '.join(missing)}",
                }
            )
    return findings


def render_plan(title: str, repo: str, risk: str, template_path: Path) -> str:
    template = template_path.read_text()
    return (
        template.replace("<!-- one sentence outcome -->", title)
        .replace("- repo:", f"- repo: {repo}")
        .replace("- risk:", f"- risk: {risk}")
    )


def print_output(findings: Any, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(findings, indent=2))
        return
    if isinstance(findings, list):
        if not findings:
            print("No findings.")
            return
        for finding in findings:
            print(f"- {finding['message']}")
        return
    print(str(findings))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    cap = subparsers.add_parser("capacity-report")
    cap.add_argument("--hardware", default=str(ROOT / "inventory" / "hardware.yaml"))
    cap.add_argument("--usage")
    cap.add_argument("--format", choices=["json", "markdown"], default="markdown")

    obs = subparsers.add_parser("observability-report")
    obs.add_argument("--services", default=str(ROOT / "inventory" / "services.yaml"))
    obs.add_argument("--observability", default=str(ROOT / "inventory" / "observability.yaml"))
    obs.add_argument("--format", choices=["json", "markdown"], default="markdown")

    plan = subparsers.add_parser("create-plan")
    plan.add_argument("--title", required=True)
    plan.add_argument("--repo", default="homelab-control")
    plan.add_argument("--risk", default="safe-update")
    plan.add_argument("--template", default=str(ROOT / "config" / "planka" / "card-template.md"))

    args = parser.parse_args()

    if args.command == "capacity-report":
        findings = capacity_report(Path(args.hardware), Path(args.usage) if args.usage else None)
        print_output(findings, args.format)
        return 0

    if args.command == "observability-report":
        findings = observability_report(Path(args.services), Path(args.observability))
        print_output(findings, args.format)
        return 0

    if args.command == "create-plan":
        print(render_plan(args.title, args.repo, args.risk, Path(args.template)))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
