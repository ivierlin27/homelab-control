# CI Failures

This runbook documents what happens (and what should happen) when CI fails
for `homelab-control` on either GitHub Actions or Forgejo Actions.

## Current state (interim, 2026-05-18)

GitHub's default email notifications were the only failure signal until
2026-05-18, when CI run 26045493776 caught a real test bug that Kevin
correctly pointed out he should *not* be the first responder for. The
platform vision (see plan: `platform_escalation`, lines 311–322) says
the human is **Tier 3** — only after agent self-recovery (Tier 1) and
executive reroute (Tier 2) have been exhausted. The current setup had
no Tier 1 or Tier 2 for CI failures, so this is a partial fix.

What now happens on a CI failure:

1. **GitHub / Forgejo Actions email** — unchanged. Kevin still gets it
   but is no longer the only consumer.
2. **Discord `#ci-failures` channel** — the workflow's
   `Notify Discord on failure` step POSTs an embed with the repo,
   branch, commit, author, and run URL to `CI_FAILURE_DISCORD_WEBHOOK`.
   This is the "single pane of glass" — once an agent is wired up it
   will read from this channel.
3. **Manual triage** — a human (Kevin or someone he asks) clicks
   through to the run URL and decides on a fix.

## Target state (Phase 0.11, `platform_followup_ci_auto_triage`)

The autonomous triage loop is a follow-up sprint, scoped roughly as:

1. **Trigger**: a worker on Alienware polls GitHub Actions API for
   failing runs on `homelab-control` (and registered sister repos)
   OR subscribes to `#ci-failures` via the Discord bridge. Polling is
   simpler — no inbound webhook to expose.
2. **Investigation**: the worker spawns a sandboxed
   `apps.author_agent` job whose intent is "investigate run <id>,
   propose a minimal-diff fix". The agent has:
   - read-only access to the failing repo via its agent identity
   - `gh run view --log-failed` and `gh api` available inside its
     sandbox image (already true for `agent:homelab`)
   - a fix-attempt budget (e.g., N=3 attempts, M=15 minutes wall clock)
   - the existing `checks` infrastructure to verify locally that the
     proposed fix turns the failing tests green inside the sandbox
3. **Outcome**:
   - **Tier 1 success**: agent opens a PR on the failing branch with a
     short fix + "auto-triage attempt by `agent:homelab` — please
     review". Posts a summary to `#ci-failures` ("attempt 1/3
     succeeded, PR #N"). Continues to monitor the PR via the existing
     babysit skill.
   - **Tier 2**: budget exhausted or fix touches a sensitive path
     (security, secrets, auth, identity). Posts a structured "I cannot
     fix this — here's what I tried and what I think is needed" to
     `#approvals` and DMs Kevin.
   - All attempts append to the agent's hash-chained
     `trust-ledger.jsonl` so post-hoc auditing can answer "why did the
     agent change this file?"
4. **Prereqs that aren't yet met**:
   - `REMIND_enable_author_sandbox` — `AUTHOR_AGENT_SANDBOX_CHECKS=1`
     needs to be on in production. Currently off pending soak.
   - `agent:homelab` PR-author identity — needs a Forgejo machine
     account + SSH push key. Half-done (see
     `docs/identity-runbook-agent-homelab.md`).
   - `platform_escalation` (Phase 0.11) — needs at least a minimal
     Tier 1/2/3 routing primitive even if `config/escalation.yaml` is
     bare-bones for the CI class.

## One-time setup (operator)

Until the autonomous loop ships, do these once so the visibility hook
above starts working:

```text
1. In Discord:
   - Create a new channel #ci-failures (under whatever category makes
     sense; alongside #ops-alerts is fine).
   - Channel Settings → Integrations → Webhooks → New Webhook.
     Name it "ci-failures" or similar. Copy the webhook URL.

2. In GitHub:
   - Go to https://github.com/<owner>/homelab-control/settings/secrets/actions
   - New repository secret, name: CI_FAILURE_DISCORD_WEBHOOK
   - Paste the webhook URL from step 1. Save.

3. In Forgejo:
   - Go to https://forgejo.dev-path.org/<owner>/homelab-control/settings/secrets
   - Add secret CI_FAILURE_DISCORD_WEBHOOK with the SAME webhook URL.
   - (Same channel — no need for two webhooks; failure source is
     visible in the embed footer.)

4. Smoke test:
   - On a throwaway branch, push a commit that intentionally fails one
     test (e.g., `assert False` in a new test_smoke.py). Confirm the
     Discord embed lands within ~30s of the run completing. Revert the
     branch.
```

## Manual fallback (Kevin or any operator)

If the Discord notification fires and no agent has yet picked it up
(today: always; tomorrow: when the autonomous loop is offline):

1. Open the run URL from the Discord embed.
2. Use the `ci-investigator` Cursor subagent (or `gh run view <id>
   --log-failed`) to root-cause.
3. Fix on a branch, push, watch the next run.
4. Note: the same on-failure hook will fire if the FIX run also fails.
   That's the right behavior — every failure should be visible.

## Symptoms → likely causes

| Symptom | Likely cause | Fix |
|---|---|---|
| `Notify Discord on failure` step also fails | webhook secret missing/typo, or Discord rate-limited | Re-check `CI_FAILURE_DISCORD_WEBHOOK` in repo secrets; rate-limit is unlikely at our volume |
| Discord embed lacks `Author` | shallow checkout (depth=1) so `git log -1 --pretty='%an'` returns empty | Add `with: { fetch-depth: 2 }` to the `actions/checkout` step (low priority — we currently use the default depth=1 only in some workflows) |
| Embed posted but Kevin still got the email first | working as designed for the interim. Email is the failsafe. When the autonomous loop ships the email becomes informational. | none |
| Two Discord posts per failure (one from `.github`, one from `.forgejo`) | both runners are active. The embed footer disambiguates ("GitHub CI" vs "Forgejo CI"). | Operator can choose to disable one workflow if the duplication is noisy; for now both are useful as cross-checks |

## Configuration

| Variable | Where | Purpose |
|---|---|---|
| `CI_FAILURE_DISCORD_WEBHOOK` | GitHub repo secrets + Forgejo repo secrets | Discord webhook URL for the `#ci-failures` channel. If unset, the on-failure step logs a workflow warning and exits 0 (visibility hook becomes a no-op, but doesn't break CI). |

## Past incidents

### 2026-05-18 — CI run 26045493776 (the impetus)

Three sandbox tests failed on GitHub Actions after commit `fac4358`
(FU3c, strict DNS allowlist). The tests passed on Alienware and the
local mac because both can resolve `forgejo.dev-path.org`; GitHub
runners can't, exposing two latent bugs (default-arg early binding +
package-double-import). Kevin received the email from GitHub. There
was no Discord alert, no agent involvement; the human ended up being
the first responder.

Kevin's reaction (paraphrased): "I should be the second tier of any
alert. I don't mind getting the email, but I shouldn't have to be the
one to act on it first." This runbook + the workflow on-failure hooks
are the FU to that observation. Autonomous triage is queued as a
separately-scoped sprint (`platform_followup_ci_auto_triage`) because
its prereqs (sandbox-checks-on, agent:homelab PR identity, escalation
primitives) deserve real design rather than being hidden inside a
"webhook" change.
