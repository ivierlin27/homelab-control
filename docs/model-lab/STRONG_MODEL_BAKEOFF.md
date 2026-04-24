# Strong Model Bakeoff

This document defines the first repeatable strong-model bakeoff for the
Alienware RTX 3090.

## Goal

Choose the next best `homelab-strong` candidate by comparing it against the
current stable baseline on realistic homelab work instead of generic benchmark
claims.

## Round 1

Baseline:

- `Qwen/Qwen2.5-14B-Instruct-AWQ`

Candidates:

- `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
- `Qwen/Qwen3-14B-AWQ`

Context tier for the first round:

- `32768`

The first round is intentionally conservative:

- same hardware
- same runtime family
- same context tier
- only one strong-model candidate loaded at a time

## What to compare

Each model should be judged on four dimensions:

1. quality of the proposed solution
2. ability to follow repo constraints and existing patterns
3. startup and serving stability on the 3090
4. latency and operator friction

## Benchmark tasks

The first bakeoff should use these task types:

1. service failure triage
2. PR review and risk assessment
3. small architecture or planning task
4. inventory-to-memory sync project

The fourth task is the anchor task for this round because it matches the actual
next project you want in the system.

## Inventory-to-memory task

Use `docs/model-lab/tasks/inventory-memory-sync.md` as the shared task packet.

The model should be given:

- the task packet
- `inventory/services.yaml`
- `inventory/observability.yaml`
- `config/memory/principals.yaml`
- `apps/homelab_operator/main.py`

The output should be compared for:

- correct understanding of the repo and data model
- realistic implementation scope
- safe plan for writing to the memory system
- useful acceptance criteria
- whether the plan is strong enough to hand to an author agent

## Scorecard

Use a simple `1` to `5` scale for each area:

- repo understanding
- implementation quality
- risk awareness
- memory-model design
- operator usefulness
- output clarity

Also record:

- cold-start success
- local endpoint success
- gateway success
- rough first-token latency
- rough completion latency

## Win conditions

A candidate wins the round only if:

- it starts cleanly on the target profile
- it clears all endpoint checks
- it beats or clearly matches the baseline on the anchor task
- it does not materially worsen operational complexity

If two candidates are close, prefer:

- the one with the cleaner operational profile
- the one that needs fewer special-case prompt rules
- the one that fits the repo's actual workload better

## Output artifact

Each bakeoff round should produce:

- a dated note under `docs/model-lab/`
- a clear recommendation: keep baseline, promote candidate, or run another round
