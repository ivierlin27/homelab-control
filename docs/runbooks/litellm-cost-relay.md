# LiteLLM cost/latency relay → memory-engine

**Owner:** `agent:homelab-maintainer` (operational), Kevin (n8n workflow + PG schema).
**Phase:** 0.6.
**Components:** `apps/_shared/litellm_callbacks/` (write side, in the
gateway container) + `apps/litellm_cost_relay/` (ship side, on Alienware).

## What this does

Every call through the `homelab-model-gateway` LiteLLM proxy gets one
JSON line written to
`~/.local/state/homelab-control/llm-calls/llm-calls.jsonl` on the
Alienware host. The `alienware-litellm-cost-relay.service` daemon
tails that file and POSTs batches to an HTTP endpoint
(`LLM_COST_RELAY_URL`) — typically an n8n webhook on the memory-engine
LXC that inserts each record into Postgres.

The pipeline is **append-only and durable**: the callback never blocks
or fails into the LLM request path, and the relay only advances its
byte offset after a successful POST. memory-engine can be offline for
hours without data loss.

## Record schema (v1)

```json
{
  "schema": 1,
  "ts": 1779040417.639343,
  "status": "success",
  "model": "homelab-strong-long-vllm",
  "agent_principal": "agent:executive",
  "request_id": "chatcmpl-922825ca99c6d968",
  "prompt_tokens": 14,
  "completion_tokens": 2,
  "total_tokens": 16,
  "cost_usd": 0.0,
  "latency_ms": 673,
  "user": null,
  "error": null
}
```

`status` is `"success"` or `"failure"`. `cost_usd` is `0.0` for our
self-hosted vLLM/LM-Studio models — LiteLLM only auto-computes costs
for catalogued hosted models. Token counts are the real signal locally.

`agent_principal` is sourced from the `x-agent-principal` HTTP header
that `apps/_shared/rlm/subcall.py` forwards based on the calling
agent's `AGENT_PRINCIPAL` env var. Defaults to `"unknown"`.

## Relay payload

POST body:
```json
{
  "schema": 1,
  "records": [<record>, <record>, ...]
}
```

The receiver must respond `2xx` for the batch to be acked. Any other
response or a network error → exponential backoff (1s → 60s), the
offset is NOT advanced, and the relay retries with the same batch on
its next iteration.

## Setup on memory-engine LXC

1. Add a `llm_calls` table to the `memory` database:

   ```sql
   CREATE TABLE IF NOT EXISTS llm_calls (
     id              BIGSERIAL PRIMARY KEY,
     ts              TIMESTAMPTZ NOT NULL,
     status          TEXT NOT NULL,
     model           TEXT,
     agent_principal TEXT,
     request_id      TEXT UNIQUE,
     prompt_tokens   INT,
     completion_tokens INT,
     total_tokens    INT,
     cost_usd        NUMERIC(12, 6),
     latency_ms      INT,
     user_id         TEXT,
     error           TEXT,
     raw             JSONB NOT NULL,
     created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
   );
   CREATE INDEX IF NOT EXISTS llm_calls_ts_idx ON llm_calls (ts DESC);
   CREATE INDEX IF NOT EXISTS llm_calls_principal_idx ON llm_calls (agent_principal, ts DESC);
   CREATE INDEX IF NOT EXISTS llm_calls_model_idx ON llm_calls (model, ts DESC);
   ```

   `request_id UNIQUE` lets us safely retry batches — the n8n insert
   should use `ON CONFLICT (request_id) DO NOTHING`.

2. Create an n8n workflow:
   - **Webhook** trigger, path `/llm-calls`, method POST, auth = "Header
     Auth" with a shared secret (matches `LLM_COST_RELAY_TOKEN`).
   - **Split In Batches** over `body.records` (size 1).
   - **Postgres** insert into `llm_calls` with `ON CONFLICT
     (request_id) DO NOTHING`, mapping fields directly and storing the
     full record in the `raw` JSONB column.
   - **Respond to Webhook** with HTTP 200.

3. Note the webhook URL (e.g.
   `https://memory-engine.dev-path.org/n8n/webhook/llm-calls`) and the
   shared secret.

## Setup on Alienware

1. Create `~/.config/homelab-control/litellm-cost-relay.env`:

   ```bash
   LLM_COST_RELAY_URL=https://memory-engine.dev-path.org/n8n/webhook/llm-calls
   LLM_COST_RELAY_TOKEN=<shared-secret>
   LLM_COST_RELAY_INTERVAL_S=30
   LLM_COST_RELAY_BATCH=200
   LOG_LEVEL=INFO
   ```

   Until the webhook is configured, **leave `LLM_COST_RELAY_URL`
   unset** — the relay will run in dry-run mode (advances offset,
   logs `would ship` counts) which is still useful for confirming the
   capture half is healthy.

2. Install the unit:
   ```bash
   cp ~/git/homelab-control/systemd/alienware-litellm-cost-relay.service \
      ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now alienware-litellm-cost-relay.service
   ```

3. Watch:
   ```bash
   journalctl --user -u alienware-litellm-cost-relay.service -f
   ```

## Operating tips

- **Replay**: to re-ship everything from scratch, stop the service,
  `rm ~/.local/state/homelab-control/llm-calls/.relay-offset`, start.
  Combined with the `ON CONFLICT (request_id) DO NOTHING` upsert, this
  is idempotent.
- **Pause shipping but keep capturing**: `systemctl --user stop
  alienware-litellm-cost-relay.service`. The gateway keeps writing to
  the JSONL; the relay catches up on restart.
- **Rotate**: the JSONL grows linearly. When you ship to PG, the JSONL
  is the durable buffer, not the system of record — feel free to
  `logrotate` it weekly. The relay handles `file shrank` gracefully
  (resets offset to 0) but you'll need to manually clear the offset
  state file or accept one duplicate batch (idempotent at PG level).

## Why JSONL + relay instead of writing to PG directly?

1. **Decoupling from memory-engine availability** — the LLM request
   path must never depend on a downstream observability sink.
2. **Network isolation** — PG on the LXC is not exposed off-host (only
   n8n's HTTP port is). The webhook indirection respects that boundary.
3. **Schema evolution** lives in n8n/PG, not the gateway container.
4. **Local debugging** — `tail -f` on the JSONL is the fastest
   feedback loop for any model-related question ("did the call go
   through? how many tokens? how long?").
