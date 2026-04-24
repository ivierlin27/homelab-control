# Agent Services

The Alienware author and review agents run as `systemd --user` services.

## Install

On the Alienware host:

```bash
./scripts/install-alienware-agent-services.sh
```

This creates:

- `~/.config/systemd/user/alienware-author-agent.service`
- `~/.config/systemd/user/alienware-review-agent.service`
- `~/.config/homelab-control/agent-homelab.env`
- `~/.config/homelab-control/agent-review.env`
- queue directories under `~/.local/state/homelab-control/`

## Queues

Author queue:

- `~/.local/state/homelab-control/agent-homelab/inbox`
- `~/.local/state/homelab-control/agent-homelab/processing`
- `~/.local/state/homelab-control/agent-homelab/done`
- `~/.local/state/homelab-control/agent-homelab/failed`

Review queue:

- `~/.local/state/homelab-control/agent-review/inbox`
- `~/.local/state/homelab-control/agent-review/processing`
- `~/.local/state/homelab-control/agent-review/done`
- `~/.local/state/homelab-control/agent-review/failed`

Each worker writes a `heartbeat.json` file beside its queue root.

## Job examples

Author plan render:

```json
{
  "action": "render-plan",
  "card": "/path/to/card.json",
  "output_path": "/path/to/rendered-plan.md"
}
```

Author summary:

```json
{
  "action": "summarize-result",
  "card": "/path/to/card.json",
  "pr_url": "https://forgejo.dev-path.org/kevin/homelab-control/pulls/123"
}
```

Review decision:

```json
{
  "action": "evaluate-review",
  "input": "/path/to/pr-context.json",
  "output_path": "/path/to/review-decision.json"
}
```

## Operations

Check service status:

```bash
systemctl --user status alienware-author-agent.service
systemctl --user status alienware-review-agent.service
```

Tail logs:

```bash
journalctl --user -u alienware-author-agent.service -f
journalctl --user -u alienware-review-agent.service -f
```
