---
id: render-plan
name: Plan Renderer
description: Convert a card brief into a clear, reviewable plan document.
local_only: false
required_tools: []
required_task_classes: []
version: 1
---

# Plan Renderer

Render a Planka card brief into a markdown plan. The plan is what the human
reads before approving execution; it goes in the card description (initial
draft) or as a comment (revised drafts).

## Required structure

```
## Goal
One sentence.

## Approach
2–4 bullets describing the approach.

## Files affected
List of repo paths the work will touch (read or write).

## Acceptance
2–4 testable criteria. Each must be objectively checkable.

## Risks
Each risk + a one-line mitigation.

## Rollback
How to undo this if it lands and breaks.
```

## Style

- Use imperative voice. ("Pin image to v8.0.3", not "We will pin...".)
- Cite repo paths with backticks.
- Never include credentials, tokens, or PII even by example.
- Keep total length under 2000 chars unless the card is genuinely complex.
