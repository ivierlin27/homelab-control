# A2A observability

Phase 0.9 agents communicate over filesystem queues under
``~/.local/state/homelab-control/``. Operators can monitor the bus without
reading inbox directories by hand.

## Master dashboard tile

The master dashboard at ``http://192.168.1.45:8800/`` includes an **A2A message
bus** tile (refreshes every 30s). It shows:

| Signal | Meaning |
|--------|---------|
| Per-agent inbox / processing / dlq / failed counts | executive + homelab-maintainer |
| **stuck replies** | ``a2a-reply-*.json`` left in inbox (should stay at 0) |
| **a2a inbox** | pending ``help_request`` envelopes in executive inbox |
| Tier 3 open / awaiting ack | Discord escalation backlog |
| Recent help_request | tail of executive ``trust-ledger.jsonl`` |

Alerts turn the tile amber when stuck replies, DLQ jobs, or a deep executive
backlog are detected.

Implementation: ``apps/_shared/a2a/observability.py``,
``apps/master_dashboard/templates/_tile_a2a.html``.

## Platform status JSON

``scripts/agent_platform_status.py`` (timer: ``alienware-agent-platform-report.timer``)
writes ``~/.local/state/homelab-control/platform-status.json`` every 5 minutes.
The snapshot now includes an ``a2a`` block with the same metrics as the tile.
Overall ``healthy`` is false when A2A alerts are present.

```bash
python3 scripts/agent_platform_status.py \
  --author-queue ~/.local/state/homelab-control/agent-homelab \
  --review-queue ~/.local/state/homelab-control/agent-review \
  --author-heartbeat ~/.local/state/homelab-control/agent-homelab/heartbeat.json \
  --review-heartbeat ~/.local/state/homelab-control/agent-review/heartbeat.json \
  --output ~/.local/state/homelab-control/platform-status.json
jq '.a2a' ~/.local/state/homelab-control/platform-status.json
```

## Audit stream

Executive ``a2a-help-request`` rows appear in the dashboard **audit** SSE tail
(``trust-ledger.jsonl``). Filter for ``"event":"a2a-help-request"`` when
correlating with Planka cards.

## Deploy cleanup

``scripts/post_deploy_agent_stack.sh`` deletes smoke Planka cards after each
deploy (``planka_cleanup_test_cards.py --execute``). Called from
``install-alienware-agent-services.sh`` and documented in ``docs/AGENT_SERVICES.md``.

## Live smoke tests

``test_live_help_request_on_host_queues`` uses ``dry_run: true`` in the payload
so routine smoke does not create Planka cards. A ``finally`` hook deletes any
card that was created anyway.
