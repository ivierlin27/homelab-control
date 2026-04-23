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
