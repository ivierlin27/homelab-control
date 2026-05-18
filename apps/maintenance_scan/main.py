"""Maintenance scan orchestrator.

End-to-end flow:

  probe (running containers across all hosts)
    → assess_many (registry tag listing per unique image:tag)
        → verifier loop (per actionable upgrade: re-query registry; if the
          newest tag the verifier sees differs from the builder's claim,
          revise the claim once; reject if still off)
    → merge + render markdown (docs/maintenance-reports/YYYY-MM-DD.md)
    → audit ledger entry (one per scan summarising counts + chain head)
    → optional Discord webhook post (tight summary)

Entry-points:
  - ``python -m apps.maintenance_scan`` (CLI; flags below)
  - ``run()`` (async; importable from tests)

Environment:
  - ``MAINTENANCE_SCAN_REPORT_DIR``   default: ``docs/maintenance-reports``
  - ``MAINTENANCE_SCAN_AUDIT_LOG``    default:
        ``~/.local/state/homelab-control/agent-homelab-maintainer/audit.jsonl``
  - ``MAINTENANCE_SCAN_SNAPSHOT``     default:
        ``~/.local/state/homelab-control/agent-homelab-maintainer/inventory-snapshot.jsonl``
  - ``MAINTENANCE_SCAN_DISCORD_WEBHOOK`` Discord webhook URL for #homelab
    (optional; missing means no post)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from _shared.audit import AuditLog  # noqa: E402
from _shared.verifier import (  # noqa: E402
    VerifierEscalation, VerifierVerdict, run_verifier_loop,
)

from .probe import probe_all, write_snapshot  # noqa: E402
from .registry import UpgradeFinding, assess_image, assess_many  # noqa: E402
from .report import FindingWithContext, merge, render_discord, render_markdown  # noqa: E402

log = logging.getLogger("maintenance-scan")

DEFAULT_REPORT_DIR = ROOT / "docs" / "maintenance-reports"
DEFAULT_AUDIT = Path.home() / ".local/state/homelab-control/agent-homelab-maintainer/audit.jsonl"
DEFAULT_SNAPSHOT = Path.home() / ".local/state/homelab-control/agent-homelab-maintainer/inventory-snapshot.jsonl"
PRINCIPAL = "agent:homelab-maintainer"
VERIFIER_PERSONA = "verifier:registry-recheck"


# ----- verifier callback --------------------------------------------------
# The verifier re-queries the registry using a *sync* httpx client. We don't
# need concurrency here (one image per round, max 2 rounds per upgrade), and
# staying sync avoids nesting an event loop inside the sync verifier-loop
# primitive.


def verify_upgrade(
    client: httpx.Client, claim: dict, _original_evidence: dict
) -> tuple[VerifierVerdict, str, dict]:
    """Re-query the registry and confirm the recommendation still holds.

    Uses the sync ``assess_image_sync`` helper so this function can be
    handed straight to ``run_verifier_loop`` (which is sync).
    """
    from .registry import assess_image_sync  # local import: optional helper

    fresh = assess_image_sync(client, claim["image"], claim["current_tag"])
    evidence = {"rechecked_severity": fresh.severity, "rechecked_newest": fresh.newest_tag}
    if fresh.severity == "ok":
        return VerifierVerdict.REJECT, "registry now reports current tag is newest in major", evidence
    if fresh.severity in {"upgrade", "major-upgrade"}:
        if fresh.newest_tag == claim["newest_tag"]:
            return VerifierVerdict.ACCEPT, f"registry confirms {fresh.newest_tag}", evidence
        return (
            VerifierVerdict.NEEDS_REVISION,
            f"registry now shows newest={fresh.newest_tag}, builder said {claim['newest_tag']}",
            evidence,
        )
    return VerifierVerdict.REJECT, f"registry classification flipped to {fresh.severity}", evidence


# ----- discord ------------------------------------------------------------

async def post_discord(webhook_url: str, content: str) -> None:
    # Discord caps message content at 2000 chars; truncate defensively.
    if len(content) > 1900:
        content = content[:1880] + "\n…truncated."
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(webhook_url, json={"content": content})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("discord post failed: %s", exc)


# ----- main ---------------------------------------------------------------

async def run(
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    audit_path: Path = DEFAULT_AUDIT,
    snapshot_path: Path = DEFAULT_SNAPSHOT,
    discord_webhook: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    started = time.monotonic()
    records = await probe_all()
    log.info("probed %d running containers", len(records))
    write_snapshot(records, str(snapshot_path))

    # Build the unique image:tag set the verifier will reason about.
    unique_pairs = sorted({(r.image, r.tag) for r in records})
    findings = await assess_many(unique_pairs)

    # Run the verifier ONLY on actionable upgrades (the others are
    # informational; verifying "ok" wastes API quota).
    items = merge(records, findings)
    actionable = [i for i in items if i.severity in {"upgrade", "major-upgrade"}]
    log.info("actionable upgrades pre-verifier: %d", len(actionable))

    audit = AuditLog(str(audit_path))

    def _run_verifiers():
        with httpx.Client(headers={"User-Agent": "homelab-control-maintenance/1.0"}, timeout=15.0) as client:
            for it in actionable:
                claim = {
                    "image": it.finding.image,
                    "current_tag": it.finding.current_tag,
                    "newest_tag": it.finding.newest_tag,
                    "severity": it.finding.severity,
                }
                evidence = {"available_tags": it.finding.available_tags or []}
                try:
                    accepted_claim, history = run_verifier_loop(
                        claim=claim, evidence=evidence,
                        verifier=lambda c, e: verify_upgrade(client, c, e),
                        persona=VERIFIER_PERSONA,
                        builder_revise=lambda c, last_round: {
                            **c,
                            "newest_tag": (last_round.rechecked_evidence or {}).get("rechecked_newest")
                                          or c["newest_tag"],
                        },
                        max_rounds=2,
                        audit=lambda payload: audit.append({"principal": PRINCIPAL, **payload}),
                        correlation_id=f"upgrade::{it.finding.image}",
                    )
                    it.verifier_status = "accepted"
                    it.verifier_notes = history[-1].notes
                    it.finding.newest_tag = accepted_claim["newest_tag"]
                except VerifierEscalation as esc:
                    it.verifier_status = "rejected" if "rejected" in esc.reason else "escalated"
                    it.verifier_notes = esc.reason

    # Run synchronous verifiers off the event loop so we don't block it.
    await asyncio.get_running_loop().run_in_executor(None, _run_verifiers)

    accepted_upgrades = [i for i in actionable if i.verifier_status == "accepted"]
    duration = time.monotonic() - started

    now = datetime.now(UTC)
    md = render_markdown(items, generated_at=now, scan_duration_s=duration)
    report_path = report_dir / f"{now.strftime('%Y-%m-%d')}.md"
    if not dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md, encoding="utf-8")
        log.info("wrote report %s", report_path)

    audit.append({
        "principal": PRINCIPAL,
        "event": "maintenance_scan_complete",
        "containers_probed": len(records),
        "unique_images": len(unique_pairs),
        "actionable_upgrades": len(actionable),
        "verifier_accepted": len(accepted_upgrades),
        "report_path": str(report_path),
        "duration_s": round(duration, 2),
    })

    if discord_webhook and not dry_run:
        await post_discord(discord_webhook, render_discord(items))

    return {
        "containers": len(records),
        "actionable": len(actionable),
        "verified_accepted": len(accepted_upgrades),
        "report": str(report_path),
        "duration_s": duration,
    }


def _cli() -> int:
    p = argparse.ArgumentParser(description="Run the homelab maintenance scan.")
    p.add_argument("--report-dir", type=Path, default=Path(os.environ.get("MAINTENANCE_SCAN_REPORT_DIR", str(DEFAULT_REPORT_DIR))))
    p.add_argument("--audit-log", type=Path, default=Path(os.environ.get("MAINTENANCE_SCAN_AUDIT_LOG", str(DEFAULT_AUDIT))))
    p.add_argument("--snapshot", type=Path, default=Path(os.environ.get("MAINTENANCE_SCAN_SNAPSHOT", str(DEFAULT_SNAPSHOT))))
    p.add_argument("--discord-webhook", default=os.environ.get("MAINTENANCE_SCAN_DISCORD_WEBHOOK"))
    p.add_argument("--dry-run", action="store_true", help="skip writes (report, audit) for testing")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    summary = asyncio.run(run(
        report_dir=args.report_dir,
        audit_path=args.audit_log,
        snapshot_path=args.snapshot,
        discord_webhook=args.discord_webhook,
        dry_run=args.dry_run,
    ))
    print(f"scan complete: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
