---
id: planka-card
name: Planka Card Updater
description: Update the Planka card driving the current task by appending comments, never rewriting the description.
local_only: false
required_tools:
  - planka.comment
required_task_classes: []
version: 1
---

# Planka Card Updater

You are working a task that is anchored to a Planka card. The card description
is the durable spec — the original intent, the agreed plan, and links to the
Forgejo PR or other artifacts. Treat it as immutable.

Every status update you produce is a **comment** on the card, attributed to
your agent identity. Comments form the visible history; the description does
not change as work progresses.

## When to comment

Add a comment when any of these happen:

- you start working the card (one-line "started")
- you produce a plan, a PR link, or a verifier verdict
- you hit a blocker or escalate
- you finish (one-line "done", with a link to the resulting artifact)

## Comment shape

Use plain markdown. Lead with a one-line summary. Then optionally a short
detail block. Do not paste full transcripts — link to them in the audit log
instead.

Example:

```
Plan ready. Drafted at <link to plan>. Three files touched, all docs.
```

```
PR opened: <link>. Ready for review-agent to inspect.
```

```
Verifier failed twice; escalating to human via #approvals.
```

## Moving the card

You may call `planka.move_to_list` only when:

- you are explicitly transitioning the card to a column the lifecycle expects
  next (see `docs/HUMAN_INTERFACES.md` board diagram), and
- your manifest's `tool_grants.planka.actions` includes `move`.

Never move a card across a column you do not own (e.g., from `Approved To Execute`
back to `Inbox`); leave that to the executive.
