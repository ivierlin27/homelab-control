"""Maintenance-mode lock for planned outages.

A small CLI + library that writes a JSON sentinel file. Other services
(health monitor first; agents later) consult this file to decide
whether to alert on failures during a planned window.

Design choices:
- **File-based, not env-based**: env vars can't be changed without a
  restart of the service that read them. A lock file is checked on
  every iteration of the consumer.
- **Time-bound, not boolean**: every lock has a hard ``until`` deadline
  so we can never accidentally leave maintenance mode active forever.
- **Scoped, not global** (optional): you can declare which subset of
  checks/services are in maintenance (e.g. ``proxmox,memory-engine``)
  so alerts on unrelated systems still fire. Empty scope = global.
- **Audited**: every start/end writes to the same hash-chained ledger
  the health monitor uses, so we have a record of every planned outage.
"""

from .lock import (
    DEFAULT_LOCK_PATH,
    MaintenanceLock,
    end_maintenance,
    is_active,
    load_lock,
    start_maintenance,
)

__all__ = [
    "DEFAULT_LOCK_PATH",
    "MaintenanceLock",
    "end_maintenance",
    "is_active",
    "load_lock",
    "start_maintenance",
]
