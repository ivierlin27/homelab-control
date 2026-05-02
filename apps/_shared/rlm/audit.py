"""JSONL audit log for RLM orchestrations.

Every probe and sub-call writes one event. The trail is what the eventual
decision memo and dashboard read; it must be self-describing and stable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[dict[str, Any]] = []
        self._step = 0

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        self._step += 1
        enriched = {
            "step": self._step,
            "logged_at": utc_now(),
            **event,
        }
        with self.path.open("a") as handle:
            handle.write(json.dumps(enriched, sort_keys=True, default=str) + "\n")
        self._events.append(enriched)
        return enriched

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def totals(self) -> dict[str, Any]:
        tokens_in = sum(int(event.get("tokens_in", 0) or 0) for event in self._events)
        tokens_out = sum(int(event.get("tokens_out", 0) or 0) for event in self._events)
        latency_ms = sum(int(event.get("latency_ms", 0) or 0) for event in self._events)
        probes = sum(1 for event in self._events if event.get("kind") == "probe")
        subcalls = sum(1 for event in self._events if event.get("kind") == "subcall")
        return {
            "events": len(self._events),
            "probes": probes,
            "subcalls": subcalls,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "latency_ms": latency_ms,
        }
