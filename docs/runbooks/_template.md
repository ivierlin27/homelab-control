# <Service or Job Name>

<One-paragraph "what it is, what it does, who it serves" intro. If an
on-call agent reads only this paragraph and the Symptoms table, they
should know whether this runbook is the right one.>

## Setup

<How to install / deploy from scratch. Commands you would run on a
fresh host. Link to systemd units + config files by path.>

## Symptoms → likely causes

| Symptom | Likely cause | Where to look first |
|---|---|---|
| _e.g. "no Discord post on Monday morning"_ | timer skipped because system was offline; Discord webhook url missing | `systemctl --user list-timers`; `~/.config/.../*.env` |
| _e.g. "exit 0 but report empty"_ | probe couldn't reach any host (SSH key issue) | `journalctl --user -u <unit>.service -n 100` |

## Investigation steps

Ordered list of commands to run, starting with the cheapest and most
likely. Each step should resolve to "this is/isn't the cause" — no
dead-ends.

1. `systemctl --user status <unit>.service` — service even tried to run?
2. `journalctl --user -u <unit>.service -n 200 --no-pager` — full last-run log
3. `<runtime-specific check>` — e.g. `curl -fsS <health endpoint>`
4. `python -m apps._shared.audit verify <ledger>` — chain still intact?
5. <whatever else is service-specific>

## Recovery

For each cause in the Symptoms table, the minimum-blast-radius fix.
Prefer "restart this one unit" over "redeploy the whole stack".

- **Cause A:** `systemctl --user restart <unit>.service`; verify with step 3.
- **Cause B:** edit `~/.config/.../service.env`; `systemctl --user daemon-reload && systemctl --user restart <unit>.service`.

## Past incidents

Append-only chronological log. Newest entry **at the top**. Each entry
captures the date, symptom, root cause, fix, and (if relevant) what
runbook section was updated as a result.

### YYYY-MM-DD — <one-line summary>

- **Symptom:** what we saw
- **Root cause:** what was actually wrong
- **Fix:** what we did
- **Followup:** runbook updated? code change? new test?

## Configuration

| Var | Default | Purpose |
|---|---|---|

## Future work / known limitations

- _e.g. "no Trivy CVE scanning yet"_
- _e.g. "no auto-PR generation"_
