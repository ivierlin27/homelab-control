"""Check protocol + state-transition engine."""

from __future__ import annotations

import enum
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable


class Status(str, enum.Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    # Reserved for transient failures (e.g. SSH timeout) — counted as
    # healthy for alerting purposes until N consecutive UNKNOWN runs in
    # a row (see ``StateStore.transitions``).
    UNKNOWN = "unknown"


@dataclass
class CheckResult:
    name: str               # stable identifier, e.g. "audit:agent-homelab-maintainer"
    status: Status
    detail: str = ""        # human-readable summary; goes into Discord post + runbook lookup
    runbook: str | None = None      # path or symptom-table key, surfaced in alerts
    metrics: dict = field(default_factory=dict)   # optional numeric extras

    def as_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# A Check is any sync callable returning a list of CheckResults. We allow
# many results per check because some sources (e.g. "all systemd timers")
# naturally produce a fan-out of independent statuses.
Check = Callable[[], list[CheckResult]]


@dataclass
class Transition:
    name: str
    previous: Status
    current: Status
    detail: str
    runbook: str | None
    ts: float

    def as_dict(self) -> dict:
        d = asdict(self)
        d["previous"] = self.previous.value
        d["current"] = self.current.value
        return d


class StateStore:
    """Persisted last-seen status per check name, plus consecutive-unknown counter.

    File schema (newline-delimited JSON is overkill; a single JSON dict is
    fine because the file is rewritten atomically each run):

      {
        "by_name": {
          "<name>": {"status": "...", "detail": "...", "unknown_streak": 0, "since_ts": 1234.5}
        }
      }
    """

    def __init__(self, path: str | Path, *, unknown_alert_after: int = 3):
        self.path = Path(path).expanduser()
        self.unknown_alert_after = unknown_alert_after
        self.by_name: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.by_name = data.get("by_name", {}) or {}
        except (OSError, json.JSONDecodeError):
            self.by_name = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"by_name": self.by_name}, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def transitions(self, results: list[CheckResult], *, now: float | None = None) -> list[Transition]:
        """Apply ``results``, return the subset that represents an alertable transition.

        Rules:
        - HEALTHY ↔ UNHEALTHY: always a transition.
        - HEALTHY → UNKNOWN: hold (no alert) until ``unknown_alert_after``
          consecutive UNKNOWN runs, then treat as UNHEALTHY.
        - UNKNOWN → HEALTHY: resets the streak silently if we were never alerted.
        - First sighting of a name: emit an "initial" transition from UNKNOWN
          to the observed status so the operator sees the inventory once.
        """
        now = now if now is not None else time.time()
        out: list[Transition] = []
        for r in results:
            prev_entry = self.by_name.get(r.name)
            prev_status = Status(prev_entry["status"]) if prev_entry else Status.UNKNOWN
            unknown_streak = (prev_entry or {}).get("unknown_streak", 0)

            current = r.status
            effective_current = current
            if current is Status.UNKNOWN:
                unknown_streak += 1
                if unknown_streak >= self.unknown_alert_after and prev_status is Status.HEALTHY:
                    effective_current = Status.UNHEALTHY
                else:
                    effective_current = prev_status  # hold the previous status
            else:
                unknown_streak = 0

            # Decide if it's a transition worth alerting on.
            if prev_entry is None:
                # First sighting → record it but only alert if observed status is bad
                if effective_current is Status.UNHEALTHY:
                    out.append(Transition(
                        name=r.name, previous=Status.UNKNOWN, current=Status.UNHEALTHY,
                        detail=r.detail, runbook=r.runbook, ts=now,
                    ))
            elif prev_status != effective_current and effective_current is not Status.UNKNOWN:
                out.append(Transition(
                    name=r.name, previous=prev_status, current=effective_current,
                    detail=r.detail, runbook=r.runbook, ts=now,
                ))

            self.by_name[r.name] = {
                "status": effective_current.value,
                "detail": r.detail,
                "unknown_streak": unknown_streak,
                "since_ts": (prev_entry or {}).get("since_ts", now)
                            if prev_entry and prev_status == effective_current
                            else now,
            }
        return out
