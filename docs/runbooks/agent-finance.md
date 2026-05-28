# agent:finance runbook (MVP-B)

## Categorize

```bash
cd ~/git/homelab-control
source ~/.config/homelab-control/agent-finance.env
export PYTHONPATH=~/git/homelab-control AGENT_PRINCIPAL=agent:finance

python3 -m apps.finance_agent --skip-boot categorize
python3 -m apps.finance_agent --skip-boot categorize --llm
```

`--llm` requires `MODEL_GATEWAY_BASE_URL` and `MODEL_GATEWAY_API_KEY` in
`agent-finance.env` and a running local LiteLLM gateway
(`alienware-model-gateway.service`).

When rows defer, the batch writes
`~/.local/state/homelab-control/agent-finance/defer-<correlation_id>.md`.
If `PLANKA_FINANCE_DEFER_LIST_ID` is set (and Planka auth works), one card
per deferred row is created automatically. Use `--planka` to force or
`--no-planka` to skip.

Env template: `config/env/agent-finance.env.example`.

## LiteLLM gateway (Alienware)

```bash
systemctl --user daemon-reload
systemctl --user enable --now alienware-model-gateway.service
systemctl --user status alienware-model-gateway.service   # active (exited) is normal
curl -s http://127.0.0.1:4000/v1/models -H "Authorization: Bearer $MODEL_GATEWAY_API_KEY" | head
```

The unit is `Type=oneshot` + `RemainAfterExit=yes` with `podman run -d` (detached).
`--num_workers 1`; no pre-start `podman rm` (uses `--replace` only).

## Ledger

Private repo: `~/finance/ledger` (Forgejo `finance/finance-ledger`).
Always run `bean-check` after manual edits.
