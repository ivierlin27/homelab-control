"""Deterministic fixtures and a scripted transport for the benchmark runner.

The fixtures encode the *shape* of real homelab inputs without secrets so the
spike can run on any host — including the Mac during development. Live runs
on the Alienware host should pass real ledgers and logs into the benchmark
input loaders instead.
"""

from __future__ import annotations

import json
from typing import Any

from .workflow_a_postmortem import PostmortemInputs
from .workflow_b_weekly_review import WeeklyReviewInputs


def synthetic_postmortem_inputs(scale: int = 200) -> PostmortemInputs:
    """Create deterministic incident inputs of approximately ``scale`` log lines."""
    log_lines = []
    for i in range(scale):
        ts = f"2026-04-29T{i // 60:02d}:{i % 60:02d}:00Z"
        if i in {12, 47, 91, 154}:
            log_lines.append(f"{ts} ERROR vllm cuda OOM allocator failed at request {i}")
        elif i in {18, 53, 99, 162}:
            log_lines.append(f"{ts} ERROR gateway upstream timeout to vllm-1 after 30s")
        elif i % 15 == 0:
            log_lines.append(f"{ts} WARN gateway latency spike p95=4500ms route=local-strong")
        else:
            log_lines.append(f"{ts} INFO gateway request route=local-fast latency_ms=320")

    planka_thread = [
        {"actor": "kevin", "ts": "2026-04-29T01:00:00Z", "kind": "comment", "text": "vLLM gateway latency is climbing again."},
        {"actor": "agent:homelab-maintainer", "ts": "2026-04-29T01:05:00Z", "kind": "comment", "text": "Triaging. Errors dominated by cuda OOM events."},
        {"actor": "kevin", "ts": "2026-04-29T01:30:00Z", "kind": "comment", "text": "Restart vLLM; check 3090 memory headroom."},
        {"actor": "agent:homelab-maintainer", "ts": "2026-04-29T02:00:00Z", "kind": "comment", "text": "Restart helped briefly. Latency p95 returned to baseline 320ms but errors recurred two hours later."},
        {"actor": "kevin", "ts": "2026-04-29T03:00:00Z", "kind": "comment", "text": "Suspect recent context-window bump consumed extra GPU memory; need to validate."},
    ]

    memory_notes = [
        {
            "record_key": "homelab.incidents.vllm.2026-03-12",
            "principal": "agent:homelab-maintainer",
            "text": "Prior similar incident: cuda OOM caused by simultaneous requests with high context. Mitigation: cap concurrency.",
        },
        {
            "record_key": "homelab.runbooks.vllm.gpu_memory",
            "principal": "agent:homelab-maintainer",
            "text": "Runbook: monitor nvidia-smi memory; restart on >90% sustained for 5 minutes.",
        },
        {
            "record_key": "homelab.config.vllm.context_window",
            "principal": "agent:homelab-maintainer",
            "text": "vLLM context window increased from 16K to 32K on 2026-04-22 to support longer code review prompts.",
        },
    ]

    return PostmortemInputs(log_lines=log_lines, planka_thread=planka_thread, memory_notes=memory_notes)


def synthetic_weekly_review_inputs(scale: int = 120) -> WeeklyReviewInputs:
    """Create deterministic weekly-review inputs of approximately ``scale`` events."""
    domains = ["homelab", "learning", "products", "finance"]
    routes = ["local-fast", "local-strong", "cloud-frontier"]
    decisions = ["allowed", "allowed", "allowed", "blocked", "escalated"]
    trust_events = []
    for i in range(scale):
        domain = domains[i % len(domains)]
        route = routes[i % len(routes)]
        decision = decisions[i % len(decisions)]
        trust_events.append(
            {
                "occurred_at": f"2026-04-{24 + (i // 30):02d}T{(i * 11) % 24:02d}:00:00Z",
                "principal": "agent:executive" if i % 5 else "agent:homelab-maintainer",
                "project_domain": domain,
                "task_class": ["summarize", "classify", "code-review-small", "architecture-synthesis"][i % 4],
                "route": route,
                "decision": decision,
                "requires_human_review": decision in {"blocked", "escalated"},
            }
        )

    planka_transitions = [
        {"card_id": f"card-{i}", "from_list": "Inbox", "to_list": "Plan-Ready" if i % 4 else "Done", "actor": "agent:executive", "ts": f"2026-04-{24 + i // 5:02d}T09:00:00Z"}
        for i in range(40)
    ]
    planka_transitions.extend(
        [
            {"card_id": f"card-{200 + i}", "from_list": "Plan-Ready", "to_list": "Stalled", "actor": "kevin", "ts": "2026-04-30T18:00:00Z"}
            for i in range(3)
        ]
    )

    memory_writes = []
    for i in range(60):
        memory_writes.append(
            {
                "occurred_at": f"2026-04-{24 + i // 15:02d}T11:00:00Z",
                "principal": ["agent:executive", "agent:homelab-maintainer", "agent:author"][i % 3],
                "record_key": ["homelab.notes.daily", "homelab.intake.idea", "intake.scratch.url"][i % 3],
                "source": ["chat", "intake-raw", "weekly-review"][i % 3],
                "classification": ["existing_project", "new_project_candidate", "scratch"][i % 3],
            }
        )

    return WeeklyReviewInputs(trust_events=trust_events, planka_transitions=planka_transitions, memory_writes=memory_writes)


