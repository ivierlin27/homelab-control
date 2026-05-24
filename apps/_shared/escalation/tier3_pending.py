"""Track Tier-3 Discord posts until acknowledged or follow-up DM is sent."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/escalation"


def _is_discord_snowflake(value: str) -> bool:
    return value.isdigit() and 17 <= len(value) <= 20


@dataclass
class Tier3PendingRecord:
    message_id: str
    channel_id: str
    task_class: str
    principal: str
    urgent: bool
    posted_at: str
    dm_after_seconds: int
    dm_sent: bool = False
    acknowledged: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def pending_path(state_dir: Path | None = None) -> Path:
    root = (state_dir or DEFAULT_STATE_DIR).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / "tier3-pending.jsonl"


def register_pending(
    record: Tier3PendingRecord,
    *,
    state_dir: Path | None = None,
) -> None:
    path = pending_path(state_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")


def load_pending_records(
    *,
    state_dir: Path | None = None,
) -> list[Tier3PendingRecord]:
    path = pending_path(state_dir)
    if not path.is_file():
        return []
    latest: dict[str, Tier3PendingRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        rec = Tier3PendingRecord(
            message_id=str(data["message_id"]),
            channel_id=str(data["channel_id"]),
            task_class=str(data.get("task_class", "")),
            principal=str(data.get("principal", "")),
            urgent=bool(data.get("urgent", False)),
            posted_at=str(data.get("posted_at", "")),
            dm_after_seconds=int(data.get("dm_after_seconds", 14400)),
            dm_sent=bool(data.get("dm_sent", False)),
            acknowledged=bool(data.get("acknowledged", False)),
        )
        latest[rec.message_id] = rec
    return list(latest.values())


def _rewrite_pending(records: list[Tier3PendingRecord], *, state_dir: Path | None = None) -> None:
    path = pending_path(state_dir)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")


def update_pending(
    message_id: str,
    *,
    dm_sent: bool | None = None,
    acknowledged: bool | None = None,
    state_dir: Path | None = None,
) -> None:
    records = load_pending_records(state_dir=state_dir)
    changed = False
    for index, record in enumerate(records):
        if record.message_id != message_id:
            continue
        if dm_sent is not None:
            record.dm_sent = dm_sent
            changed = True
        if acknowledged is not None:
            record.acknowledged = acknowledged
            changed = True
        records[index] = record
    if changed:
        _rewrite_pending(records, state_dir=state_dir)


def is_message_acknowledged(
    token: str,
    channel_id: str,
    message_id: str,
    *,
    fetch_message: Any | None = None,
) -> bool:
    """True if the Discord message has a thread with replies or a ✅ reaction."""
    if fetch_message is None:
        from .tier3_discord import fetch_channel_message

        fetch_message = fetch_channel_message

    message = fetch_message(token, channel_id, message_id)
    thread = message.get("thread")
    if isinstance(thread, Mapping) and int(thread.get("message_count", 0) or 0) > 0:
        return True
    reactions = message.get("reactions") or []
    for reaction in reactions:
        if not isinstance(reaction, Mapping):
            continue
        emoji = reaction.get("emoji") or {}
        name = str(emoji.get("name", ""))
        if name in {"✅", "white_check_mark", "☑️"} and int(reaction.get("count", 0) or 0) > 0:
            return True
    return False


def process_pending_followups(
    *,
    token: str,
    dm_user_ids: list[str],
    state_dir: Path | None = None,
    now_monotonic: float | None = None,
    send_dm: Any | None = None,
    check_ack: Any | None = None,
) -> list[dict[str, Any]]:
    """Acknowledge or DM for overdue Tier-3 posts. Returns action summaries."""
    from .tier3_discord import format_tier3_message, send_user_dm

    if send_dm is None:
        send_dm = send_user_dm
    if check_ack is None:
        check_ack = is_message_acknowledged

    results: list[dict[str, Any]] = []

    for record in load_pending_records(state_dir=state_dir):
        if record.acknowledged:
            continue
        if not _is_discord_snowflake(record.channel_id) or not _is_discord_snowflake(
            record.message_id
        ):
            update_pending(record.message_id, acknowledged=True, state_dir=state_dir)
            results.append(
                {
                    "message_id": record.message_id,
                    "action": "skipped_invalid_ids",
                }
            )
            continue
        if record.urgent and not record.dm_sent:
            overdue = True
        else:
            try:
                posted = datetime.fromisoformat(record.posted_at.replace("Z", "+00:00"))
            except ValueError:
                posted = datetime.now(timezone.utc)
            age = (datetime.now(timezone.utc) - posted.astimezone(timezone.utc)).total_seconds()
            overdue = age >= record.dm_after_seconds

        if check_ack(token, record.channel_id, record.message_id):
            update_pending(record.message_id, acknowledged=True, state_dir=state_dir)
            results.append({"message_id": record.message_id, "action": "acknowledged"})
            continue

        if not overdue or record.dm_sent:
            continue

        content = (
            f"**Tier 3 follow-up** — `{record.task_class}` was posted to #approvals "
            f"and is still unacknowledged.\n"
            f"Message id: `{record.message_id}`"
        )
        dm_results = []
        for user_id in dm_user_ids:
            try:
                dm_results.append(send_dm(token, user_id, content))
            except Exception as exc:  # noqa: BLE001
                dm_results.append({"user_id": user_id, "error": str(exc)})
        update_pending(record.message_id, dm_sent=True, state_dir=state_dir)
        results.append(
            {
                "message_id": record.message_id,
                "action": "dm_sent",
                "dm_results": dm_results,
            }
        )
    return results
