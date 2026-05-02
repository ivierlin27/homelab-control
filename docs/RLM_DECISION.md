# RLM Spike: Decision Memo

This memo records the outcome of the Recursive Language Model (RLM) research
spike. The spike's contract lives in
[docs/RLM_HARNESS.md](RLM_HARNESS.md); the implementation lives in
`apps/_shared/rlm/`; the benchmark workflows live in
`apps/_shared/rlm/benchmarks/`.

## Decision

**Partial integration, gated on a live measurement step with the strong
route enabled.**

Adopt the RLM harness as a substrate for two specific task classes
(`long_context_synthesis`, `tool_output_sandbox`), but only after we validate
live runs with the `homelab-strong` route. Until then, project agents
continue to use the existing direct + RAG routes added by the
routing-system plan.

This is intentionally not "integrate now" or "don't integrate." The
architectural argument is strong; the live evidence is still incomplete.

## Why partial, not full

Three findings from the spike argue for adopting RLM as a routing target,
not as the default:

1. The RLM harness puts a hard, auditable lid on root-model context size.
   For both synthetic workflows the root token estimate stays well under 1K
   regardless of input size, while direct grows linearly with input. That
   matches the property the routing-system plan needs for any local-strong
   workload that risks blowing 32K.
2. The structural cost of RLM is small for these workflows (a handful of
   sub-calls), but it is not free. For tasks that already fit cleanly in
   `homelab-fast` with a single shot, RLM adds latency, sub-call coordination
   overhead, and more parsing failure surface.
3. The synthetic comparison cannot tell us about *quality* differences,
   because the scripted transport returns the same prose regardless of which
   probes the harness ran. Live runs now exist, but only with the
   `homelab-fast` route; we still need evidence on the strong route before
   promoting any task class to RLM by default.

Using RLM for explicitly long-context or tool-output-heavy workloads, while
leaving the rest on direct/RAG paths, is the safe shape.

## What we measured (synthetic)

Captured on host via `python3 -m _shared.rlm.benchmarks.runner --mode
synthetic --output-dir ~/.local/state/homelab-control/rlm-spike` against
deterministic fixtures shaped like real homelab inputs.

| Workflow | Variant | Root tokens | Tokens in | Tokens out | Sub-calls |
| --- | --- | ---: | ---: | ---: | ---: |
| incident_postmortem | direct | 3919 | 3975 | 99 | 1 |
| incident_postmortem | rag | 7 | 3997 | 99 | 1 |
| incident_postmortem | rlm | 409 | 566 | 298 | 4 |
| weekly_review | direct | 10384 | 11738 | 131 | 1 |
| weekly_review | rag | 7 | 10177 | 131 | 1 |
| weekly_review | rlm | 400 | 556 | 362 | 4 |

Three takeaways, each with caveats:

- RLM keeps the root context tiny (≈400 tokens) regardless of input size.
  This is a structural property of the harness, not a model behavior, so it
  generalizes to live runs.
- RLM uses far fewer total tokens than direct because the per-handle
  metadata projection replaces the raw body. This number will *grow* under
  live runs because real models produce longer summaries than the scripted
  transport, but the relative shape (RLM small, direct large) will hold.
- RAG matches direct's quality on these synthetic cases at lower input
  cost. RAG's strength is the same property that makes it dangerous for
  aggregation tasks: it discards records the keyword scorer didn't pick.
  For weekly review, that means missing per-domain counts; RLM's `index_by`
  probe avoids that failure mode by construction.

What the synthetic numbers do **not** tell us:

- whether RLM produces *better* answers, *worse* answers, or the same
- whether sub-call schema parse failures dominate live error rate
- end-to-end wall time on real GPU under contention
- whether the planner-as-root pattern works for the homelab-fast model

## What we measured (live)

Two live runs were captured on the Alienware host via
`python3 -m _shared.rlm.benchmarks.runner --mode live` using the local
LiteLLM gateway (`http://127.0.0.1:4000/v1`).

### Live run (homelab-fast only)

Output: `~/.local/state/homelab-control/rlm-spike-live`

| Workflow | Variant | Root tokens | Tokens in | Tokens out | Sub-calls | Latency (ms) | Keyword coverage | Confidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| incident_postmortem | direct | 3919 | 7262 | 414 | 1 | 9856 | 0.6 | medium |
| incident_postmortem | rag | 7 | 7297 | 282 | 1 | 7161 | 0.6 | medium |
| incident_postmortem | rlm | 409 | 1095 | 470 | 4 | 9518 | 0.6 | medium |
| weekly_review | direct | 10384 | 15756 | 308 | 1 | 10117 | 0.6 | medium |
| weekly_review | rag | 7 | 13107 | 178 | 1 | 6675 | 0.6 | high |
| weekly_review | rlm | 400 | 1059 | 399 | 4 | 8054 | 0.8 | high |

### Live run (homelab-strong only)

Output: `~/.local/state/homelab-control/rlm-spike-live-strong`

All intents were forced to `homelab-strong` while the fast service was
stopped to keep the GPU in single-model mode.

| Workflow | Variant | Root tokens | Tokens in | Tokens out | Sub-calls | Latency (ms) | Keyword coverage | Confidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| incident_postmortem | direct | 3919 | 7262 | 223 | 1 | 6602 | 0.6 | high |
| incident_postmortem | rag | 7 | 7297 | 173 | 1 | 5527 | 0.8 | high |
| incident_postmortem | rlm | 409 | 256 | 175 | 2 | 2925 | 0.0 | low |
| weekly_review | direct | 10384 | 15756 | 233 | 1 | 11468 | 0.8 | high |
| weekly_review | rag | 7 | 13107 | 250 | 1 | 10067 | 0.6 | high |
| weekly_review | rlm | 400 | 1059 | 452 | 4 | 6053 | 0.6 | high |

