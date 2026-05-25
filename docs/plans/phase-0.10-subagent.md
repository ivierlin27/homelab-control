# Phase 0.10: Sub-agent spawner

**Status: SHIPPED 2026-05-24** (`phase-1-finance`).

## Goal

Let parent agents delegate focused LLM work without growing the parent context.
The parent receives a **distilled** JSON result; the **full transcript** lives
only in the hash-chained audit log under the parent's `correlation_id`.

## Module: `apps/_shared/subagent/`

| File | Role |
|------|------|
| `personas.py` | Built-in roles: `researcher`, `planner`, `tool-runner`, `verifier` |
| `routing.py` | `RoutePolicy`; logical `local` → `homelab-strong-long` via `gateway_routes` |
| `spawner.py` | `spawn_subagent()`, `SubagentResult` |
| `errors.py` | `SubagentRoleError`, `SubagentRouteError` |

## Public API

```python
from apps._shared.subagent import RoutePolicy, spawn_subagent

result = spawn_subagent(
    "tool-runner",
    "Summarize ERROR lines from handle log-1.",
    ["grep"],
    route="local",
    parent_correlation_id=parent_task_id,
    audit_path=state_dir / "trust-ledger.jsonl",
    route_policy=RoutePolicy(allowed_routes=frozenset({"local"})),
    context={"handles": ["log-1"]},
)
# Parent sees only:
print(result.summary, result.confidence)
```

### Audit events

- `subagent_spawn` — role, route, tools, prompt preview
- `subagent_complete` — full `transcript` + `distilled` payload
- `subagent_failed` — on gateway/schema errors

Replay: `python -m apps._shared.audit verify <ledger>` then filter
`parent_correlation_id` / `subagent_correlation_id`.

## Routes

Alienware runs a **single** vLLM backend (`homelab-strong-long` on both 3090s).
Logical route `local` resolves to that alias. Legacy names `local-fast` /
`local-strong` (and gateway aliases `homelab-fast` / `homelab-strong`) still
work but all hit the same upstream — see `apps/_shared/gateway_routes.py`.

| Route | Gateway model | Notes |
|-------|---------------|-------|
| `local` | `homelab-strong-long` | default for all personas |
| `local-fast`, `local-strong` | `homelab-strong-long` | legacy; normalized to `local` |
| `cloud-frontier` | `cloud-frontier` | only when `RoutePolicy.allow_cloud` |

Override with env `HOMELAB_LOCAL_MODEL` if the canonical alias changes.

Parents may pass `RoutePolicy.from_manifest_routing(manifest.get("routing"))`
when manifests add `subagent_allowed_routes` / `allow_cloud_subagent`.

## Monitoring

Sub-agent runs appear in the master dashboard **audit** tile (trust ledger SSE)
as `subagent_*` events. No separate tile in 0.10 — use audit + `verify-ledger`.

## Not in 0.10 (follow-up)

- Wiring `spawn_subagent` into executive / maintainer / author handlers (call sites)
- Sandbox-isolated tool execution inside `tool-runner` (tools are metadata only)
- Sub-agent rows on the A2A dashboard tile
- Manifest schema formalization for `routing.subagent_allowed_routes` in registry loader

## Tests

```bash
pytest apps/_shared/subagent/test_subagent.py -q
```
