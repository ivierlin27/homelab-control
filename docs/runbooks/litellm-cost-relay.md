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

## Setup on memory-engine LXC (already applied 2026-05-17)

### Postgres `llm_calls` table

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

`request_id UNIQUE` is the idempotency anchor — the n8n insert uses
`ON CONFLICT (request_id) DO NOTHING`, so the relay can replay batches
without producing duplicates.

To re-apply (e.g. on a rebuilt LXC):

```bash
ssh root@memory-engine.dev-path.org \
  "docker exec -i memory-postgres psql -U memory -d memory" \
  < <DDL above>
```

### n8n workflow

The workflow definition lives in
[`compose/n8n-workflows/homelab-llm-cost-ingest.json`](../../compose/n8n-workflows/homelab-llm-cost-ingest.json)
and is imported into the LXC via the n8n CLI:

```bash
# Generate a fresh bearer token (paste into Alienware env later):
BEARER=$(openssl rand -hex 24)

# Build creds JSON inline (do NOT commit this file — it has the PG password):
cat > /tmp/creds.json <<EOF
[
  {"id":"homelab-pg","name":"memory-engine postgres","type":"postgres",
   "data":{"host":"postgres","port":5432,"database":"memory","user":"memory",
           "password":"$(ssh root@memory-engine.dev-path.org 'grep ^POSTGRES_PASSWORD /opt/memory-engine/.env | cut -d= -f2-')","ssl":"disable"}},
  {"id":"homelab-cost-bearer","name":"homelab cost-relay bearer","type":"httpHeaderAuth",
   "data":{"name":"Authorization","value":"Bearer $BEARER"}}
]
EOF

scp /tmp/creds.json root@memory-engine.dev-path.org:/tmp/
scp compose/n8n-workflows/homelab-llm-cost-ingest.json root@memory-engine.dev-path.org:/tmp/workflow.json
ssh root@memory-engine.dev-path.org "
  docker cp /tmp/creds.json memory-n8n:/tmp/creds.json
  docker cp /tmp/workflow.json memory-n8n:/tmp/workflow.json
  docker exec memory-n8n n8n import:credentials --input=/tmp/creds.json
  docker exec memory-n8n n8n import:workflow --input=/tmp/workflow.json

  # n8n 2.x ships imported workflows as drafts; publish + activate:
  docker exec memory-postgres psql -U memory -d n8n -c \"
    UPDATE workflow_entity
       SET active=true, \\\"activeVersionId\\\" = \\\"versionId\\\"
     WHERE id='homelab-llm-cost-ingest';\"
  docker restart memory-n8n
"
rm /tmp/creds.json    # do not leave PG password on the laptop
```

Webhook URL: `https://n8n.dev-path.org/webhook/llm-calls`.

## Setup on Alienware

1. Create `~/.config/homelab-control/litellm-cost-relay.env`:

   ```bash
   LLM_COST_RELAY_URL=https://n8n.dev-path.org/webhook/llm-calls
   LLM_COST_RELAY_TOKEN=<bearer from n8n credential>
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

## Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| Dashboard cost tile shows "stale (>1h)" | relay service is down OR n8n webhook is unreachable | `systemctl --user is-active alienware-litellm-cost-relay.service`; `curl -fsS <n8n-webhook-url>` (200 = up) |
| Cost numbers stopped updating but service is running | offset file got out of sync with the JSONL (e.g., manual `truncate`) | `stat ~/.local/state/homelab-control/llm-calls/llm-calls.jsonl`; if smaller than the offset, the relay resets to 0 — but a stale offset persists |
| n8n returns 500 on POST | Postgres `llm_calls` insert failed (e.g., new column required but the workflow wasn't updated) | n8n execution log; `psql -h <pg> -c '\d llm_calls'` schema |
| Duplicate rows in PG | `request_id` not unique in the gateway log (race condition on retry) | the INSERT uses `ON CONFLICT (request_id) DO NOTHING`; if you see dupes, the conflict target may be missing — verify `\d llm_calls` shows a unique index |
| Missing `task_intent` on rows | older gateway version was running when the call happened | the `x-task-intent` header is opt-in from the caller; older callers send nothing — `task_intent` is NULL by design for those |

## Investigation steps

1. `systemctl --user status alienware-litellm-cost-relay.service` — running?
2. `tail -20 ~/.local/state/homelab-control/llm-calls/llm-calls.jsonl` — gateway still writing?
3. `cat ~/.local/state/homelab-control/llm-calls/.relay-offset` — where the relay thinks it is
4. `journalctl --user -u alienware-litellm-cost-relay.service -n 100 --no-pager` — last batch result + any HTTP errors
5. `psql -h <pg> -d memory -c "select count(*), max(ts) from llm_calls"` — actual row count + latest timestamp
6. `curl -fsS -X POST <n8n-webhook-url> -d '[]' -H 'content-type: application/json'` — can we hit n8n directly?

## Recovery

- **Service crashed**: `systemctl --user restart alienware-litellm-cost-relay.service`; check that the offset file is preserved.
- **Need to replay everything**: stop service → `rm ~/.local/state/homelab-control/llm-calls/.relay-offset` → start service. The PG-side `ON CONFLICT DO NOTHING` makes this safe.
- **Schema added a column**: update the n8n workflow's INSERT (and re-import); rows already in flight will fail and the relay will retry forever — pause the relay while editing the workflow.

## Past incidents

### 2026-05-16 — Postgres rejected `ALTER TABLE` for `task_intent` column

- **Symptom:** `psql -c '…ALTER…; \d llm_calls'` returned a syntax error
- **Root cause:** `\d llm_calls` is a psql meta-command, not SQL — it cannot share a `-c` invocation with an SQL statement
- **Fix:** split into two `-c` flags, one per statement/meta-command
- **Followup:** the `task_intent` column add is now in this runbook (todo: capture as a one-shot migration script)
