"""A2A message envelope — on-disk JSON schema for agent-to-agent requests."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class A2AEnvelope:
    """One A2A message written to a filesystem queue inbox.

    Request envelopes carry ``action`` + ``payload`` and set ``reply_to`` to
    the caller's inbox path. Reply envelopes set ``is_reply=True`` and echo
    the same ``correlation_id``.
    """

    id: str
    correlation_id: str
    caller: str
    callee: str
    action: str
    payload: dict[str, Any]
    reply_to: str
    created_at: str
    timeout_seconds: float = 300.0
    is_reply: bool = False
    success: bool | None = None
    outcome: str = ""
    reply_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> A2AEnvelope:
        return cls(
            id=str(data["id"]),
            correlation_id=str(data["correlation_id"]),
            caller=str(data["caller"]),
            callee=str(data["callee"]),
            action=str(data["action"]),
            payload=dict(data.get("payload") or {}),
            reply_to=str(data.get("reply_to", "")),
            created_at=str(data.get("created_at") or _utc_now_iso()),
            timeout_seconds=float(data.get("timeout_seconds", 300.0)),
            is_reply=bool(data.get("is_reply", False)),
            success=data.get("success") if data.get("success") is None else bool(data["success"]),
            outcome=str(data.get("outcome", "")),
            reply_payload=dict(data.get("reply_payload") or {}),
        )

    @classmethod
    def from_path(cls, path: Path) -> A2AEnvelope:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def new_request(
        cls,
        *,
        caller: str,
        callee: str,
        action: str,
        payload: dict[str, Any],
        reply_to: str,
        timeout_seconds: float = 300.0,
        correlation_id: str | None = None,
        envelope_id: str | None = None,
    ) -> A2AEnvelope:
        return cls(
            id=envelope_id or str(uuid.uuid4()),
            correlation_id=correlation_id or str(uuid.uuid4()),
            caller=caller,
            callee=callee,
            action=action,
            payload=payload,
            reply_to=reply_to,
            created_at=_utc_now_iso(),
            timeout_seconds=timeout_seconds,
            is_reply=False,
        )

    @classmethod
    def new_reply(
        cls,
        request: A2AEnvelope,
        *,
        success: bool,
        outcome: str,
        reply_payload: dict[str, Any] | None = None,
    ) -> A2AEnvelope:
        return cls(
            id=str(uuid.uuid4()),
            correlation_id=request.correlation_id,
            caller=request.callee,
            callee=request.caller,
            action=request.action,
            payload={},
            reply_to="",
            created_at=_utc_now_iso(),
            timeout_seconds=request.timeout_seconds,
            is_reply=True,
            success=success,
            outcome=outcome,
            reply_payload=dict(reply_payload or {}),
        )
