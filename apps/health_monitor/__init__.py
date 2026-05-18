"""Periodic health monitor (Phase 0-ops sprint).

Runs every 15 minutes on Alienware. Polls a small set of checks
(service health endpoints, audit-chain integrity, systemd timer
last-run status, restic snapshot freshness) and **alerts on state
transitions only** — healthy→unhealthy posts a Discord ping, the
recovery posts a one-line "back" message. Steady-state silence.

Designed so that adding a new check is one ``Check`` callable in
``checks.py``; nothing else needs to change.
"""

from .core import Check, CheckResult, Status

__all__ = ["Check", "CheckResult", "Status"]
