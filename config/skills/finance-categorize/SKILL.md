---
id: finance-categorize
name: Finance Transaction Categorizer
description: Categorize a draft Beancount transaction by proposing the destination expense/income account; run analyst+risk personas in MVP-B and full TradingAgents debate in MVP-C; verifier loop confirms before any ledger write. STUB — full implementation lands in Sprint F5 of docs/plans/phase-1-finance.md.
local_only: true
required_tools:
  - memory.write
  - memory.search
  - shell.beancount
required_task_classes: [classify, plan]
version: 1
---

# Finance Transaction Categorizer  (STUB)

This skill is declared so the agent:finance manifest validates ahead of
Sprint F5 (categorize loop + verifier integration). It is **not yet
implemented**.

## Scope (when implemented, MVP-B)

You receive a draft transaction (from `finance-ingest`) with provisional
category. You run a two-persona categorize loop:

1. **Analyst** proposes the destination account from the chart of
   accounts (`~/finance/ledger/accounts.beancount`), citing similar past
   transactions found via `memory.search` in `finance.transaction.*`.
2. **Risk** reviews the proposal for:
   - misclassification likelihood (vendor name + merchant category)
   - tax-bucket / cost-basis implications (mark for human review if any)
   - account-balance sanity (does this push an account negative?)
3. Verifier loop confirms the analyst proposal vs the risk critique;
   only on agreement is the row promoted from draft to committed.

In MVP-C the full TradingAgents debate (analyst + researcher + advisor +
risk) replaces this two-step.

## Local-only invariant

`local_only: true`. Every LLM call goes through the local route. See
`docs/plans/phase-1-finance.md` "Local-only enforcement" for the
three-layer defense.

## Output

```json
{
  "tx_id": "2026-05-15-bmo-12345",
  "decision": "commit|defer_to_human|hold",
  "proposed_account": "Expenses:Groceries",
  "confidence": "high|medium|low",
  "verifier_rounds": 1,
  "risk_notes": "..."
}
```
