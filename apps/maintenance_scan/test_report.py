"""Tests for report rendering + merge."""

from __future__ import annotations

from datetime import UTC, datetime

from .probe import ContainerRecord
from .registry import UpgradeFinding
from .report import FindingWithContext, merge, render_discord, render_markdown


def _rec(host, container, image, tag):
    return ContainerRecord(host=host, container=container, image=image, tag=tag,
                           container_id="x", image_id="sha", status="running")


def test_merge_groups_fan_out_by_image_tag():
    records = [
        _rec("h1", "c1", "img", "1.0.0"),
        _rec("h2", "c2", "img", "1.0.0"),  # same image:tag, different host
        _rec("h1", "c3", "other", "latest"),
    ]
    findings = [
        UpgradeFinding(image="img", current_tag="1.0.0", newest_tag="1.1.0", severity="upgrade", notes=""),
        UpgradeFinding(image="other", current_tag="latest", newest_tag=None, severity="floating", notes=""),
    ]
    merged = merge(records, findings)
    img_item = next(m for m in merged if m.finding.image == "img")
    assert len(img_item.instances) == 2
    assert {c.host for c in img_item.instances} == {"h1", "h2"}


def test_merge_sorts_by_severity():
    findings = [
        UpgradeFinding(image="a", current_tag="x", newest_tag=None, severity="ok", notes=""),
        UpgradeFinding(image="b", current_tag="x", newest_tag=None, severity="major-upgrade", notes=""),
        UpgradeFinding(image="c", current_tag="x", newest_tag=None, severity="upgrade", notes=""),
    ]
    merged = merge([], findings)
    assert [m.finding.image for m in merged] == ["b", "c", "a"]


def test_render_markdown_includes_counts_and_sections():
    items = [
        FindingWithContext(
            finding=UpgradeFinding(image="img", current_tag="1.0.0", newest_tag="1.1.0",
                                   severity="upgrade", notes="upgrade in major"),
            instances=[_rec("h1", "c1", "img", "1.0.0")],
            verifier_status="accepted",
            verifier_notes="registry confirmed",
        ),
    ]
    md = render_markdown(items, generated_at=datetime(2026, 5, 17, 16, 0, tzinfo=UTC), scan_duration_s=3.4)
    assert "2026-05-17 16:00 UTC" in md
    assert "## Summary" in md
    assert "upgrade" in md
    assert "`img:1.0.0`" in md and "`1.1.0`" in md
    assert "verifier" in md and "accepted" in md
    assert "h1/c1" in md
    assert "3.4s" in md


def test_render_discord_short_circuits_when_nothing_actionable():
    items = [
        FindingWithContext(
            finding=UpgradeFinding(image="i", current_tag="1.0", newest_tag=None,
                                   severity="ok", notes=""),
            instances=[],
        ),
    ]
    out = render_discord(items)
    assert "No upgrade actions" in out


def test_render_discord_lists_actionable_with_verify_ticks():
    items = [
        FindingWithContext(
            finding=UpgradeFinding(image="img", current_tag="1.0.0", newest_tag="2.0.0",
                                   severity="major-upgrade", notes=""),
            instances=[_rec("hostA", "c", "img", "1.0.0")],
            verifier_status="accepted",
        ),
        FindingWithContext(
            finding=UpgradeFinding(image="other", current_tag="1.0", newest_tag="1.1",
                                   severity="upgrade", notes=""),
            instances=[_rec("hostB", "c", "other", "1.0")],
            verifier_status="rejected",
        ),
    ]
    out = render_discord(items)
    assert "img" in out and "1.0.0 → **2.0.0**" in out
    assert "✅" in out and "❌" in out
    assert "hostA" in out and "hostB" in out


def test_render_discord_truncates_long_lists():
    items = []
    for i in range(20):
        items.append(FindingWithContext(
            finding=UpgradeFinding(image=f"img{i}", current_tag="1.0", newest_tag="1.1",
                                   severity="upgrade", notes=""),
            instances=[_rec("h", "c", f"img{i}", "1.0")],
        ))
    out = render_discord(items, max_lines=5)
    assert "and 15 more" in out
