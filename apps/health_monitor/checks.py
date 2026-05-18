"""Concrete checks. Each ``check_*`` is a ``Check`` (returns list[CheckResult]).

To add a new check: write a function and add it to ``ALL_CHECKS``. The
runner takes care of state, transitions, alerts, and audit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable

import httpx

from .core import CheckResult, Status

# ---------- knobs (env-overridable) ----------

DEFAULT_AUDIT_DIR = Path(os.environ.get(
    "HEALTH_MONITOR_AUDIT_DIR",
    str(Path.home() / ".local/state/homelab-control"),
))
DEFAULT_PROXMOX_SSH = os.environ.get("HEALTH_MONITOR_PROXMOX_SSH", "root@proxmox.dev-path.org")
# Comma-separated list of restic repo paths (local) + sftp:user@host:/path for off-host
DEFAULT_RESTIC_REPOS = [
    s.strip() for s in os.environ.get("HEALTH_MONITOR_RESTIC_REPOS", "").split(",") if s.strip()
]
DEFAULT_RESTIC_FRESH_HOURS = float(os.environ.get("HEALTH_MONITOR_RESTIC_FRESH_HOURS", "30"))
# Timers we care about (local on Alienware). Format: unit name → max age hours.
DEFAULT_LOCAL_TIMERS = {
    "alienware-maintenance-scan.timer": 24 * 8,    # weekly + grace
    "alienware-backup-hot.timer":       24 * 1.5,  # hot tier daily
    "alienware-backup-full.timer":      24 * 8,    # full weekly
    "alienware-backup-dr-drill.timer":  24 * 100,  # quarterly
    "alienware-master-dashboard.service": None,    # service, not timer — check is_active
}
# Service-style units (always-on) to verify with `is-active`
DEFAULT_LOCAL_SERVICES = [
    "alienware-master-dashboard.service",
    "alienware-litellm-cost-relay.service",
    "homelab-model-gateway.service",
]

# ---------- helpers ----------

def _run(cmd: list[str], *, timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return -2, "", str(exc)


# ---------- check: audit chain integrity ----------

def check_audit_chains(*, audit_dir: Path = DEFAULT_AUDIT_DIR) -> list[CheckResult]:
    """For each `<dir>/audit.jsonl` under audit_dir, run our chain verifier."""
    out: list[CheckResult] = []
    if not audit_dir.exists():
        return out
    for ledger in sorted(audit_dir.rglob("audit.jsonl")):
        name = f"audit:{ledger.parent.name}"
        rc, stdout, stderr = _run(
            ["python3", "-m", "apps._shared.audit", "verify", str(ledger)],
            timeout=30.0,
        )
        out.append(_audit_result(name, ledger, rc, stdout, stderr))
    return out


def _audit_result(name: str, ledger: Path, rc: int, stdout: str, stderr: str) -> CheckResult:
    if rc == 0:
        # Parse "ok: <path> — N chained (M legacy prefix); head=…" for metrics
        chained = 0
        for tok in stdout.split():
            if tok.isdigit():
                chained = int(tok); break
        return CheckResult(
            name=name, status=Status.HEALTHY,
            detail=f"chain intact ({chained} entries)",
            metrics={"chained": chained},
            runbook="docs/runbooks/README.md (audit chain verify failed → AUDIT_RECOVERY)",
        )
    if rc < 0:
        return CheckResult(name=name, status=Status.UNKNOWN, detail=stderr.strip()[:160])
    return CheckResult(
        name=name, status=Status.UNHEALTHY,
        detail=(stdout + stderr).strip().splitlines()[-1][:200] if (stdout + stderr).strip() else f"verify rc={rc}",
        runbook="docs/runbooks/README.md (audit chain verify failed)",
    )


# ---------- check: systemd timers ran recently enough ----------

def check_systemd_timers(*, expected: dict[str, float | None] = DEFAULT_LOCAL_TIMERS) -> list[CheckResult]:
    """Each timer should have run within ``hours`` ago. ``None`` skips age check."""
    out: list[CheckResult] = []
    # `systemctl --user show <unit> -p LastTriggerUSec -p Result -p NextElapseUSecRealtime`
    for unit, max_hours in expected.items():
        if not unit.endswith(".timer"):
            continue
        rc, stdout, stderr = _run([
            "systemctl", "--user", "show", unit,
            "-p", "LastTriggerUSec", "-p", "Result", "-p", "NextElapseUSecRealtime",
            "-p", "ActiveState",
        ], timeout=5.0)
        if rc != 0:
            out.append(CheckResult(name=f"timer:{unit}", status=Status.UNKNOWN,
                                   detail=stderr.strip()[:120] or f"systemctl rc={rc}"))
            continue
        fields = dict(line.split("=", 1) for line in stdout.strip().splitlines() if "=" in line)
        last = fields.get("LastTriggerUSec", "n/a").strip()
        next_elapse = fields.get("NextElapseUSecRealtime", "").strip()
        result = fields.get("Result", "n/a").strip()
        active = fields.get("ActiveState", "").strip()
        never_fired = last in {"n/a", "0", "", "0 n/a"} or not any(c.isdigit() for c in last)
        scheduled_future = bool(next_elapse) and next_elapse not in {"0", "", "n/a"}
        if never_fired and scheduled_future and active == "active":
            out.append(CheckResult(
                name=f"timer:{unit}", status=Status.HEALTHY,
                detail=f"never fired yet; next run {next_elapse}",
            ))
            continue
        if never_fired:
            out.append(CheckResult(
                name=f"timer:{unit}", status=Status.UNHEALTHY,
                detail=f"timer has never fired and is not scheduled (active={active})",
                runbook="docs/runbooks/README.md → 'Backup didn't run last night' row",
            ))
            continue
        if result not in {"success", "no-op", ""}:
            out.append(CheckResult(
                name=f"timer:{unit}", status=Status.UNHEALTHY,
                detail=f"last run Result={result}; LastTrigger={last}",
                runbook=f"see runbook for {unit.removesuffix('.timer')}",
            ))
        else:
            out.append(CheckResult(
                name=f"timer:{unit}", status=Status.HEALTHY,
                detail=f"last {last}; result={result or 'success'}",
            ))
    return out


# ---------- check: systemd services are active ----------

def check_systemd_services(*, units: Iterable[str] = DEFAULT_LOCAL_SERVICES) -> list[CheckResult]:
    out: list[CheckResult] = []
    for unit in units:
        rc, stdout, stderr = _run(["systemctl", "--user", "is-active", unit], timeout=5.0)
        active = stdout.strip()
        if active == "active":
            out.append(CheckResult(name=f"service:{unit}", status=Status.HEALTHY, detail="active"))
        else:
            out.append(CheckResult(
                name=f"service:{unit}", status=Status.UNHEALTHY,
                detail=f"is-active={active!r}; rc={rc}",
                runbook=f"see runbook for {unit.removesuffix('.service')}",
            ))
    return out


# ---------- check: service health endpoints ----------

def check_health_endpoints(*, inventory_path: Path | None = None) -> list[CheckResult]:
    """GET each `endpoints[].url` from inventory/services.yaml; 2xx/3xx = healthy."""
    out: list[CheckResult] = []
    p = inventory_path or Path(__file__).resolve().parents[2] / "inventory" / "services.yaml"
    if not p.exists():
        return out
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return [CheckResult(name="health:inventory", status=Status.UNHEALTHY,
                            detail=f"could not parse {p}: {exc}"[:160])]
    services = data.get("services", []) or []
    with httpx.Client(timeout=5.0, verify=False, follow_redirects=True) as client:
        for svc in services:
            sid = svc.get("id", "?")
            for endpoint in svc.get("endpoints", []) or []:
                ep_name = endpoint.get("name") or "default"
                url = endpoint.get("url")
                if not url:
                    continue
                name = f"health:{sid}:{ep_name}"
                try:
                    r = client.get(url)
                    if 200 <= r.status_code < 400:
                        out.append(CheckResult(
                            name=name, status=Status.HEALTHY,
                            detail=f"HTTP {r.status_code} {url}",
                        ))
                    else:
                        out.append(CheckResult(
                            name=name, status=Status.UNHEALTHY,
                            detail=f"HTTP {r.status_code} {url}",
                            runbook=f"see runbook for {sid}",
                        ))
                except httpx.HTTPError as exc:
                    out.append(CheckResult(
                        name=name, status=Status.UNKNOWN,
                        detail=f"{type(exc).__name__}: {url}",
                    ))
    return out


# ---------- check: restic snapshot freshness ----------

def check_restic_freshness(
    *,
    repos: Iterable[str] = DEFAULT_RESTIC_REPOS,
    fresh_hours: float = DEFAULT_RESTIC_FRESH_HOURS,
) -> list[CheckResult]:
    """Each repo's latest snapshot must be within ``fresh_hours``."""
    out: list[CheckResult] = []
    if not shutil.which("restic"):
        return out  # not installed here — skip silently (we may be in CI)
    now = time.time()
    for repo in repos:
        rc, stdout, stderr = _run(
            ["restic", "-r", repo, "snapshots", "--json", "--last", "1"],
            timeout=30.0,
        )
        name = f"restic:{repo.split('/')[-1] or repo}"
        if rc != 0:
            out.append(CheckResult(
                name=name, status=Status.UNHEALTHY,
                detail=(stderr or stdout).strip().splitlines()[-1][:160] if (stderr or stdout).strip() else f"rc={rc}",
                runbook="docs/runbooks/backup.md",
            ))
            continue
        try:
            snaps = json.loads(stdout)
        except json.JSONDecodeError:
            out.append(CheckResult(name=name, status=Status.UNKNOWN, detail="snapshots json parse failed"))
            continue
        if not snaps:
            out.append(CheckResult(
                name=name, status=Status.UNHEALTHY,
                detail="no snapshots in repo",
                runbook="docs/runbooks/backup.md",
            ))
            continue
        latest = snaps[-1]
        # `time`: "2026-05-17T09:00:00.123-07:00"
        import datetime
        try:
            t = datetime.datetime.fromisoformat(latest["time"])
            age_h = (now - t.timestamp()) / 3600.0
        except Exception:  # noqa: BLE001
            out.append(CheckResult(name=name, status=Status.UNKNOWN, detail="snapshot time unparseable"))
            continue
        if age_h <= fresh_hours:
            out.append(CheckResult(
                name=name, status=Status.HEALTHY,
                detail=f"latest snapshot {age_h:.1f}h ago (limit {fresh_hours:.0f}h)",
                metrics={"age_h": round(age_h, 2)},
            ))
        else:
            out.append(CheckResult(
                name=name, status=Status.UNHEALTHY,
                detail=f"latest snapshot {age_h:.1f}h ago (limit {fresh_hours:.0f}h)",
                runbook="docs/runbooks/backup.md → 'Backup didn't run last night'",
                metrics={"age_h": round(age_h, 2)},
            ))
    return out


# ---------- registry ----------

ALL_CHECKS = [
    check_audit_chains,
    check_systemd_timers,
    check_systemd_services,
    check_health_endpoints,
    check_restic_freshness,
]
