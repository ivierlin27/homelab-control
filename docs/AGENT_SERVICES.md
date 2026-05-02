# Agent Services

The Alienware author and review agents run as `systemd --user` services.

## Install

On the Alienware host:

```bash
./scripts/install-alienware-agent-checkout.sh "${HOME}/git/homelab-control"
./scripts/install-alienware-agent-services.sh
```

When deploying from another machine, SSH to the host-specific address:

```bash
ssh kenns@192.168.1.45
```

Do not use service DNS names that terminate at the nginx/reverse-proxy host
(`192.168.1.42`) unless Pi-hole has a host-specific override for SSH.

The workers should run from a clean git-backed checkout, not a hand-rsynced or
dirty runtime tree. The recommended root is:

- `~/git/homelab-control`

This creates:

- `~/.config/systemd/user/alienware-homelab-maintainer-agent.service`
- `~/.config/systemd/user/alienware-author-agent.service`
- `~/.config/systemd/user/alienware-review-agent.service`
- `~/.config/systemd/user/alienware-executive-agent.service`
- `~/.config/systemd/user/alienware-executive-chat.service`
- `~/.config/systemd/user/alienware-executive-discord.service`
- `~/.config/systemd/user/alienware-executive-weekly-review.timer`
- `~/.config/systemd/user/alienware-agent-event-dispatcher.service`
- `~/.config/homelab-control/agent-homelab.env`
- `~/.config/homelab-control/agent-homelab-maintainer.env`
- `~/.config/homelab-control/agent-review.env`
- `~/.config/homelab-control/agent-executive.env`
- `~/.config/homelab-control/agent-executive-chat.env`
- `~/.config/homelab-control/agent-executive-discord.env`
- `~/.config/homelab-control/agent-dispatcher.env`
- queue directories under `~/.local/state/homelab-control/`

The env files are the editable source of truth for:

- `HOMELAB_CONTROL_ROOT`
- Forgejo base URL, repo owner, repo name, and API token
- the preferred git remote for author branches
- whether the review agent may auto-merge low-risk PRs
- whether the homelab-maintainer may delegate into author/review queues
- Planka board/list IDs used by the event dispatcher

The author env should point `HOMELAB_CONTROL_ROOT` at the clean checkout and
define a working `forgejo` push path through `AGENT_GIT_REMOTE` plus
`AGENT_GIT_SSH_COMMAND`.

## Planka lifecycle

Planka columns are the trigger surface:

- `Plan Ready` asks the agent to draft/refresh a plan
- `Approved To Execute` starts execution
- `Needs Human Review` waits for a person
- `Done` means complete

Labels are state and metadata only. Manual label changes do not enqueue work.

The event dispatcher moves cards as the agents report progress:

- PR opened -> `In Progress` with `state:pr-open` and `state:review-agent`
- review needs approval -> `Needs Human Review` with `review:pr`
- review says ready -> `Needs Human Review` with `state:ready-to-merge`
- PR merged -> `Done`

## Queues

Author queue:

- `~/.local/state/homelab-control/agent-homelab/inbox`
- `~/.local/state/homelab-control/agent-homelab/processing`
- `~/.local/state/homelab-control/agent-homelab/done`
- `~/.local/state/homelab-control/agent-homelab/failed`

Homelab-maintainer queue:

- `~/.local/state/homelab-control/agent-homelab-maintainer/inbox`
- `~/.local/state/homelab-control/agent-homelab-maintainer/processing`
- `~/.local/state/homelab-control/agent-homelab-maintainer/done`
- `~/.local/state/homelab-control/agent-homelab-maintainer/failed`
- `~/.local/state/homelab-control/agent-homelab-maintainer/trust-ledger.jsonl`
- `~/.local/state/homelab-control/agent-homelab-maintainer/lifecycle-events.jsonl`

Review queue:

- `~/.local/state/homelab-control/agent-review/inbox`
- `~/.local/state/homelab-control/agent-review/processing`
- `~/.local/state/homelab-control/agent-review/done`
- `~/.local/state/homelab-control/agent-review/failed`

Executive queue:

- `~/.local/state/homelab-control/agent-executive/inbox`
- `~/.local/state/homelab-control/agent-executive/processing`
- `~/.local/state/homelab-control/agent-executive/done`
- `~/.local/state/homelab-control/agent-executive/failed`

Executive chat state:

- `~/.local/state/homelab-control/agent-executive/conversations.sqlite3`
- `~/.local/state/homelab-control/agent-executive/trust-ledger.jsonl`
- `~/.local/state/homelab-control/agent-executive/lifecycle-events.jsonl`

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

Executive assistant intake:

```json
{
  "action": "handle-request",
  "request": "Research better family calendar options",
  "domain": "homelab",
  "task_type": "research",
  "labels": [
    "type:research"
  ],
  "search_memory": true,
  "plan_ready": true,
  "write_memory": true
}
```

Homelab-maintainer intake triage:

```json
{
  "action": "triage-intake",
  "intake_id": "intake-20260501-homelab-router",
  "title": "Model gateway cleanup idea",
  "content": "Clean up old LiteLLM routes and document current defaults.",
  "source_kind": "text",
  "task_class": "architecture_synthesis",
  "symbolic_intent": "plan",
  "routing": {
    "route": "cloud-frontier",
    "model_tier": "cloud-frontier"
  }
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
systemctl --user status alienware-executive-agent.service
systemctl --user status alienware-homelab-maintainer-agent.service
systemctl --user status alienware-executive-chat.service
systemctl --user status alienware-executive-weekly-review.timer
```

Tail logs:

```bash
journalctl --user -u alienware-author-agent.service -f
journalctl --user -u alienware-review-agent.service -f
journalctl --user -u alienware-executive-agent.service -f
journalctl --user -u alienware-homelab-maintainer-agent.service -f
journalctl --user -u alienware-executive-chat.service -f
```

Inspect queue state:

```bash
python3 apps/author_agent/main.py queue-status --queue-dir ~/.local/state/homelab-control/agent-homelab
python3 apps/review_agent/main.py queue-status --queue-dir ~/.local/state/homelab-control/agent-review
python3 apps/executive_agent/main.py queue-status --queue-dir ~/.local/state/homelab-control/agent-executive
python3 apps/homelab_maintainer_agent/main.py queue-status --queue-dir ~/.local/state/homelab-control/agent-homelab-maintainer
python3 apps/executive_agent/main.py weekly-review --state-dir ~/.local/state/homelab-control/agent-executive
```

Local executive chat UI:

```bash
grep EXECUTIVE_CHAT_TOKEN ~/.config/homelab-control/agent-executive-chat.env
# http://192.168.1.45:8767/?token=<token>
```

Dashboard:

```bash
grep AGENT_ACTIVITY_TOKEN ~/.config/homelab-control/agent-activity.env
# https://agents.dev-path.org/?token=<token>
```

Discord bridge:

```bash
python3 -m pip install --user -r apps/executive_agent/requirements.txt
${EDITOR:-vi} ~/.config/homelab-control/agent-executive-discord.env
systemctl --user enable --now alienware-executive-discord.service
```
