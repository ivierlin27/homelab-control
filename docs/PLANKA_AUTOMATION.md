# Planka Automation

Planka is the control plane. Cards are the durable work items that tie together:

- plan/checklist
- repo / environment
- PR or MR
- review decision
- human escalation

## Required card metadata

- domain
- repo
- risk markers
- link to plan
- link to PR/MR once opened

## Trigger model

- `Plan Ready` -> author agent expands plan
- `Approved To Execute` -> author agent works on branch
- `Author Review Ready` -> review agent runs
- `Needs Human Review` -> notify Kevin and wait
- `Merged / Applied` -> close loop and summarize into memory

## Queue dispatch

The repo now includes a small bridge from card exports to queue jobs:

```bash
python3 scripts/planka_dispatch.py \
  --card /path/to/card.json \
  --author-queue ~/.local/state/homelab-control/agent-homelab \
  --review-queue ~/.local/state/homelab-control/agent-review \
  --artifact-dir ~/.local/state/homelab-control/planka-artifacts
```

Expected mappings:

- `Plan Ready` -> enqueue `create-execution-job`
- `Approved To Execute` or `In Progress` -> enqueue `execute-task`
- `Author Review Ready` or `Review Agent` -> enqueue `review-pr`

The card JSON should carry enough metadata to preserve linkage between:

- card ID
- plan link
- Planka URL
- branch name
- PR URL
- review context path

The generated queue job file name should include the Planka card ID so later
receipts and PR artifacts remain traceable.

## Live webhook dispatcher

The Alienware agent host also runs an HTTP dispatcher for n8n:

- `POST http://<alienware>:8765/planka-control-plane`
- `POST http://<alienware>:8765/planka/card-moved`
- `POST http://<alienware>:8765/forgejo/pull-request`

Requests should include:

```http
X-Agent-Dispatch-Token: <shared secret>
```

For execution, a Planka card moved to `Approved To Execute` should include a
fenced JSON block in its description:

````markdown
```agent-execution
{
  "allowed_paths": ["docs"],
  "checks": ["git diff --check"],
  "operations": {
    "write_files": [
      {
        "path": "docs/example.md",
        "content": "hello\n"
      }
    ]
  },
  "review_queue_dir": "/home/kenns/.local/state/homelab-control/agent-review"
}
```
````

When Forgejo reports a merged PR, the dispatcher moves the related Planka card:

- default: `Done`
- if the PR body contains `Next Planka list: Approved To Execute`: `Approved To Execute`

This supports both flows:

- normal implementation PR merged -> card is done
- plan/scaffold PR merged -> card can move back to `Approved To Execute` for the
  next execution step

## Live smoke test

A real Planka card can be moved to `Approved To Execute` to enqueue an author-agent job through n8n and the Alienware dispatcher. The matching Forgejo merge webhook moves the linked card to `Done`.

## Lifecycle lane smoke test

A real Planka card now moves from `Approved To Execute` to `Author Review Ready` when the author agent opens a PR, then to `Merged / Applied` after review approval, and finally to `Done` when Forgejo reports the merged PR.
