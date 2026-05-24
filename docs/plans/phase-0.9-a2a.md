# Phase 0.9: Agent-to-Agent Message Bus

**Status: SHIPPED 2026-05-24** (`phase-1-finance`, deployed on Alienware).

## Delivered

| Component | Location |
|-----------|----------|
| Envelope schema | `apps/_shared/a2a/envelope.py` |
| Registry routing + ACL | `apps/_shared/a2a/routing.py` |
| Shared queue ops | `apps/_shared/a2a/queue.py` |
| Public API | `apps/_shared/a2a/__init__.py` — `ask_agent`, `reply_to_caller`, `await_reply`, `make_tier2_executive_handler` |
| Escalation Tier 2 | `make_tier2_executive_handler()` → executive `help_request` |
| Executive handler | `apps/executive_agent/help_request.py` |
| Escalation Tier 3 (Discord) | `apps/_shared/escalation/tier3_discord.py` |
| Tests | `apps/_shared/a2a/test_a2a.py`, `test_e2e_production.py` |
| Planka auth | `apps/_shared/planka_client.py` (`PLANKA_API_KEY`, v2 card `type`) |

Maintainer, executive, and author use shared `enqueue`; maintainer delegate paths use `resolve_queue_dir()` instead of `AUTHOR_QUEUE_DIR` / `REVIEW_QUEUE_DIR` env vars.

## Live verification

With `alienware-executive-agent.service` running and Planka configured per agent:

```bash
cd ~/git/homelab-control
git pull --ff-only forgejo phase-1-finance   # canonical remote
export PYTHONPATH=.
export HOMELAB_E2E_LIVE=1 HOMELAB_E2E_LIVE_QUEUES=1
pytest apps/_shared/a2a/test_e2e_production.py::test_live_help_request_on_host_queues -q
```

Live smoke uses ``dry_run`` in the help_request payload (no Planka card); a
finally hook deletes the card if one was created anyway.

Or Tier 2 via escalation handler:

```bash
set -a && source ~/.config/homelab-control/agent-homelab-maintainer.env && set +a
export PYTHONPATH=~/git/homelab-control AGENT_PRINCIPAL=agent:homelab-maintainer
python3 -c "
from apps._shared.a2a import make_tier2_executive_handler
ok, payload, reason = make_tier2_executive_handler()({
    'task_class': 'live.tier2.smoke',
    'blocked_reason': 'manual tier2 round-trip',
    'urgent': False,
})
print('ok', ok, 'reason', reason, 'payload', payload)
"
```

## Follow-up (shipped 2026-05-24)

- **Maintainer worker** — `process_job` runs `Dispatcher` with Tier 2/3 on failure
- **Finance ingest** — `finance.ingest` bean-check failures escalate (DM-only Tier 3)
- **Tier 3 ack daemon** — `scripts/escalation_tier3_ack_daemon.py` + `alienware-escalation-tier3-ack.timer`
- **Queue retry/DLQ** — `requeue_for_retry()` + `dlq/` stage in `apps/_shared/a2a/queue.py`
- **Reply-loop fix + help_request dedupe** — skip `a2a-reply-*` in workers; `help_request_dedup.py`
- **Observability** — `apps/_shared/a2a/observability.py`; master dashboard **A2A** tile; `platform-status.json` `a2a` block — see `docs/A2A_OBSERVABILITY.md`
- **Deploy cleanup** — `scripts/post_deploy_agent_stack.sh` (Planka smoke card delete on install/deploy)
- **Git remotes** — Forgejo canonical; `scripts/push-primary-remotes.sh`; live smoke uses `dry_run`

Set `ESCALATION_APPROVALS_CHANNEL_ID` (or manifest `discord.channels[].id` for `#approvals`) on Alienware. Disable escalation in tests with `HOMELAB_ESCALATION_DISABLE=1`.

**Sprint closed.** Still open elsewhere: Phase 0.10 sub-agent spawner; richer A2A retry/backoff per action class; finance queue worker.

See also `docs/plans/phase-0-platform.md` §0.9 and §0.11.
