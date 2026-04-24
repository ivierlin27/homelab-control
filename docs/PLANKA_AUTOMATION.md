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
