"""agent:finance — advisory finance agent (MVP-B scope).

See docs/plans/phase-1-finance.md for the full design. The agent runs
inside the agent-finance sandbox (rootless Podman, no network) and is
strictly advisory: every ledger mutation is operator-initiated or
operator-confirmed.

Sprint F2 ships the skeleton only — a `status` subcommand. Subsequent
sprints add `ingest`, `categorize`, `advise`.
"""

__version__ = "0.1.0"
