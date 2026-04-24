# Agent Services

The Alienware author and review agents run as `systemd --user` services.

## Install

On the Alienware host:

```bash
./scripts/install-alienware-agent-checkout.sh "${HOME}/git/homelab-control"
./scripts/install-alienware-agent-services.sh
```

The workers should run from a clean git-backed checkout, not a hand-rsynced or
dirty runtime tree. The recommended root is:

- `~/git/homelab-control`

This creates:

- `~/.config/systemd/user/alienware-author-agent.service`
- `~/.config/systemd/user/alienware-review-agent.service`
- `~/.config/homelab-control/agent-homelab.env`
- `~/.config/homelab-control/agent-review.env`
- queue directories under `~/.local/state/homelab-control/`

The env files are the editable source of truth for:

- `HOMELAB_CONTROL_ROOT`
- Forgejo base URL, repo owner, repo name, and API token
- the preferred git remote for author branches
- whether the review agent may auto-merge low-risk PRs

The author env should point `HOMELAB_CONTROL_ROOT` at the clean checkout and
define a working `forgejo` push path through `AGENT_GIT_REMOTE` plus
`AGENT_GIT_SSH_COMMAND`.

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

The author queue also keeps git worktrees under:

- `~/.local/state/homelab-control/agent-homelab/worktrees`

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
  "action": "review-pr",
  "input": "/path/to/pr-context.json",
  "output_path": "/path/to/review-decision.json"
}
```

Author execution job:

```json
{
  "action": "execute-task",
  "title": "Pin mutable container images",
  "allowed_paths": [
    "compose/model-gateway",
    "compose/infisical"
  ],
  "operations": {
    "replacements": [
      {
        "path": "compose/model-gateway/docker-compose.yml",
        "old_string": "ghcr.io/berriai/litellm:main-latest",
        "new_string": "ghcr.io/berriai/litellm:vX.Y.Z-stable"
      }
    ]
  },
  "checks": [
    "git diff --check"
  ],
  "labels": [
    "safe-update"
  ],
  "plan_link": "https://planka.example/cards/123",
  "planka_card": "https://planka.example/cards/123",
  "review_queue_dir": "/home/kenns/.local/state/homelab-control/agent-review"
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

Inspect queue state:

```bash
python3 apps/author_agent/main.py queue-status --queue-dir ~/.local/state/homelab-control/agent-homelab
python3 apps/review_agent/main.py queue-status --queue-dir ~/.local/state/homelab-control/agent-review
```
