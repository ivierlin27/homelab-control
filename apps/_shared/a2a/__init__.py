"""Agent-to-agent message bus (Phase 0.9).

Filesystem-backed queues with reply semantics, registry-driven routing,
and manifest ``a2a.allowed_callees`` enforcement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from ..audit import AuditLog

from .envelope import A2AEnvelope
from .errors import A2AError, A2ANotAllowedError, A2ARoutingError, A2ATimeoutError
from .queue import enqueue, enqueue_envelope, enqueue_inbox, ensure_dirs, poll_reply, write_json
from .routing import assert_callee_allowed, resolve_inbox, resolve_queue_dir

__all__ = [
    "A2AEnvelope",
    "A2AError",
    "A2ANotAllowedError",
    "A2ARoutingError",
    "A2ATimeoutError",
    "AskResult",
    "ask_agent",
    "await_reply",
    "enqueue",
    "enqueue_inbox",
    "ensure_dirs",
    "make_tier2_executive_handler",
    "poll_reply",
    "reply_to_caller",
    "resolve_inbox",
    "resolve_queue_dir",
    "write_json",
]


@dataclass(frozen=True)
class AskResult:
    """Returned by :func:`ask_agent` — use :func:`await_reply` to collect the response."""

    correlation_id: str
    envelope_id: str
    job_path: Path
    callee: str
    caller: str


def ask_agent(
    caller: str,
    callee: str,
    action: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 300.0,
    audit: AuditLog | None = None,
    reply_to: str | None = None,
) -> AskResult:
    """Enqueue a request to *callee* and return immediately.

    Raises :class:`A2ANotAllowedError` if the caller manifest forbids *callee*.
  """
    assert_callee_allowed(caller, callee)

    caller_inbox = resolve_inbox(caller)
    callee_queue = resolve_queue_dir(callee)
    reply_path = reply_to or str(caller_inbox)

    envelope = A2AEnvelope.new_request(
        caller=caller,
        callee=callee,
        action=action,
        payload=payload,
        reply_to=reply_path,
        timeout_seconds=timeout_seconds,
    )
    job_path = enqueue_envelope(callee_queue, envelope)

    if audit is not None:
        audit.append(
            {
                "event": "a2a_ask",
                "caller": caller,
                "callee": callee,
                "action": action,
                "correlation_id": envelope.correlation_id,
                "envelope_id": envelope.id,
                "job_path": str(job_path),
                "reply_to": reply_path,
            }
        )

    return AskResult(
        correlation_id=envelope.correlation_id,
        envelope_id=envelope.id,
        job_path=job_path,
        callee=callee,
        caller=caller,
    )


def reply_to_caller(
    request: A2AEnvelope,
    *,
    success: bool,
    outcome: str,
    payload: dict[str, Any] | None = None,
    audit: AuditLog | None = None,
) -> Path:
    """Write a reply envelope to the request's ``reply_to`` inbox."""
    if not request.reply_to:
        raise A2ARoutingError("request envelope has no reply_to path")

    reply = A2AEnvelope.new_reply(
        request,
        success=success,
        outcome=outcome,
        reply_payload=payload,
    )
    reply_target = Path(request.reply_to).expanduser().resolve()
    # ask_agent sets reply_to to the caller inbox dir (may not exist yet).
    if reply_target.suffix == ".json":
        inbox = reply_target.parent
    else:
        inbox = reply_target
    name = f"a2a-reply-{reply.correlation_id}.json"
    path = enqueue_inbox(inbox, name, reply.to_dict())

    if audit is not None:
        audit.append(
            {
                "event": "a2a_reply",
                "caller": reply.caller,
                "callee": reply.callee,
                "correlation_id": reply.correlation_id,
                "success": success,
                "outcome": outcome,
                "reply_path": str(path),
            }
        )

    return path


def await_reply(
    correlation_id: str,
    reply_queue: Path,
    *,
    timeout_seconds: float = 300.0,
    poll_interval: float = 2.0,
) -> A2AEnvelope | None:
    """Poll *reply_queue* (inbox dir) for a matching reply; ``None`` on timeout."""
    inbox = reply_queue
    if inbox.name != "inbox":
        inbox = reply_queue / "inbox" if (reply_queue / "inbox").is_dir() else reply_queue
    result = poll_reply(
        correlation_id,
        inbox,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )
    return result


def make_tier2_executive_handler(
    *,
    caller: str | None = None,
    reply_queue_dir: Path | None = None,
    ask_timeout_seconds: float = 120.0,
    poll_interval: float = 0.1,
):
    """Factory for an escalation Tier-2 handler that asks ``agent:executive``.

    Returns a callable matching ``Dispatcher``'s ``Tier2Fn`` signature:
    ``(envelope) -> (success, payload, reason)``.

    Requires the executive worker to handle ``help_request`` A2A jobs (see
    ``apps/executive_agent/help_request.py``).
    """
    from typing import Tuple

    def tier2_executive_reroute(
        envelope: Mapping[str, Any],
    ) -> Tuple[bool, Any, str]:
        principal = caller or os.environ.get("AGENT_PRINCIPAL", "")
        if not principal:
            return (False, None, "AGENT_PRINCIPAL not set")

        reply_root = reply_queue_dir or resolve_queue_dir(principal)
        reply_inbox = reply_root / "inbox"

        try:
            result = ask_agent(
                caller=principal,
                callee="agent:executive",
                action="help_request",
                payload=dict(envelope),
                timeout_seconds=ask_timeout_seconds,
                reply_to=str(reply_inbox),
            )
        except (A2ANotAllowedError, A2ARoutingError) as exc:
            return (False, None, str(exc))

        reply = await_reply(
            result.correlation_id,
            reply_inbox,
            timeout_seconds=ask_timeout_seconds,
            poll_interval=poll_interval,
        )
        if reply is None:
            return (
                False,
                None,
                f"executive did not reply within {ask_timeout_seconds}s "
                f"(correlation_id={result.correlation_id})",
            )
        if reply.success:
            return (True, reply.reply_payload, reply.outcome or "executive resolved")
        return (False, reply.reply_payload, reply.outcome or "executive declined")

    return tier2_executive_reroute
