---
id: finance-advise
name: Finance Advisor
description: Produce advisory-only narrative answers about the ledger (cash flow, account balances, category breakdowns, account-balance projections). NEVER mutates the ledger. STUB and DISABLED in MVP-B; activated in MVP-C with the full TradingAgents debate. See Sprint F8 of docs/plans/phase-1-finance.md.
local_only: true
required_tools:
  - memory.search
  - shell.beancount
required_task_classes: [summarize, plan]
version: 1
---

# Finance Advisor  (STUB — disabled in MVP-B)

This skill is declared so the agent:finance manifest validates and so
the gateway's `local_only` enforcement covers it the moment it goes
live. **It is NOT enabled in MVP-B.** The agent's runtime check refuses
invocation until MVP-C ships.

## Scope (when implemented, MVP-C)

You answer free-form advisory questions about the user's ledger:

- "How much did I spend on groceries in April?"
- "What's my Tax-Free Savings room left for 2026?"
- "Project my Q3 cash balance given current spend trends"

The full TradingAgents debate pattern runs:

1. **Analyst** queries the ledger via `bean-query` and produces a
   numerical baseline.
2. **Researcher** investigates context (e.g. "is this month seasonally
   high?" by comparing to prior years).
3. **Advisor** synthesizes the analyst + researcher into a recommendation
   or answer.
4. **Risk** challenges the synthesis for blind spots (e.g. unpaid bills
   not yet in the ledger, FX-rate assumptions, tax-bucket misclassification).
5. Verifier loop confirms the advisor's answer survives the risk
   challenge before any answer leaves the agent.

## Read-only invariant

This skill **never writes to the ledger**. The agent enforces this at
the tool layer: `tools` for this skill explicitly omit `shell.git` write
operations.

## Local-only invariant

`local_only: true`. Personal financial detail is the most sensitive
data the agent handles.

## Output

```json
{
  "question": "How much did I spend on groceries in April?",
  "answer": "...",
  "evidence": [{"bean_query": "...", "result": "..."}],
  "advisor_confidence": "high|medium|low",
  "risk_concerns": ["..."],
  "verifier_rounds": 1
}
```
