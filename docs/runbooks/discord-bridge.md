# Discord bridge runbook

> Per-agent Discord bots (`executive`, `homelab-maintainer`, `homelab`,
> `author`, `review`) — the shared bridge in
> [`apps/_shared/discord_bridge/`](/apps/_shared/discord_bridge/) plus the
> thin per-agent wrappers at `apps/<agent>/discord_bot.py`.

## 1. What it is

Each agent runs its own Discord bot process under systemd-user. The shared
bridge in `apps/_shared/discord_bridge/bridge.py` handles:

- Discord client setup with intents (`message_content`, `dm_messages`,
  `members`).
- Inbound filtering: drop bot authors; require DM-or-mention-or-prefix;
  enforce a **channel allowlist** and a **user allowlist**.
- Audit logging via the agent's hash-chained trust ledger.
- Reply chunking at 1800 chars.

Per-agent wrappers supply a `handler(ctx) -> str | None` that returns a reply
string (or `None` for silence).

## 2. Channel allowlist source-of-truth

The allowlist is now **manifest-driven** when the agent manifest's
`discord.channels[]` entries include `id:` values:

```yaml
# config/agents/agent-executive.yaml
discord:
  bot_app_name: agent-executive
  channels:
    - { id: 1234567890123, name: "#intake",     mode: write }
    - { id: 2345678901234, name: "#approvals",  mode: write }
    - { id: 3456789012345, name: "#finance",    mode: read }   # listen only
  default_channel: "#intake"
```

- **`mode: write`** — agent receives and replies.
- **`mode: read`** — agent receives but cannot reply. Outbound writes are
  blocked at the bridge and audited as `direction: write-denied`.

Channels listed by `name:` only (no `id:`) are skipped by the bridge — Discord
identifies channels by snowflake, not name. The `DISCORD_ALLOWED_CHANNEL_IDS`
environment variable is the **legacy fallback** used when no `id:` values are
populated. If both are set and disagree, the manifest wins and a warning is
logged.

## 3. Setup — backfilling channel IDs into a manifest

```bash
# On the host where the bot runs, in the project venv:
DISCORD_BOT_TOKEN=<bot-token-for-the-bot-you-care-about> \
  python3 scripts/discord/dump-channel-ids.py
```

Output is YAML-ready entries like
`- { id: 1234..., name: "#ops", mode: write }`. Copy the ones you want into
`config/agents/agent-<principal>.yaml` under `discord.channels`. Commit the
change; the bridge picks it up on next restart.

Each bot only sees channels it has been invited to, so you'll have to run the
script for each bot whose manifest you're updating.

After backfilling, **drop** the now-redundant `DISCORD_ALLOWED_CHANNEL_IDS=`
line from the systemd unit and `systemctl --user daemon-reload && restart`.

## 4. Symptoms → likely causes

| Symptom | Likely cause | First check |
|---|---|---|
| Bot online but ignores messages in a channel | Channel not in allowlist (manifest or env var) | `grep -E 'channel_name\|allowlist' ~/.local/state/homelab-control/agent-<p>/trust-ledger.jsonl \| tail` |
| Bot receives but never replies | Channel `mode: read` in manifest; outbound blocked | look for `direction: write-denied` rows in the trust ledger |
| `WARNING: ... disagrees with manifest ...` in logs | Env var allowlist no longer matches manifest IDs | drop the env var; restart |
| `DISCORD_BOT_TOKEN must be configured` | Token env var missing or placeholder | check `~/.config/homelab-control/agent-<principal>.env` |
| Bot can't see a channel that's in the manifest | Bot was never invited to it | invite the bot via Discord server settings |

## 5. Investigation steps

```bash
# Check what the bridge thinks its allowlist is (it logs this on startup):
journalctl --user -u alienware-<agent>-discord.service -n 50 | grep allowlist

# Recent audit events:
tail -n 20 ~/.local/state/homelab-control/agent-<p>/trust-ledger.jsonl | \
  jq -r '[.direction, .channel_name // "(?)", .channel_mode // "(?)", .source_user // "(?)"] | @tsv'

# Did the manifest validate cleanly?
python3 -m apps._shared.registry validate
```

## 6. Recovery

- **Mistakenly listed channel as `read`**: change the manifest entry to
  `mode: write`, commit, restart the bot.
- **Bot in wrong channel**: remove the channel from the manifest, commit,
  restart. (Or kick the bot from the channel in Discord; either works, but
  manifest changes survive reinvites.)
- **Token leaked**: regenerate in Discord Developer Portal; update
  `agent-<p>.env`; restart.

## 7. Past incidents

(none yet — this runbook lands with the manifest-driven allowlist change in
the `platform_followup_discord_channels` work item.)

## 8. Future work / limitations

- **Slash commands** — see plan item `platform_discord_ux`. The current
  prefix-command style (`!assistant ...`) doesn't get Discord's native
  autocomplete; slash commands with typed args are strictly better UX.
- **Buttons + modals for replies** — single-message reply chunking is fine
  for free-form text but bad for "approve / deny" flows.
- **Forum-channel-per-task threading** — one thread per task with auto-routing
  is a much nicer UX than channel-level filtering; future plan item.
- **Cross-channel posting** — today's bridge replies only to the channel the
  inbound came from. If an agent ever needs to *initiate* an outbound (e.g. an
  alert), it currently uses a different code path (`apps/health_monitor` posts
  directly via webhook); unify that with the bridge's allowlist enforcement
  in a future pass.
