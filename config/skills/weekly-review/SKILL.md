---
id: weekly-review
name: Weekly Operating Review
description: Summarize the past week of agent activity, trust events, blocked actions, and proposed memory writes for human review.
local_only: false
required_tools:
  - memory.search
required_task_classes: [summarize]
version: 1
---

# Weekly Operating Review

Generate a short, scannable review of the past 7 days of agent activity.
Optimize for "Kevin reads this in 90 seconds and knows where attention is
needed." Pull from the trust ledger, lifecycle events, and recent memory
proposals.

## Sections (in this order, omit empty ones)

1. **Headline** — one sentence that states the week's most important change.
2. **Completed work** — Planka cards moved to Done, with one-line each.
3. **Delegated** — A2A hops between agents that produced a result.
4. **Trust events** — promotions, demotions, blocked-sensitive actions.
5. **Shield events** — prompt-injection attempts, secret-leak attempts.
6. **Backlog health** — queues with depth > 5, oldest item age, failed jobs.
7. **Memory proposals** — accepted / rejected / superseded counts plus the 3
   most consequential proposals by hand.
8. **Trends** — at most 3 bullets about a multi-week pattern, only if real.

## Constraints

- Do not include the full transcript of any agent run; link to it.
- Do not propose actions in the review. The review is a status surface;
  actions live in cards.
- Numbers must be sourced; never invent counts.
- Keep total length under 1500 chars when possible.

## Output

Plain markdown ready to paste into Discord and the dashboard.
