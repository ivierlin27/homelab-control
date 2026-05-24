"""Tests for Tier-3 pending ack / follow-up state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from apps._shared.escalation.tier3_pending import (
    Tier3PendingRecord,
    is_message_acknowledged,
    load_pending_records,
    process_pending_followups,
    register_pending,
    update_pending,
)


def test_register_and_load_pending(tmp_path: Path) -> None:
    register_pending(
        Tier3PendingRecord(
            message_id="1234567890123456780",
            channel_id="9876543210987654320",
            task_class="homelab.deploy",
            principal="agent:executive",
            urgent=False,
            posted_at=datetime.now(timezone.utc).isoformat(),
            dm_after_seconds=60,
        ),
        state_dir=tmp_path,
    )
    records = load_pending_records(state_dir=tmp_path)
    assert len(records) == 1
    assert records[0].message_id == "1234567890123456780"


def test_process_pending_sends_dm_when_overdue(tmp_path: Path) -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    register_pending(
        Tier3PendingRecord(
            message_id="1234567890123456789",
            channel_id="9876543210987654321",
            task_class="homelab.deploy",
            principal="agent:executive",
            urgent=False,
            posted_at=old,
            dm_after_seconds=60,
        ),
        state_dir=tmp_path,
    )

    def fake_check(token, channel_id, message_id):
        return False

    def fake_dm(token, user_id, content):
        return {"user_id": user_id, "ok": True}

    results = process_pending_followups(
        token="token",
        dm_user_ids=["user-1"],
        state_dir=tmp_path,
        send_dm=fake_dm,
        check_ack=fake_check,
    )
    assert results and results[0]["action"] == "dm_sent"
    records = load_pending_records(state_dir=tmp_path)
    assert records[0].dm_sent is True


def test_is_message_acknowledged_detects_reaction() -> None:
    message = {
        "reactions": [
            {"emoji": {"name": "white_check_mark"}, "count": 1},
        ],
    }
    assert is_message_acknowledged(
        "token",
        "c",
        "m",
        fetch_message=lambda *args: message,
    )
