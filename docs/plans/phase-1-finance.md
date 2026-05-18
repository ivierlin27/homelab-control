# Phase 1: agent:finance (local-only proof)

Phase 1 builds the first domain agent on top of the Phase 0 platform.
Goal: replace the unaudited "Kevin manually moves statement PDFs into
a spreadsheet, sometimes" loop with a sandboxed, local-only agent that
ingests bank/credit/brokerage statements, posts double-entry Beancount
entries, agent-categorizes new transactions through a verifier loop,
and surfaces the whole thing through Fava behind Authentik. The agent
is **advisory-only** — it never moves money, never reaches a public
API on its own, and is read-only from Discord.

Phase 1 is the first time we exercise the platform's "sensitive
namespace" promises end-to-end (local-only routing, read-only Discord,
verifier-loop required for advice, escalation routed to human DM on
verifier failure). If we cannot make those promises hold for finance,
the rest of the vision is a marketing document.

---

## Decisions locked at planning time

These are not open for re-litigation inside a sprint. Re-open via an
explicit "we got this wrong" pass.

| Decision | Choice | Why |
|---|---|---|
| Ledger format | **Beancount** v3 | Python-native (drops into the agent stack with no shell-out), `smart_importer` does ML-style categorization out of the box and learns from history, Fava is best-in-class UI, parser hands you a structured object so the verifier loop is straightforward. hledger was the real alternative and remains the right choice in a CLI-first hand-edit-the-journal world — that's not this world. |
| Web UI | **Fava** behind Authentik at `fava.dev-path.org`, dedicated LXC | Best UI for plain-text accounting; integrates with Beancount natively; SSO mirrors the Planka/Forgejo/Khoj pattern; deployed as its own LXC for blast-radius isolation (a Fava compromise doesn't reach the agent's queue or memory engine). |
| Ledger storage | `~/finance/ledger/` on Alienware, git-tracked in a **private Forgejo repo** (`finance-ledger`), encrypted at rest via LUKS on the host filesystem | Git gives audit history independent of the audit ledger; private Forgejo keeps the repo off-LAN-internet; LUKS means a stolen laptop doesn't leak. Decrypted only while the agent is active. |
| Routing posture | `routing_policy.local_only: ["*"]` — **literally everything stays local** | Sensitive enough that even classification + summarization must not leave the home network. Enforced at the LiteLLM gateway via `skill.local_only=true` (blocked from routing to cloud models). |
| Discord posture | **Read-only DM with Kevin only** | Discord can invoke analysis / classification / summarization / report. Any tool call that would mutate the ledger (commit a transaction, edit metadata, override a category) is rejected at the bridge based on call origin = `discord`. Mutations must originate from CLI or the master dashboard. |
| Operating currency | **CAD as primary** for reporting (Kevin's tax-and-life-home is Canada); USD positions tracked as commodities and revalued at month-end | Real Beancount complexity for multicurrency setups. Defer "live FX rates per-transaction" and use month-end snapshots from `pricer` plugin via Bank of Canada noon rates. Investment cost-basis stays in the position's native currency. |
| Trading agent pattern | **TradingAgents-style debate (analyst → researcher → advisor → risk)**, **advisory-only**, no live trading ever | Multi-persona debate is the pattern; the implementation lives inside `apps/finance_agent/`, not at the platform layer. Live trading is permanently out of scope. |
| Verifier loop | **Required** for any `finance.advise` and `finance.categorize` call | Per Phase 0.4 vision and Phase 0.11 escalation policy (`finance.advise` skips Tier 2, goes straight to human on verifier failure). |

## Non-goals (for Phase 1 entirely, not just MVP)

- No automated trading. Ever. TradingAgents is a debate pattern, not a trade execution pattern.
- No tax filing automation (Phase 5+ at earliest; tax-time export of Schedule X / T4 / RRSP slips is in scope, FILING IS NOT).
- No bill-pay automation (read-only ledger, period).
- No multi-user. Finance is a Kevin-only namespace until Phase 4 (profile) defines the family/wife consent model.
- No "predictive cash flow" / "spending forecast" until MVP-B has been live for ≥3 months of real data.
- No crypto cost-basis tracking in MVP. Crypto.com + Coinbase live in Tier 3 (manual entry) until pain justifies a separate sprint.

---

## Status

| Item | Status |
|---|---|
| Phase 0 prereqs closed | partial — see Prereqs below |
| `agent:finance` identity issued | not started (blocked on identity-followup-runbook-stub) |
| `apps/finance_agent/` skeleton | not started |
| Ingest pipeline (`apps/finance_agent/ingest/`) | not started |
| Beancount chart of accounts drafted | not started |
| Tier-1 importers | not started |
| Categorization loop | not started |
| Fava LXC | not started |
| Persona prompts | drafted in this document, not yet wired |
| Phase 1 plan document | **this file** |

---

## Prereqs (strict serial order)

Per Kevin's 2026-05-18 decision, no `apps/finance_agent/` code lands
until every item below is `done`. The reasoning is concentric: each
prereq enforces a security property that finance specifically needs,
and shipping finance without them would mean a sensitive namespace
operating on weaker guarantees than the vision requires.

1. **`skills_followup_local_only_gateway`** — LiteLLM gateway must
   refuse cloud routes when the caller's skill declares
   `local_only: true`. Wired via an `x-skill: <skill-id>` header on
   gateway requests + a policy lookup that rejects with audit row +
   4xx if the resolved model is non-local. Without this, an
   accidental cloud call inside finance is a silent data leak; the
   audit row catches it after the fact, but "after the fact" is too
   late for a sensitive namespace. Acceptance: a test that sets
   `x-skill: finance.categorize` and asks for `gpt-4o-mini` gets a 4xx
   with reason `local_only_violation`, AND a `local_only_block` audit
   row.
2. **`identity_followup_runbook_stub`** — `python -m
   apps._shared.identity plan --principal-stub <manifest-yaml>` so we
   can produce `docs/identity-runbook-agent-finance.md` *before*
   `agent:finance` lands in `registry.yaml`. Without this we'd be
   editing the registry to generate the runbook, which is the wrong
   order (the runbook is supposed to guide the registry edit, not the
   other way around).
3. **`agent:finance` identity end-to-end** — walk the runbook:
   - SSH key on Alienware (`~/.ssh/agent-finance`)
   - Forgejo bot account `agent-finance@dev-path.org`, push access to
     the private `finance-ledger` repo only
   - Discord bot registered in the existing guild; bot manifested
     into `agent-executive`'s channel allowlist with `mode: read`
     against a new `#finance` channel (DM-only origin policy still
     enforced at the bridge)
   - Infisical token scoped to `finance/*` secrets only
   - Per-principal sandbox image
     (`apps/_shared/sandbox/images/agent-finance.Containerfile`) built
4. **`platform_escalation` minimal** — at least
   `config/escalation.yaml` defines the `finance.advise` and
   `finance.categorize` task classes with their Tier 1 budgets and
   "skip Tier 2, go to human DM on verifier failure" policy. The
   wider Phase 0.11 escalation primitive can stay scaffold-level
   elsewhere, but these two task classes must work.
5. **Verifier-loop primitive (Phase 0.4)** — must be implemented and
   tested in at least one non-finance context (e.g., a synthetic
   categorize-this-test-transaction) before finance depends on it.
   Not strictly blocking MVP-B if the loop is simple enough (we can
   inline it), but strongly preferred so finance isn't the first
   real customer of an unproven primitive.

When all of the above are `done`, the sprint can start. Not before.

---

## Architecture

```
apps/finance_agent/
├── __init__.py
├── main.py                  # CLI entry: ingest, categorize, advise, status
├── ingest/
│   ├── __init__.py
│   ├── registry.py          # importer registry, dispatch by institution
│   ├── importers/
│   │   ├── __init__.py
│   │   ├── bmo_chequing.py
│   │   ├── bmo_cashback.py
│   │   ├── boa_checking.py
│   │   ├── amplify_cu.py
│   │   ├── rbc_visa.py
│   │   ├── discover.py
│   │   ├── capital_one.py
│   │   └── _shared/ofx_base.py
│   └── inbox/               # operator drops statement files here
├── categorize/
│   ├── __init__.py
│   ├── classifier.py        # smart_importer wrapper
│   ├── verifier.py          # verifier-loop integration
│   └── policy.yaml          # account → category rules, confidence thresholds
├── personas/
│   ├── analyst.md           # full system prompt
│   ├── researcher.md
│   ├── advisor.md
│   └── risk.md
├── debate/
│   ├── __init__.py
│   ├── runner.py            # orchestrates the 4-persona debate
│   └── transcript.py        # writes debate to audit log + Planka card
└── tests/

config/
├── agents/agent-finance.yaml         # manifest
├── escalation.yaml                   # finance.advise + finance.categorize
└── skills/finance/                   # skill manifests w/ local_only: true
    ├── ingest-statement.yaml
    ├── categorize-transaction.yaml
    └── advise.yaml

~/finance/ledger/                     # NOT IN REPO
├── main.beancount                    # top-level include file
├── accounts.beancount                # chart of accounts
├── commodities.beancount             # CAD, USD, BTC, ETH, plus equities
├── prices/                           # pricer plugin output
├── 2026/                             # year-partitioned transaction files
│   ├── bmo-chequing.beancount
│   ├── boa-checking.beancount
│   └── ...
└── .git/                             # private Forgejo `finance-ledger` repo
```

### Data flow

```
1. Statement drop (manual or future fetcher)
   ↓
   apps/finance_agent/ingest/inbox/<institution>-<yyyymm>.ofx
   ↓
2. CLI: python -m apps.finance_agent ingest --institution bmo-chequing --file <path>
   ↓ Sandbox: agent-finance image, --network=none, /etc/hosts hardened
   ↓
3. smart_importer + per-institution OFX/CSV parser → candidate Beancount entries
   ↓
4. Categorizer (analyst persona) proposes Expenses:* / Income:* / Liabilities:* postings
   ↓
5. Verifier (risk persona) re-checks each entry; flags low-confidence
   ↓ if all entries above threshold (e.g., 0.85 conf):
        bulk-approve, write to ~/finance/ledger/2026/<institution>.beancount
   ↓ if any below threshold:
        post to Planka finance board as one card per ambiguous entry, await Kevin
        (Discord DM to Kevin with summary + link to Fava query for context)
   ↓
6. `bean-check ~/finance/ledger/main.beancount` runs as a gate. Failure rolls back the entry batch.
   ↓
7. Audit row: `finance_ingest` { institution, file_hash, entries_added,
                                 entries_deferred, verifier_rounds, correlation_id }
   ↓
8. Git commit on ledger repo, push to private Forgejo finance-ledger.
   ↓
9. Discord DM summary: "Ingested N entries from BMO Chequing for Apr 2026.
                        M entries deferred for review — see Planka board."
```

`advise` is a separate, opt-in path (MVP-C), not chained into ingest.

---

## Ingest pipeline (MVP-B detail)

### Operator-facing UX

```bash
# Drop a statement
cp ~/Downloads/bmo-chequing-202604.ofx \
   ~/finance/inbox/bmo-chequing-202604.ofx

# CLI runs in the agent's sandbox
python -m apps.finance_agent ingest \
  --institution bmo-chequing \
  --file ~/finance/inbox/bmo-chequing-202604.ofx \
  --dry-run                          # show what would be added; no write
```

Or operator-less mode after MVP-B settles:

```bash
# inbox-watcher service that auto-runs ingest on new files
systemctl --user start alienware-finance-inbox-watcher.service
```

### Beancount conventions

These are decided now to avoid re-litigation per-importer.

| Convention | Choice |
|---|---|
| Operating currency | CAD |
| Account naming | `Assets:Bank:<Country>:<Institution>:<Account>` (e.g., `Assets:Bank:CA:BMO:Chequing`) |
| Liability accounts | `Liabilities:CC:<Country>:<Institution>:<Card>` |
| Income | `Income:Salary:<Source>`, `Income:Interest:<Source>`, `Income:Dividends:<Source>`, `Income:Capital-Gains:<Account>` |
| Expense categories | Initial set tracks roughly the categories `smart_importer` learns from; refine over the first 90 days |
| Commodities | CAD, USD, BTC, ETH plus ticker symbols (VOO, etc.). Declared in `commodities.beancount`. |
| Cost basis | Tracked in the position's native currency. CAD-USD conversion happens at report time via month-end Bank of Canada rates. |
| Transaction metadata | Every txn carries `import_source: <institution>`, `import_run: <correlation_id>`, `confidence: <float>` |
| Year partitioning | One file per year per institution under `<year>/<institution>.beancount`. Top-level `main.beancount` does `include` directives. |

### Categorization loop

```python
# pseudocode for apps/finance_agent/categorize/classifier.py
def categorize(entry: Posting, *, threshold: float = 0.85) -> CategorizeResult:
    """Returns one of:
      - (Approved, category, confidence)            confidence >= threshold
      - (NeedsReview, top_3_candidates, conf_each)  confidence < threshold
      - (Failed, reason)                            classifier raised
    """
    # 1. analyst persona classifies (smart_importer)
    classification = analyst.classify(entry)

    # 2. risk persona verifies
    verdict = risk.verify(entry, classification)
    if verdict.kind == "ok":
        return Approved(classification.category, classification.confidence)
    elif verdict.kind == "revise":
        # one re-round; risk gets to push back ONCE
        revised = analyst.reclassify(entry, hint=verdict.reason)
        verdict2 = risk.verify(entry, revised)
        if verdict2.kind == "ok":
            return Approved(revised.category, revised.confidence)
    # else: defer to human
    return NeedsReview(classification.top_3, classification.confidence)
```

This is **two-persona for MVP-B** (analyst + risk), not four. The
researcher + advisor personas don't activate for categorization; they
activate for the **advise** path in MVP-C.

---

## Personas (full system prompts)

All four personas are drafted now per Kevin's 2026-05-18 preference
("draft full system prompts now — it's design work, do it once").
MVP-B uses **analyst + risk only**; researcher + advisor activate in
MVP-C.

Each persona is a system prompt loaded from `apps/finance_agent/personas/<name>.md`
at construction time and pinned (the agent CANNOT replace or override
its own system prompt — that's a Phase 0.4 verifier-loop invariant).

### Persona: analyst

```text
You are the analyst persona of agent:finance, a local-only finance
agent for Kevin's personal household. Your job is to look at financial
data — transactions, holdings, statements — and produce ACCURATE,
SOURCED, NON-OPINIONATED descriptions of what happened.

You produce facts, never advice. You categorize transactions by their
most likely intent based on memo, payee, amount, recurrence, and
historical context. You report holdings at their reported value. You
flag anomalies (unusual amounts, new payees, recurring transactions
that suddenly stopped) but you do not interpret them.

Your output is always structured: a category proposal includes the
category, your confidence (0.0–1.0), the top three alternative
categories with their relative likelihoods, and the specific evidence
that drove the decision (e.g., "payee 'SHELL' + amount $74.23 + prior
12 transactions to SHELL all categorized as Expenses:Auto:Fuel").

You DO NOT:
- offer investment advice, even when asked
- speculate about market direction
- assume Kevin's intent — when ambiguous, say so and surface the
  ambiguity for the risk persona to verify
- modify the ledger directly; you propose, others commit
- ignore the operating currency conversion — when a posting is in USD
  and you cite a CAD comparison, you note the FX rate used and its
  source

Your tone is concise and technical. When uncertain, you say "I am
uncertain because <specific reason>." You do not pad with disclaimers.

Boundaries:
- You operate under `routing_policy.local_only: ["*"]`. Every call you
  make is satisfied by a local model. If a downstream call would
  require a cloud model, the gateway will reject it and you will
  surface that as an explicit error to the orchestrator, not a silent
  fallback.
- You have NO network access at all in the sandbox (`--network=none`
  inherited from `agent:finance` manifest). Your only inputs are the
  files mounted into your worktree.
- You do not have access to the ledger itself unless explicitly passed
  in your context. The orchestrator decides what slice of history you
  see.
```

### Persona: researcher (activates MVP-C)

```text
You are the researcher persona of agent:finance. The analyst has
produced facts; your job is to provide RELEVANT CONTEXT that turns
facts into a basis for decisions.

When the orchestrator asks "should Kevin rebalance X?" or "how does
Kevin's current cash position compare to historical norms?", you:

1. Pull the analyst's structured output for the question's time range.
2. Combine with historical context already in the ledger
   (year-over-year, prior-quarter trends, recurring patterns).
3. Identify the 2–4 factors that materially shape the answer.
4. Produce a structured "context report" with: question restated,
   the factors, supporting numbers, and a one-line summary of "what
   this means for the advisor's deliberation."

You DO NOT:
- recommend an action — that's the advisor's job
- include external market commentary (you have no network access)
- use jargon Kevin wouldn't recognize unless you define it inline
- speculate about future returns or rates
- exceed a context window of 8K tokens — if the question requires more
  context, you say so and ask the orchestrator to narrow the scope

Your tone is informational, not persuasive. You write as if briefing a
careful executive who will make the decision.

Boundaries: same as the analyst — local-only, sandboxed, read-only
from your perspective.
```

### Persona: advisor (activates MVP-C)

```text
You are the advisor persona of agent:finance. The analyst gave facts,
the researcher gave context, and now you propose a RECOMMENDATION.

Your recommendation is structured:

  RECOMMENDATION: <one sentence, imperative voice>
  CONFIDENCE: <low | medium | high>
  RATIONALE: <2-4 sentences citing the analyst's facts and researcher's
              context>
  ASSUMPTIONS: <bulleted, what would have to remain true for this to
                hold up>
  WHAT-IFS: <bulleted, 1-3 specific things that would change your
             recommendation>
  ALTERNATIVES CONSIDERED: <bulleted, the 1-2 paths you rejected and
                            why>

You DO NOT:
- present a single recommendation as if it were the only option
- omit ASSUMPTIONS or WHAT-IFS — those are how Kevin grades you later
- use the phrase "Most experts say" or any equivalent appeal to
  external authority. You don't have external network access; if a
  fact isn't in the context the researcher gave you, you don't have
  it.
- recommend any action that would move money outside the ledger (tax
  filing, fund transfers, trades). You can recommend Kevin do those
  things; you cannot do them.
- speculate about market direction more than is necessary to ground
  your recommendation. "If interest rates rise, the calculus changes"
  is OK; "interest rates will rise" is not.

Your tone is decisive but humble. Recommendations are clear, the
caveats are loud.

Your output goes to the risk persona for challenge BEFORE it ever
reaches Kevin.

Boundaries: same as the analyst.
```

### Persona: risk

```text
You are the risk persona of agent:finance. Your job is the LAST LINE
OF DEFENSE. Everything that reaches Kevin must first survive your
challenge.

When the analyst proposes a categorization (MVP-B path) or the advisor
proposes a recommendation (MVP-C path), you receive their output and
ask:

1. What is this output's WORST plausible interpretation? Could the
   action — if Kevin takes it on faith — harm Kevin's financial
   position, security, or future optionality?
2. What evidence is the output relying on? Is the evidence solid, or
   is it an extrapolation from a small sample?
3. What would be true if the output is WRONG? Is it a $5 miscategorized
   coffee (rollback cost: tiny), or is it "shift 30% of the portfolio
   to bonds" (rollback cost: huge)?
4. Is there a specific bias you should challenge — recency bias,
   anchoring on the most-recent statement, optimism on the user's own
   trajectory, etc.?

You produce a verdict, structured:

  VERDICT: <ok | revise | escalate>
  CHALLENGE: <the specific concern, in plain language>
  REQUEST: <if revise: what the analyst/advisor should reconsider; if
             escalate: which human decision this needs Kevin to make>

Categorization path (MVP-B): you get ONE re-round with the analyst.
After that, defer to human (NeedsReview).

Advise path (MVP-C): you get ONE re-round with the advisor. After
that, escalate to Kevin via Discord DM with the full debate transcript
attached (per Phase 0.11 escalation policy, `finance.advise` skips
Tier 2 entirely).

You DO NOT:
- rubber-stamp. If the analyst's confidence is 0.86 and the threshold
  is 0.85, that's borderline — ask a real question.
- engage in your own analysis. You challenge; you don't propose.
- speculate about the analyst's or advisor's reasoning when their
  output is explicit. Cite their actual text.

Your tone is skeptical but professional. You are the person at the
table who says "wait, what if?"

Boundaries: same as the analyst.
```

---

## Local-only enforcement

Three layered defences. Belt-and-suspenders is the right posture here.

1. **Manifest declaration**: `agent-finance.yaml` has
   `routing_policy.local_only: ["*"]`. All skills under
   `config/skills/finance/` set `local_only: true`.
2. **Gateway enforcement**: per `skills_followup_local_only_gateway`
   prereq, the LiteLLM gateway rejects any call with
   `x-skill: finance.*` if the resolved model is non-local. Audit
   row `local_only_block` on rejection.
3. **Sandbox defense**: `agent-finance`'s sandbox has
   `--network=none` for ingest and categorize; categorize calls hit
   the local gateway via Unix socket (the gateway runs on the host,
   not in the sandbox). The sandbox has NO IP route to any cloud
   endpoint.

A bug in any single layer is recoverable. A bug in two of three is a
P0 alert. All three failing means the architecture has a hole.

---

## Discord surface

| Channel | Mode | Notes |
|---|---|---|
| `#finance` | **read-only** for `agent:finance` from Discord origin (no mutations from Discord); Kevin can DM the agent for analysis | New channel — Kevin creates manually before identity issuance; webhook ID added to `agent-executive`'s manifest as `mode: read` to match the existing channel-from-registry pattern |
| Kevin's DM | bidirectional but tools filtered by origin | The agent can DM Kevin (escalations + ingest summaries). Kevin can DM the agent for analysis. Tools that would mutate state are filtered out at the bridge based on `origin = "discord-dm"`. |

No other channels. Finance is NEVER mentioned in `#homelab`,
`#ops`, `#approvals` (excepting Tier-3 escalations which route to a
Kevin DM, not to `#approvals`). This is enforced by the
manifest-driven channel allowlist already shipped in `FOLLOWUP 2a`
(commit `fac4358` base).

---

## Fava deployment

```yaml
# config/inventory/services/fava.yaml  (new)
service: fava
host: alienware
runtime: podman
image: yegle/fava:latest          # community image; pin to digest before going live
data_mounts:
  - host: ~/finance/ledger
    container: /bean
    readonly: true                # Fava reads only; it doesn't write the ledger
network:
  expose: 5000
  reverse_proxy:
    domain: fava.dev-path.org
    sso: authentik                # OIDC, same realm as Planka/Forgejo/Khoj
backup_class: derived             # data is in ~/finance/ledger which has its own class
```

Read-only is important: Fava can mutate the ledger via its query
interface if writable. Kept read-only so the only writers are
`apps/finance_agent/` and Kevin via CLI.

---

## Backup integration

`~/finance/ledger/` already on the restore-order critical path per
the umbrella vision (line 365, 368). Confirm:

- restic timer `alienware-restic-finance.timer` (daily, 02:23 PT, same
  family as the other agent-data timers)
- restic pre-hook does `git -C ~/finance/ledger status --porcelain` to
  refuse a snapshot if there are uncommitted changes (commit hygiene
  enforced)
- restic post-hook does `git -C ~/finance/ledger push origin main` to
  the private Forgejo repo (off-host mirror of the ledger)
- DR drill script extended: restore ledger, run `bean-check`, refuse
  to pass if the chain doesn't balance

---

## Sprint sequence

Each sprint is sized for one focused work session (~3 days each, give
or take).

| Sprint | Title | Output | Acceptance |
|---|---|---|---|
| **F1** | Identity issuance | `agent:finance` runbook generated; SSH key, Forgejo bot, Discord bot, Infisical token, sandbox image all issued | `python -m apps._shared.identity plan --principal agent:finance --output docs/identity-runbook-agent-finance.md` shows all components ISSUED |
| **F2** | Skeleton + manifest | `apps/finance_agent/main.py` with `status` subcommand; `config/agents/agent-finance.yaml`; `config/skills/finance/*.yaml`; sandbox image builds | `python -m apps.finance_agent status` runs in the sandbox and prints "agent:finance v0.1 — no ledger initialized" |
| **F3** | Ledger init + chart of accounts | `~/finance/ledger/` initialized; `accounts.beancount`, `commodities.beancount`, `main.beancount` committed to private Forgejo repo; CAD/USD declared; placeholder accounts for all 14 institutions | `bean-check ~/finance/ledger/main.beancount` passes |
| **F4** | First Tier-1 importer (BMO chequing) | OFX importer wired through smart_importer; CLI `ingest --institution bmo-chequing --file …`; sandboxed; audit row written | One real BMO statement ingests without manual edits, all entries land in the ledger, `bean-check` passes, `finance_ingest` audit row written |
| **F5** | Remaining Tier-1 importers | BoA, Amplify CU, BMO Cash Back, RBC Visa, Discover, Capital One | Same acceptance per institution; each importer has a fixture + test |
| **F6** | Categorization loop (analyst + risk) | `apps/finance_agent/categorize/` with two-persona loop; verifier-loop primitive integrated; threshold + revise + defer paths all exercised | Real statement re-ingests with categorization on; high-confidence entries auto-land, low-confidence routed to Planka finance board + Kevin DM |
| **F7** | Fava deployment | Fava LXC behind Authentik at fava.dev-path.org; finance tile on master dashboard linking through | Kevin can SSO into Fava, see this month's transactions, filter by category |
| **F8** | Inbox watcher + automation | `alienware-finance-inbox-watcher.service`; drop a statement file → auto-ingest → DM summary | One full cycle works without manual CLI; metrics show in `#finance` DM |
| **F9 (MVP-B complete)** | Soak + tune | Run for 30 days, tune categorization thresholds, fix importer edge cases, fill out reusable category rules | Categorization confidence > 0.85 on ≥80% of real transactions |
| **F10+ (MVP-C)** | Researcher + advisor personas | The other two personas; `advise` CLI; Planka finance board for advice cards; weekly digest piped into executive review | Out of scope of this document — separately scoped sprint with its own acceptance |

---

## Institution coverage (tiered)

| Tier | Institution | Country | Account | MVP-B sprint | Importer strategy |
|---|---|---|---|---|---|
| 1 | BMO | CA | Primary chequing + savings | F4 | OFX (BMO supports Quicken export) |
| 1 | Bank of America | US | Checking | F5 | OFX |
| 1 | Amplify CU | US | Whatever account exists | F5 | Likely CSV; OFX if available |
| 1 | BMO Cash Back | CA | Credit card | F5 | OFX |
| 1 | RBC Visa | CA | Credit card | F5 | OFX |
| 1 | DiscoverCard | US | Credit card | F5 | OFX |
| 1 | Capital One | US | Credit card | F5 | **CSV only** (Capital One dropped OFX/Quicken integration ~2018) |
| 2 | Merrill Lynch | US | Brokerage | post-MVP-B | OFX + CSV for cost-basis |
| 2 | Vanguard | US | Brokerage | post-MVP-B | CSV; cost-basis import needs care |
| 2 | Etrade | US | Brokerage | post-MVP-B | CSV |
| 2 | Wealthsimple (need confirm of "wealthserv") | CA | RRSP | post-MVP-B | CSV only |
| 3 | Tangerine | CA | Inactive | manual placeholder | N/A — single zero-balance entry at ledger init |
| 3 | iA Financial | CA | RESP | manual entry | PDF only; quarterly manual reconcile until pain justifies a custom importer |
| 3 | First National | CA | Mortgage | manual entry | PDF statements; monthly manual reconcile |
| 3 | Webull | US | Brokerage | manual entry | CSV exists but low volume; defer |
| 3 | Crypto.com | — | Crypto | manual entry | Crypto cost-basis is its own subdiscipline; defer indefinitely |
| 3 | Coinbase | — | Crypto | manual entry | Same |

**Decision needed before F4 starts**: confirm the spelling of
"wealthserv" — best guess is Wealthsimple, but Wealthserv could be a
different platform (e.g., a managed-account portal). If it's truly
something I haven't heard of, F4 may need to reorder.

---

## Open questions / decision queue

These don't block writing this plan but should be resolved before the
sprints they affect.

| Question | Affects | Default if not answered by sprint start |
|---|---|---|
| "Wealthserv" — is that Wealthsimple, or a separate platform? | F5 (Tier 2 brokerage import) | Treat as Wealthsimple |
| Should the ledger repo be public-readable (private Forgejo) or fully private even from other agents on the LAN? | F3 | Fully private; only `agent:finance`'s identity has access |
| Currency conversion frequency: real-time per-transaction (more accurate, more API calls) vs. month-end (simpler, slightly less accurate)? | F3, F6 | Month-end, Bank of Canada noon rates |
| LUKS-encrypted partition vs. ecryptfs vs. just rely on FDE? | F3 | LUKS on a dedicated partition mounted at `~/finance/` |
| Authentik realm for Fava: shared with Planka/Forgejo, or its own with stricter MFA? | F7 | Shared realm; Kevin can decide later if MFA tightening is warranted |
| Smart_importer learning store: where does the training data live? Same git repo as ledger? | F6 | Same repo, under `~/finance/ledger/.smart_importer/`, gitignored |
| MVP-B → MVP-C trigger: time-based (3 months soak) or quality-based (categorization confidence > X on Y% of txns)? | F10+ | Quality-based: >0.85 confidence on ≥80% of real transactions for 30 consecutive days |

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `smart_importer`'s categorization is poor on Kevin's actual transaction history | medium | Slows MVP-B by 1-2 sprints | F6 starts with `bean-extract` + manual review of first 100 entries to seed the classifier; F9 soak is explicitly there to tune |
| Capital One's CSV format changes silently | high | One importer breaks | Importer tests on a fixture; nightly Alienware test catches breakage within 24h |
| Multicurrency math at month-end is wrong | medium | Reports lie | Verifier loop spot-checks USD-CAD conversion against Bank of Canada published rates; deliberate test case per month |
| Local-only gateway has a hole | low | Silent cloud routing of sensitive data | Triple-layer defense (manifest, gateway, sandbox); audit row review weekly |
| Beancount v3 ships a breaking change mid-sprint | low | Days of yak-shaving | Pin version in `apps/_shared/sandbox/images/agent-finance.Containerfile`; treat upgrades as a separate sprint |

---

## What this plan deliberately leaves to other documents

- **Verifier-loop primitive design** — that's Phase 0.4, lives in
  `docs/plans/phase-0-platform.md` §0.4. This plan consumes it.
- **Identity issuance mechanics** — `apps/_shared/identity` is the
  source of truth; this plan generates `docs/identity-runbook-agent-finance.md`
  via that tool when F1 starts.
- **Audit row format** — `apps/_shared/audit` defines the hash chain;
  this plan adds the `finance_ingest`, `finance_categorize`,
  `finance_advise`, `local_only_block` event types.
- **Skill registry** — `apps/_shared/skills`; this plan adds three
  skill manifests under `config/skills/finance/`.

If a section of this plan disagrees with `docs/VISION.md` (when that
file lands per the umbrella plan's section 6 wrap-up), VISION wins.
Phase 1 is a refinement, not an authority.
