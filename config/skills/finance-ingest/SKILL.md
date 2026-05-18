---
id: finance-ingest
name: Finance Statement Ingester
description: Parse a bank/credit-card/brokerage statement (CSV or PDF) into Beancount postings; emit a draft journal entry per transaction with provisional account assignments. STUB — full implementation lands in Sprint F4 of docs/plans/phase-1-finance.md.
local_only: true
required_tools:
  - memory.write
  - shell.git
  - shell.beancount
required_task_classes: [classify, summarize]
version: 1
---

# Finance Statement Ingester  (STUB)

This skill is declared so the agent:finance manifest validates ahead of
Sprint F4 (statement importer scaffold). It is **not yet implemented**.
Until F4 ships, any invocation should refuse with a clear message.

## Scope (when implemented)

You receive a single statement file (CSV or PDF text-extract) plus
operator-provided metadata:

- `institution` — e.g. `bmo-cash-back`, `discovercard`, `merrill-lynch`
- `account` — Beancount account, e.g. `Assets:BMO:Checking`
- `statement_period` — `YYYY-MM-DD/YYYY-MM-DD`

You produce:

1. A draft Beancount transaction per row with provisional category
   (`Expenses:Uncategorized:<heuristic>`).
2. A small summary table: count of rows, total inflow / outflow, any
   rows that did not parse cleanly (these block ingestion).
3. The output is **never written to the ledger directly** — it is handed
   to `finance-categorize` for per-row confirmation, then committed via
   the verifier loop.

## Local-only invariant

This skill is `local_only: true`. The gateway's LocalOnlyGuard
(`apps/_shared/litellm_callbacks/local_only_policy.py`) enforces this at
the per-call layer; the manifest declares the intent. **Statement
contents are personal financial data** and never leave the homelab.

## Tools

- `shell.beancount` — bean-check / bean-query for validation
- `shell.git` — commit drafts as a branch on the ledger repo
- `memory.write` — record the ingest event in `finance.ingest.*`

## Output

```json
{
  "draft_path": "~/finance/ledger/drafts/2026-05/bmo-checking.beancount",
  "tx_count": 42,
  "skipped_rows": [],
  "total_inflow_cad": 5234.12,
  "total_outflow_cad": 4912.05,
  "ready_for_categorize": true
}
```