class ScriptedTransport:
    """Deterministic transport that returns plausible JSON-shaped responses.

    Used so the runner can produce side-by-side comparison records without a
    live gateway. Real numbers come from on-host runs; this transport exists
    to (a) make the runner exercisable on the Mac, (b) provide stable test
    fixtures, and (c) let us catch schema regressions before burning GPU.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, intent: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        user_message = next(
            (msg.get("content", "") for msg in payload.get("messages", []) if msg.get("role") == "user"),
            "",
        )
        try:
            user_blob = json.loads(user_message)
        except json.JSONDecodeError:
            user_blob = {"sub_prompt": user_message}
        sub_prompt = str(user_blob.get("sub_prompt", "")).lower()
        context = user_blob.get("context", {})

        if "post-mortem" in sub_prompt or "incident" in sub_prompt or "remediation" in sub_prompt:
            summary = (
                "Timeline: cuda OOM errors clustered around requests 12 and 47, then again at 91 and 154; "
                "memory gateway showed latency spikes between OOM events. Contributing factors: vllm context "
                "window bump from 16K to 32K, missing concurrency cap, no automated GPU memory alert. "
                "Remediation: cap vllm concurrency, add nvidia-smi memory alert at 85%, schedule a runbook "
                "review for the 32K context-window change."
            )
            citations = [{"handle": "service-log", "range": [12, 47]}, {"handle": "memory-notes", "range": [0, 1]}]
            confidence = "high"
        elif "weekly executive review" in sub_prompt or "per-domain" in sub_prompt:
            summary = (
                "Homelab dominated activity with most allowed local-fast routes; one blocked secrets attempt "
                "and one escalated architecture-synthesis to cloud-frontier with required review. Plan-Ready "
                "received the bulk of card transitions; three cards stalled. Memory writes were balanced across "
                "executive and homelab-maintainer; intake.scratch.url accumulated faster than promotions. "
                "Open risks: cloud route usage unreviewed and stalled cards. Operator review next week: "
                "cloud-frontier audit, stalled cards, intake-scratch promotion backlog."
            )
            citations = [{"handle": "trust-events", "range": [0, 5]}, {"handle": "planka-transitions", "range": [40, 43]}]
            confidence = "medium"
        elif "summarize" in sub_prompt and "highest-severity" in sub_prompt:
            summary = "Four cuda OOM events at requests 12, 47, 91, 154. Four upstream gateway timeouts shortly after each."
            citations = [{"handle": "service-log", "range": [12, 162]}]
            confidence = "high"
        elif "summarize" in sub_prompt and "human discussion" in sub_prompt:
            summary = "Operator and homelab-maintainer discussed restart, GPU memory headroom, and the recent context window change as a likely root cause."
            citations = [{"handle": "planka-thread", "range": [0, 5]}]
            confidence = "medium"
        elif "prior incidents" in sub_prompt or "recurring failure" in sub_prompt:
            summary = "Prior cuda OOM on 2026-03-12 mitigated by concurrency cap; runbook says alert at 90% GPU memory."
            citations = [{"handle": "memory-notes", "range": [0, 2]}]
            confidence = "medium"
        elif "per-domain decisions" in sub_prompt or "route usage" in sub_prompt:
            summary = "Homelab made up the bulk of activity, with allowed local-fast and local-strong routes; finance saw blocked actions; cloud-frontier saw a small number of escalated calls requiring review."
            citations = [{"handle": "trust-events", "range": [0, 10]}]
            confidence = "high"
        elif "plan-ready" in sub_prompt or "stalled" in sub_prompt:
            summary = "Most transitions moved to Done or Plan-Ready; three cards stalled and need follow-up."
            citations = [{"handle": "planka-transitions", "range": [0, 5]}]
            confidence = "medium"
        elif "memory writes" in sub_prompt or "record_key" in sub_prompt:
            summary = "Most writes were homelab.notes.daily and homelab.intake.idea; intake.scratch.url is growing faster than projects are being promoted."
            citations = [{"handle": "memory-writes", "range": [0, 5]}]
            confidence = "medium"
        else:
            summary = "Generic summary placeholder."
            citations = []
            confidence = "low"

        response_payload = {
            "summary": summary,
            "citations": citations,
            "confidence": confidence,
            "open_questions": [],
        }
        usage_in = max(50, len(json.dumps(context, default=str)) // 4)
        usage_out = max(50, len(summary) // 4)
        self.calls.append(
            {
                "intent": intent,
                "model": model,
                "tokens_in": usage_in,
                "tokens_out": usage_out,
            }
        )
        return {
            "choices": [{"message": {"content": json.dumps(response_payload)}}],
            "usage": {"prompt_tokens": usage_in, "completion_tokens": usage_out},
        }
