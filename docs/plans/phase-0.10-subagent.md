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
| `routing.py` | `RoutePolicy`, route → model map (`homelab-fast` / `homelab-strong`) |
| `spawner.py` | `spawn_subagent()`, `SubagentResult` |
| `errors.py` | `SubagentRoleError`, `SubagentRouteError` |

## Public API

```python
from apps._shared.subagent import RoutePolicy, spawn_subagent

result = spawn_subagent(
    "tool-runner",
    "Summarize ERROR lines from handle log-1.",
    ["grep"],
    route="local-fast",
    parent_correlation_id=parent_task_id,
    audit_path=state_dir / "trust-ledger.jsonl",
    route_policy=RoutePolicy(allowed_routes=frozenset({"local-fast", "local-strong"})),
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

| Route | Model alias | Default roles |
|-------|-------------|---------------|
| `local-fast` | `homelab-fast` | researcher, tool-runner, verifier |
| `local-strong` | `homelab-strong` | planner |
| `cloud-frontier` | `cloud-frontier` | only when `RoutePolicy.allow_cloud` |

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
