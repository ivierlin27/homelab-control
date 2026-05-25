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

**Git remotes:** Forgejo (`forgejo.dev-path.org`) is canonical; GitHub personal
(`origin`) is the backup mirror. After committing locally, push both:

```bash
./scripts/push-primary-remotes.sh
```

On Alienware, pull from Forgejo (not only GitHub), then run post-deploy hooks:

```bash
cd ~/git/homelab-control
git fetch forgejo
git pull --ff-only forgejo "$(git branch --show-current)"
./scripts/post_deploy_agent_stack.sh
# optional: RESTART_AGENT_SERVICES=1 ./scripts/post_deploy_agent_stack.sh
```

`post_deploy_agent_stack.sh` runs `planka_cleanup_test_cards.py --execute` (smoke /
A2A test cards) using `agent-executive.env` by default, then
`live_smoke_subagent.sh` (executive + maintainer sub-agent wiring). Set
`SKIP_SUBAGENT_SMOKE=1` to skip the LLM smoke when the gateway is down.

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

### Planka credentials per service

All Planka HTTP callers use `apps/_shared/planka_client.py`:

1. **`PLANKA_API_KEY`** (preferred) — `X-Api-Key` header, one key per Planka service user
2. **`PLANKA_API_TOKEN`** — legacy Bearer token
3. **`PLANKA_EMAIL_OR_USERNAME` + `PLANKA_PASSWORD`** — mints a Bearer token at startup

| Service | Env file | Planka user (recommended) |
|---------|----------|---------------------------|
| Executive worker | `agent-executive.env` | `agent-executive` |
| Homelab maintainer | `agent-homelab-maintainer.env` | `agent-homelab-maintainer` |
| Event dispatcher | `agent-dispatcher.env` | `agent-dispatcher` (or `agent-platform`) |

Use the **same homelab board** IDs (`PLANKA_BOARD_ID`, column list IDs) across executive, maintainer, and dispatcher. Copy the full column set from `agent-dispatcher.env` into each file that needs it.

**Planka roles:** executive and maintainer only **create cards** and attach labels — **Board user** on that board is enough. The dispatcher also **moves cards**, edits descriptions, and may **create board labels** when missing (`ensure_label`) — use **Board editor** on that board, or **project owner** on the homelab project if label creation or cross-list moves fail as board user. Project owner is fine for a dedicated service account; it is broader than the agents need.

After changing `agent-dispatcher.env`, restart:

```bash
systemctl --user restart alienware-agent-event-dispatcher.service
```

### Clean up test cards

`scripts/planka_cleanup_test_cards.py` deletes agent smoke cards (`verify-*`, `smoke-*`, `A2A help:`, etc.) from the homelab board. **Dry-run by default** — pass `--execute` to delete.

```bash
set -a && source ~/.config/homelab-control/agent-dispatcher.env && set +a
export PYTHONPATH=~/git/homelab-control
python3 scripts/planka_cleanup_test_cards.py              # preview
python3 scripts/planka_cleanup_test_cards.py --execute    # delete
python3 scripts/planka_cleanup_test_cards.py --execute --include-legacy-e2e
```

Use any env file with `PLANKA_BASE_URL`, `PLANKA_API_KEY`, and `PLANKA_BOARD_ID` (dispatcher or executive). The API user needs permission to delete cards on that board (board editor or project owner).

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
