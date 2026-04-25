# Human Interfaces Guide

This system has a lot of moving parts behind the scenes, but most people only
need to know three human-facing places:

1. the task board
2. the review page
3. the password vault

Think of it like this:

- the board is where you ask for something and track it
- the review page is where you approve or reject important changes
- the password vault is where you store and share human passwords safely

## 1. Task board

This is the main everyday interface.

Use it for:

- asking the system to do something
- tracking what is in progress
- seeing whether something is waiting on a person
- seeing what was finished

Examples:

- "Add a photo backup system"
- "Fix the movie server buffering problem"
- "Research a good family calendar setup"
- "Set up a shared shopping list"

### What using the board feels like

You create or open a card, add what you want, and move it forward when you want
the system to do the next step.

Simple example:

1. Create a card called `Fix Plex buffering`.
2. Add a short note like: `Videos pause at night. Please investigate.`
3. Move it to `Plan Ready` when you want the agent to draft a plan.
4. The card moves to `Needs Human Review` when it needs a person to decide.
5. If the plan looks right, move the card to `Approved To Execute`.
6. The agent does the work and opens a review page.
7. The card moves back to `Needs Human Review` if you need to approve the change.
8. When approved and merged, the card ends up in `Done`.

Labels explain what is happening. Moving cards starts work; changing labels does
not start work.

Common labels:

- `review:plan`: review the plan before work starts
- `review:pr`: review the change before it is merged
- `state:pr-open`: a review page exists
- `state:review-agent`: the review agent is checking it
- `state:ready-to-merge`: the review agent thinks it is ready
- `type:docs`, `type:deployment`, `type:research`: what kind of task this is

### Board diagram

```mermaid
flowchart LR
    A[Inbox: new request] -->|Move when ready| B[Plan Ready]
    B --> C[Agent drafts plan]
    C --> D[Needs Human Review + review:plan]
    D -->|Approve plan| E[Approved To Execute]
    D -->|Needs changes| B
    E --> F[In Progress + state labels]
    F --> G[Agent opens review page]
    G --> H[Needs Human Review + review:pr]
    H -->|Approve / merge| I[Done]
    H -->|Request changes| E
    A -->|Already has execution details| E
```

The only moves that start work are:

- move to `Plan Ready` to ask for a plan
- move to `Approved To Execute` to ask the agent to do approved work

## 2. Review page

This is the page in Forgejo where you approve important changes before they go
live.

You do not need to use it for every little thing. It is mainly for:

- changes that could break something
- changes that affect shared family systems
- anything new, sensitive, or unclear

What you will usually see:

- a short title
- a summary of what changed
- why the change was made
- whether the review agent thinks it is safe
- buttons to approve, request changes, or leave comments

Examples:

- approving a new backup schedule
- approving a networking change
- rejecting a change because the plan sounds too disruptive

### Example review

The review page might say:

- `Change: Add nightly backup for family photos`
- `Why: protect photo library from disk failure`
- `Risk: low`
- `Review agent says: safe to merge`

A less technical reviewer can usually just answer:

- "Yes, this is what we wanted"
- "No, this is not what I meant"
- "Wait, explain this first"

### Review diagram

```mermaid
sequenceDiagram
    participant Human as You
    participant Board as Task Board
    participant Author as Author Agent
    participant Review as Review Agent
    participant PR as Review Page

    Human->>Board: Move card to Approved To Execute
    Board->>Author: Do the work
    Author->>PR: Open change for review
    Author->>Board: Move card to In Progress
    Review->>PR: Check risk and summarize
    Review->>Board: Move card to Needs Human Review if needed
    PR->>Human: Ask for approval
    Human->>PR: Approve or request changes
    PR->>Board: Merge moves card to Done
```

## 3. Password vault

This is where human passwords live.

Use it for:

- personal passwords
- shared family logins
- recovery codes
- important notes like break-glass steps

Do not use it for machine-only tokens or service secrets. Those go into the
machine secret system in the background.

Examples:

- home Wi-Fi password
- streaming service login
- recovery code for a shared account
- notes for how to recover access to something important

### Password vault diagram

```mermaid
flowchart TD
    A[You save a password] --> B[Password vault stores it safely]
    B --> C[You or family members open it later]
    C --> D[Humans use the password]
    D --> E[Machines do not see personal passwords unless explicitly shared]
```

## A simple household example

Here is what it might look like to use the system without needing to know the
technical details.

### Example: "Set up better backups for family photos"

1. Create a card on the task board.
2. Move it to `Plan Ready`.
3. The system writes a simple plan.
4. You review the plan in `Needs Human Review`.
5. You move it to `Approved To Execute`.
6. The agent prepares the change.
7. If it is important, you get a review page with a summary.
8. You approve it.
9. The task is marked `Done`.
10. If the new setup needs a shared password, that password is stored in the
   password vault.

```mermaid
flowchart TD
    A[Create photo backup request] --> B[Move to Plan Ready]
    B --> C[System writes plan]
    C --> D[Needs Human Review]
    D --> E[You approve plan]
    E --> F[Move to Approved To Execute]
    F --> G[Agent prepares change]
    G --> H[Needs Human Review if approval is needed]
    H --> I[You approve review page]
    I --> J[Task marked Done]
    J --> K[Shared passwords saved in vault if needed]
```

## What most people should remember

- Start on the task board.
- Use the review page when the system asks for approval.
- Use the password vault for human passwords.

Everything else is mostly there so the system can do safe work in the
background.
