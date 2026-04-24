#!/usr/bin/env python3
"""Homelab operator utilities for inventory, capacity, observability, and plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_SYNC_STATE = (
    Path.home() / ".local" / "state" / "homelab-control" / "inventory-memory-sync-state.json"
)


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) or {}


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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


def build_service_inventory_records(services_path: Path, observability_path: Path) -> list[dict[str, Any]]:
    services = load_yaml(services_path).get("services", [])
    observability = load_yaml(observability_path)
    profiles = observability.get("profiles", {})
    checks = {item["service"]: item for item in observability.get("checks", [])}

    records: list[dict[str, Any]] = []
    for service in sorted(services, key=lambda item: item["id"]):
        profile_name = service.get("observability_profile")
        required = profiles.get(profile_name, {}).get("required", [])
        declared = checks.get(service["id"], {})

        present = {requirement: bool(declared.get(f"has_{requirement}", False)) for requirement in required}
        missing = [requirement for requirement, enabled in present.items() if not enabled]

        extras = {
            key.removeprefix("has_"): value
            for key, value in declared.items()
            if key.startswith("has_") and key.removeprefix("has_") not in present
        }

        record = {
            "record_type": "homelab_service_inventory",
            "service_id": service["id"],
            "host": service["host"],
            "type": service["type"],
            "role": service["role"],
            "repo": service["repo"],
            "observability_profile": profile_name,
            "endpoints": service.get("endpoints", []),
            "observability": {
                "required": required,
                "present": present,
                "missing": missing,
                "extras": extras,
            },
            "provenance": {
                "source_repo": "homelab-control",
                "source_files": [
                    display_path(services_path),
                    display_path(observability_path),
                ],
            },
        }
        record["fingerprint"] = fingerprint_record(record)
        records.append(record)
    return records


def fingerprint_record(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_inventory_memory_text(record: dict[str, Any]) -> str:
    observability = record["observability"]
    lines = [
        f"Service inventory: {record['service_id']}",
        "",
        "This is a derived homelab service inventory record. Git-backed inventory files remain the source of truth.",
        "",
        f"- host: {record['host']}",
        f"- type: {record['type']}",
        f"- role: {record['role']}",
        f"- repo: {record['repo']}",
        f"- observability_profile: {record['observability_profile']}",
        f"- required_observability: {', '.join(observability['required']) or '(none)'}",
        f"- missing_observability: {', '.join(observability['missing']) or '(none)'}",
        f"- source_repo: {record['provenance']['source_repo']}",
        f"- source_files: {', '.join(record['provenance']['source_files'])}",
        f"- record_key: homelab.service.{record['service_id']}",
        f"- fingerprint: {record['fingerprint']}",
    ]

    if record["endpoints"]:
        lines.append("- endpoints:")
        for endpoint in record["endpoints"]:
            name = endpoint.get("name", "endpoint")
            url = endpoint.get("url", "")
            lines.append(f"  - {name}: {url}")

    return "\n".join(lines)


def build_inventory_ingest_payload(
    record: dict[str, Any],
    *,
    principal: str,
    source: str,
    command_or_api: str,
    git_ref: str,
    artifact_url: str,
) -> dict[str, Any]:
    return {
        "type": "text",
        "content": render_inventory_memory_text(record),
        "source": source,
        "principal": principal,
        "command_or_api": command_or_api,
        "git_ref": git_ref,
        "artifact_url": artifact_url,
        "metadata": {
            "record_type": record["record_type"],
            "record_key": f"homelab.service.{record['service_id']}",
            "fingerprint": record["fingerprint"],
            "service_id": record["service_id"],
            "host": record["host"],
            "role": record["role"],
            "repo": record["repo"],
            "observability_profile": record["observability_profile"],
            "source_files": record["provenance"]["source_files"],
            "missing_observability": record["observability"]["missing"],
            "record": record,
        },
    }


def load_sync_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    records = raw.get("records", raw) if isinstance(raw, dict) else {}
    if not isinstance(records, dict):
        return {}
    return {str(key): str(value) for key, value in records.items()}


def save_sync_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "records": dict(sorted(state.items()))}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)


def sync_inventory_memory(
    *,
    services_path: Path,
    observability_path: Path,
    ingest_url: str,
    principal: str,
    source: str,
    command_or_api: str,
    git_ref: str,
    artifact_url: str,
    state_path: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    if not dry_run and not ingest_url:
        raise ValueError("ingest_url is required unless --dry-run is used")

    records = build_service_inventory_records(services_path, observability_path)
    previous_state = load_sync_state(state_path)
    next_state = dict(previous_state)

    changed: list[dict[str, Any]] = []
    skipped: list[str] = []
    results: list[dict[str, Any]] = []

    for record in records:
        record_key = f"homelab.service.{record['service_id']}"
        fingerprint = record["fingerprint"]
        if previous_state.get(record_key) == fingerprint:
            skipped.append(record["service_id"])
            continue

        payload = build_inventory_ingest_payload(
            record,
            principal=principal,
            source=source,
            command_or_api=command_or_api,
            git_ref=git_ref,
            artifact_url=artifact_url,
        )
        changed.append({"service_id": record["service_id"], "record_key": record_key, "payload": payload})

        if dry_run:
            results.append({"service_id": record["service_id"], "status": "dry-run"})
            continue

        try:
            response = post_json(ingest_url, payload, timeout)
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            results.append(
                {
                    "service_id": record["service_id"],
                    "status": "error",
                    "error": f"http {exc.code}",
                    "response": response_body,
                }
            )
            continue
        except error.URLError as exc:
            results.append(
                {
                    "service_id": record["service_id"],
                    "status": "error",
                    "error": str(exc.reason),
                }
            )
            continue

        next_state[record_key] = fingerprint
        results.append({"service_id": record["service_id"], "status": "ingested", "response": response})

    if dry_run:
        return {"changed": changed, "skipped": skipped, "results": results}

    if any(result["status"] == "error" for result in results):
        return {"changed": changed, "skipped": skipped, "results": results, "state_updated": False}

    if changed:
        save_sync_state(state_path, next_state)
    return {"changed": changed, "skipped": skipped, "results": results, "state_updated": bool(changed)}


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

    export = subparsers.add_parser("inventory-memory-export")
    export.add_argument("--services", default=str(ROOT / "inventory" / "services.yaml"))
    export.add_argument("--observability", default=str(ROOT / "inventory" / "observability.yaml"))
    export.add_argument("--format", choices=["json", "markdown"], default="json")
    export.add_argument("--payload", action="store_true")
    export.add_argument("--principal", default="agent:homelab")
    export.add_argument("--source", default="operator")
    export.add_argument("--command-or-api", default="homelab_operator:inventory-memory-export")
    export.add_argument("--git-ref", default="homelab-control@working-tree")
    export.add_argument("--artifact-url", default="")

    sync = subparsers.add_parser("inventory-memory-sync")
    sync.add_argument("--services", default=str(ROOT / "inventory" / "services.yaml"))
    sync.add_argument("--observability", default=str(ROOT / "inventory" / "observability.yaml"))
    sync.add_argument("--ingest-url", default=os.environ.get("MEMORY_ENGINE_INGEST_URL", ""))
    sync.add_argument("--principal", default="agent:homelab")
    sync.add_argument("--source", default="operator")
    sync.add_argument("--command-or-api", default="homelab_operator:inventory-memory-sync")
    sync.add_argument("--git-ref", default="homelab-control@working-tree")
    sync.add_argument("--artifact-url", default="")
    sync.add_argument("--state-file", default=str(DEFAULT_INVENTORY_SYNC_STATE))
    sync.add_argument("--timeout", type=int, default=30)
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--format", choices=["json", "markdown"], default="markdown")

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

    if args.command == "inventory-memory-export":
        records = build_service_inventory_records(Path(args.services), Path(args.observability))
        if args.payload:
            payloads = [
                build_inventory_ingest_payload(
                    record,
                    principal=args.principal,
                    source=args.source,
                    command_or_api=args.command_or_api,
                    git_ref=args.git_ref,
                    artifact_url=args.artifact_url,
                )
                for record in records
            ]
            print_output(payloads, args.format)
            return 0

        if args.format == "markdown":
            for index, record in enumerate(records):
                if index:
                    print("\n---\n")
                print(render_inventory_memory_text(record))
            return 0

        print_output(records, args.format)
        return 0

    if args.command == "inventory-memory-sync":
        try:
            report = sync_inventory_memory(
                services_path=Path(args.services),
                observability_path=Path(args.observability),
                ingest_url=args.ingest_url,
                principal=args.principal,
                source=args.source,
                command_or_api=args.command_or_api,
                git_ref=args.git_ref,
                artifact_url=args.artifact_url,
                state_path=Path(args.state_file),
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            parser.error(str(exc))

        if args.format == "json":
            print_output(report, "json")
            return 0

        changed = report.get("changed", [])
        skipped = report.get("skipped", [])
        results = report.get("results", [])
        print(f"Changed records: {len(changed)}")
        print(f"Skipped records: {len(skipped)}")
        for result in results:
            service_id = result["service_id"]
            status = result["status"]
            if status == "ingested":
                print(f"- ingested {service_id}")
            elif status == "dry-run":
                print(f"- would ingest {service_id}")
            else:
                print(f"- failed {service_id}: {result.get('error', 'unknown error')}")
        if skipped:
            print(f"- skipped unchanged: {', '.join(skipped)}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
