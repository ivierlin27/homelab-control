"""Write a local markdown report for deferred categorize rows."""

from __future__ import annotations

from pathlib import Path

from .runner import EntryOutcome


def write_defer_report(
    outcomes: list[EntryOutcome],
    *,
    correlation_id: str,
    output_dir: Path,
) -> Path | None:
    deferred = [o for o in outcomes if o.status == "deferred"]
    if not deferred:
        return None
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"defer-{correlation_id}.md"
    lines = [
        f"# Categorize defer report `{correlation_id}`",
        "",
        f"**Deferred:** {len(deferred)}",
        "",
    ]
    for o in deferred:
        lines.extend(
            [
                f"## {o.date} — {o.description}",
                "",
                f"- Proposed: `{o.category or '—'}`",
                f"- Confidence: {o.confidence:.3f}",
                f"- Reason: {o.reason or 'verifier escalation'}",
                f"- Verifier rounds: {o.verifier_rounds}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
