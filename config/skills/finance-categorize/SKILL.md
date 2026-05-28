---
id: finance-categorize
name: Finance Transaction Categorizer
description: Categorize draft Beancount transactions via rule-based analyst + risk verifier; optional local LLM pass for sub-threshold rows (`categorize --llm`).
local_only: true
required_tools:
  - memory.write
  - memory.search
  - shell.beancount
required_task_classes: [classify, plan]
version: 2
---

# Finance Transaction Categorizer

MVP-B implementation in `apps/finance_agent/categorize/`. Full four-persona
debate remains MVP-C (`finance-advise`).

## Scope (MVP-B)

1. **Analyst** proposes category from `policy.yaml` (account roles, context
   rules, merchant regexes). With `--llm`, rows below `threshold` (default
   0.85) call the local LiteLLM gateway using this skill id
   (`finance-categorize`).
2. **Risk** verifies the proposal (`apps/finance_agent/categorize/risk.py`) —
   valid account prefix, non-empty description, confidence ≥ threshold.
3. Verifier loop (`apps/_shared/verifier`) allows one revise round; failures
   defer (leave `!` on `Expenses:Uncategorized`).
4. Approved rows promote to `*` and swap the counter leg; metadata:
   `categorize_confidence`, `categorize_run`.

## CLI

```bash
python3 -m apps.finance_agent --skip-boot categorize [--limit N] [--dry-run]
python3 -m apps.finance_agent --skip-boot categorize --llm [--limit N]
```

Requires `MODEL_GATEWAY_BASE_URL` / `MODEL_GATEWAY_API_KEY` for `--llm`.
Set `AGENT_PRINCIPAL=agent:finance` for gateway attribution.

## Local-only invariant

`local_only: true`. Every LLM call goes through the local route. See
`docs/plans/phase-1-finance.md` "Local-only enforcement" for the
three-layer defense.

## Output (batch summary)

Human: `✓ categorize … scanned=N approved=A deferred=D` plus per-row lines.
Audit: `finance_categorize` event in agent-finance `audit.jsonl`.