The incident-postmortem RLM run aborted with
`aborted_reason=root_probe_error: sub-call schema error: sub-call response
missing non-empty 'summary'`, leaving the summary empty.

Key takeaways from the live runs:

- RLM still keeps the root context well under 1K tokens.
- RLM's total tokens are materially lower than direct, but higher than
  synthetic because the real model generates longer summaries.
- RLM latency is higher than direct for these workflows, mostly due to the
  extra sub-calls, but still in the 8–10s range for a 200-line post-mortem
  and a 120-event weekly review.
- The homelab-strong incident-postmortem RLM run aborted with a schema error
  (`sub-call response missing non-empty 'summary'`), yielding zero keyword
  coverage and low confidence. This failure must be fixed before adopting
  RLM defaults.

## Remaining gaps after live runs

The original `No connected db` errors came from the external
`model-gateway.dev-path.org` endpoint combined with placeholder API keys in
the agent env files. Pointing the agents and benchmarks to the **local**
gateway (`http://127.0.0.1:4000/v1`) with the `LITELLM_MASTER_KEY` resolved
that issue.

Two gaps remain:

- The incident-postmortem RLM run on `homelab-strong` failed with a schema
  error, so we need to tighten the sub-call prompt or response handling and
  re-run that workflow.
- The strong run forced all intents to `homelab-strong` because the fast
  route was stopped. A mixed fast+strong run would better reflect the
  eventual routing posture.

Until the schema failure is resolved, any decision about default-routing RLM
is still premature.

## What to fold into the active routing-system plan

The discovery + routing system shipped in the prior commit is the right
place to register two new task classes. The edits below are the concrete
changes to add **after the live-measurement step succeeds**, not now:

1. Extend
   [config/policies/executive-assistant-policy.yaml](../config/policies/executive-assistant-policy.yaml)
   with two new task classes:
   ```yaml
   long_context_synthesis:
     symbolic_intent: plan
     keywords: [post-mortem, weekly review, multi-day, aggregate, synthesize]
   tool_output_sandbox:
     symbolic_intent: summarize
     keywords: [tool output, large response, log dump, attachment]
   ```
2. Add `rlm-local-strong` and `rlm-local-fast` to `symbolic_routes` so
   project policies can route these task classes through the harness without
   referencing the harness module directly.
3. Update `homelab-maintainer-policy.yaml` to opt in:
   ```yaml
   route_overrides:
     long_context_synthesis: rlm-local-strong
     tool_output_sandbox: rlm-local-fast
   ```
4. Add a small adapter in
   `apps/homelab_maintainer_agent/main.py` that, when the route resolves to
   `rlm-*`, dispatches through `_shared.rlm.harness.Harness` instead of the
   direct sub-call path.
5. Extend the dashboard summary in `scripts/agent_activity_server.py` with
   per-orchestration RLM metrics: probes, sub-calls, root tokens,
   `aborted_reason`. The audit log already emits everything required.

None of these edits should land before the strong-route measurement step
records **healthy** numbers (no schema failures) in `docs/RLM_DECISION.md`.

## Pre-conditions for default adoption

Promote any task class to default-RLM **only** when:

- live-mode runs against a healthy gateway show, on real workloads,
  - root tokens stay under 1.5K across 10 runs
  - quality rubric matches or beats RAG on the same prompts
  - schema parse failures stay below 5%
  - end-to-end wall time stays under the workload's existing SLO
- the executive assistant's trust ledger captures `aborted_reason` and
  `requires_human_review` from the harness, not just from the project agent
- there is an explicit demotion rule: any task class that hits the schema
  failure ceiling falls back to direct + flag for review

If any of those don't hold, RLM stays opt-in per call instead of becoming
the default for that task class.

## What we learned that is independently useful

Even if RLM is never the default routing target, the spike produced two
durable artifacts the project-agent stack should keep:

1. The constrained probe vocabulary plus JSON-schema-only sub-call return is
   a good shape for *any* future tool-output-heavy agent. Project agents
   can use the harness as an opt-in feature for one-shot tasks ("summarize
   this 30K-line log") without committing the routing layer to it.
2. The audit format is the right shape for cross-agent debugging.
   `(orchestration_id, probe, args, tokens, latency, route, model)`
   subsumes much of what the trust ledger already records and makes it
   easier to compare orchestrations across agents.

Both belong in the substrate regardless of whether the routing layer adopts
RLM as a default.

## Next concrete actions

1. Fix the incident-postmortem schema failure on `homelab-strong`
   (investigate the sub-call prompt and response parsing; re-run just that
   workflow until `aborted_reason` is empty).
2. Run a mixed-mode benchmark (fast + strong) to mirror the intended routing
   posture, if we plan to let sub-calls use `homelab-fast` while the root
   stays `homelab-strong`.
3. If the pre-conditions above hold, land the policy + adapter edits listed
   in "What to fold into the active routing-system plan."
4. If they don't, leave the harness in place as an opt-in tool and move on;
   keep the audit format and the constrained probe vocabulary as the
   reusable artifacts.

The harness is committed, deployed where useful, tested, and inert until a
project agent or a routing rule asks for it. That is the appropriate end
state for a research spike.
