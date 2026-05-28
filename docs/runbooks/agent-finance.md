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

### Planka smoke tests

**Unit tests** (no Planka network):

```bash
cd ~/git/homelab-control
export PYTHONPATH=.
python3 -m unittest apps.finance_agent.test_categorize_planka -v
pytest apps/finance_agent/test_categorize_planka.py apps/finance_agent/test_main.py -q
```

**Live smoke** (create + delete `smoke-finance-defer` on the finance board):

```bash
source ~/.config/homelab-control/agent-finance.env
export PYTHONPATH=~/git/homelab-control
./scripts/live_smoke_finance_planka.sh
```

Or via pytest on Alienware:

```bash
export FINANCE_PLANKA_LIVE=1
pytest apps/finance_agent/test_finance_planka_live.py -m live -v
```

Preflight only:

```bash
source ~/.config/homelab-control/agent-finance.env
python3 -c "
from apps.finance_agent.categorize.planka import planka_defer_configured
from apps._shared.planka_client import planka_auth_configured
print('auth', planka_auth_configured())
print('ready', planka_defer_configured())
"
```

**Cleanup** leftover finance smoke cards (separate from homelab
`planka_cleanup_test_cards.py`). Set `PLANKA_FINANCE_BOARD_ID` in
`agent-finance.env` (board id from the Planka URL).

```bash
source ~/.config/homelab-control/agent-finance.env
export PYTHONPATH=~/git/homelab-control

# preview
python3 scripts/planka_cleanup_finance_test_cards.py

# delete
python3 scripts/planka_cleanup_finance_test_cards.py --execute
```

Normal `live_smoke_finance_planka.sh` runs delete automatically; use cleanup
after `--no-cleanup` or a failed delete.

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
