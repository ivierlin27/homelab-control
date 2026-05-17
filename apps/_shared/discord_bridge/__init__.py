"""Shared Discord bridge for agent bots (Phase 0.7).

Each agent's bridge process imports ``run_bridge`` and provides an async
``handler`` that maps incoming :class:`MessageContext` to a reply string
(or ``None`` to remain silent). The bridge handles:

- Discord client setup with the correct intents for our private-channel design
  (``message_content`` + ``members``; ``dm_messages`` for DM reachability).
- Inbound filtering: drop bot authors; enforce ``DISCORD_ALLOWED_USER_IDS``
  and ``DISCORD_ALLOWED_CHANNEL_IDS`` allowlists; require either DM, mention,
  or the agent's prefix command to dispatch.
- Audit logging via :mod:`apps._shared.audit` — each inbound, outbound, and
  handler error is written to the agent's hash-chained trust ledger.
- Reply chunking at 1800 chars so long answers fit Discord's 2000-char limit.

Handlers should be small and synchronous-friendly; for heavy work delegate to
``asyncio.to_thread`` inside the handler so the event loop stays responsive.
"""

from .bridge import BridgeConfig, MessageContext, run_bridge

__all__ = ["BridgeConfig", "MessageContext", "run_bridge"]
