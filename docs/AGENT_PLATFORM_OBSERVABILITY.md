# Agent Platform Observability

The Alienware agent runtime now writes a consolidated platform snapshot to:

- `~/.local/state/homelab-control/platform-status.json`

It includes:

- author queue counts and failed jobs
- review queue counts and failed jobs
- executive assistant queue counts and failed jobs
- author and review heartbeat freshness
- executive assistant heartbeat freshness
- open PRs that the review agent has left in `needs_human_review` or `request_changes`
- an overall `healthy` flag

## Manual usage

```bash
python3 scripts/agent_platform_status.py \
  --author-queue ~/.local/state/homelab-control/agent-homelab \
  --review-queue ~/.local/state/homelab-control/agent-review \
  --author-heartbeat ~/.local/state/homelab-control/agent-homelab/heartbeat.json \
  --review-heartbeat ~/.local/state/homelab-control/agent-review/heartbeat.json \
  --executive-queue ~/.local/state/homelab-control/agent-executive \
  --executive-heartbeat ~/.local/state/homelab-control/agent-executive/heartbeat.json
```

## Scheduled refresh

`scripts/install-alienware-agent-services.sh` now installs:

- `alienware-agent-platform-report.service`
- `alienware-agent-platform-report.timer`

The timer refreshes the snapshot every 5 minutes so operators can inspect one
status file instead of multiple queue folders and PR comment streams.

The Agent Activity dashboard also reads the executive assistant trust ledger and
weekly review file from `~/.local/state/homelab-control/agent-executive/` so the
assistant can summarize trends without putting every event into chat.

The dashboard now also renders:

- project-level trust posture and route usage from executive + maintainer ledgers
- the discovery intake funnel (`raw`, `scratch`, `projects`, `routed`, `project-proposals`)
- the homelab-maintainer queue and heartbeat
