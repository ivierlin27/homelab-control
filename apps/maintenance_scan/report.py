"""Markdown report renderer + tight Discord summary."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from .probe import ContainerRecord
from .registry import UpgradeFinding


SEVERITY_ORDER = ["major-upgrade", "upgrade", "floating", "error", "unmanaged", "ok"]
SEVERITY_EMOJI = {
    "major-upgrade": "🔴",
    "upgrade":       "🟠",
    "floating":      "🟡",
    "error":         "⚪",
    "unmanaged":     "⚪",
    "ok":            "🟢",
}


@dataclass
class FindingWithContext:
    """One finding plus all containers running it (host:container fan-out)."""
    finding: UpgradeFinding
    instances: list[ContainerRecord]
    verifier_status: str = "not-run"   # "accepted" | "rejected" | "escalated" | "not-run"
    verifier_notes: str = ""

    @property
    def severity(self) -> str:
        return self.finding.severity


def merge(records: Iterable[ContainerRecord], findings: Iterable[UpgradeFinding]) -> list[FindingWithContext]:
    """Group container records by (image, tag) and pair with their finding."""
    by_image_tag: dict[tuple[str, str], list[ContainerRecord]] = defaultdict(list)
    for r in records:
        by_image_tag[(r.image, r.tag)].append(r)
    out: list[FindingWithContext] = []
    for f in findings:
        instances = by_image_tag.get((f.image, f.current_tag), [])
        out.append(FindingWithContext(finding=f, instances=instances))
    out.sort(key=lambda x: (SEVERITY_ORDER.index(x.severity), x.finding.image))
    return out


def render_markdown(
    items: list[FindingWithContext],
    *,
    generated_at: datetime | None = None,
    scan_duration_s: float | None = None,
) -> str:
    ts = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    counts = {s: sum(1 for i in items if i.severity == s) for s in SEVERITY_ORDER}
    total_containers = sum(len(i.instances) for i in items)
    head = [
        f"# Homelab maintenance scan — {ts}",
        "",
        f"Scanned **{total_containers}** running container(s) across "
        f"all hosts; {len(items)} unique image:tag combination(s).",
        "",
        "## Summary",
        "",
        "| Severity | Count |",
        "|---|---|",
    ]
    for s in SEVERITY_ORDER:
        if counts[s]:
            head.append(f"| {SEVERITY_EMOJI[s]} {s} | {counts[s]} |")
    if scan_duration_s is not None:
        head += ["", f"_Scan completed in {scan_duration_s:.1f}s._"]
    head.append("")

    sections: list[str] = []
    for sev in SEVERITY_ORDER:
        chunk = [i for i in items if i.severity == sev]
        if not chunk:
            continue
        sections += ["", f"## {SEVERITY_EMOJI[sev]} {sev} ({len(chunk)})", ""]
        for it in chunk:
            f = it.finding
            target = f.newest_tag or "—"
            sections.append(f"### `{f.image}:{f.current_tag}` → `{target}`")
            sections.append("")
            sections.append(f"- **note:** {f.notes}")
            if it.verifier_status != "not-run":
                tick = {"accepted": "✅", "rejected": "❌", "escalated": "⚠️"}.get(it.verifier_status, "?")
                sections.append(f"- **verifier:** {tick} {it.verifier_status}"
                                + (f" — {it.verifier_notes}" if it.verifier_notes else ""))
            if it.instances:
                inst = ", ".join(f"`{c.host}/{c.container}`" for c in it.instances)
                sections.append(f"- **running on:** {inst}")
            sections.append("")
    return "\n".join(head + sections).rstrip() + "\n"


def render_discord(items: list[FindingWithContext], *, max_lines: int = 12) -> str:
    """Tight summary for Discord (<= ~1500 chars)."""
    counts = {s: sum(1 for i in items if i.severity == s) for s in SEVERITY_ORDER}
    headline = " · ".join(
        f"{SEVERITY_EMOJI[s]} {counts[s]} {s}"
        for s in SEVERITY_ORDER if counts[s]
    )
    lines = [f"**Weekly maintenance scan** — {headline}"]
    actionable = [i for i in items if i.severity in {"major-upgrade", "upgrade"}]
    if not actionable:
        lines.append("_No upgrade actions needed; everything in same-major is current._")
        return "\n".join(lines)
    lines.append("")
    for it in actionable[:max_lines]:
        verify_tick = ""
        if it.verifier_status == "accepted":
            verify_tick = " ✅"
        elif it.verifier_status == "rejected":
            verify_tick = " ❌"
        elif it.verifier_status == "escalated":
            verify_tick = " ⚠️"
        f = it.finding
        hosts = ", ".join(sorted({c.host for c in it.instances})) or "?"
        lines.append(f"{SEVERITY_EMOJI[it.severity]} `{f.image}` {f.current_tag} → **{f.newest_tag}**{verify_tick}  _{hosts}_")
    if len(actionable) > max_lines:
        lines.append(f"…and {len(actionable) - max_lines} more (see report).")
    return "\n".join(lines)
