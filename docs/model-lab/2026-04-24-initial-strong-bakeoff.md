# 2026-04-24 Initial Strong Bakeoff

This note captures the first real strong-model bakeoff run after the `vllm`
`v0.19.1` migration.

## Task

Anchor task:

- `docs/model-lab/tasks/inventory-memory-sync.md`

Question:

- which `14B`-class strong model gives the best starting plan for syncing
  homelab inventory into the shared memory system?

## Models tested

1. baseline: `Qwen/Qwen2.5-14B-Instruct-AWQ`
2. challenger: `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
3. challenger: `Qwen/Qwen3-14B-AWQ`

All were tested on the same RTX 3090, same `vllm` image, and same `32768`
context target.

## Operational findings

### Qwen2.5-Coder-14B-Instruct-AWQ

- started successfully
- answered through the public strong route
- required no special runtime flags

### Qwen3-14B-AWQ

- the first attempt failed because the current `vllm` CLI on this image rejected
  `--enable-reasoning`
- the plain model startup succeeded after removing that flag
- weights downloaded and the service eventually reached a healthy API state
- therefore the model itself is viable, but the serving guidance for this stack
  is not yet clean

## Quality findings

### Baseline: Qwen2.5-14B-Instruct-AWQ

Strengths:

- stayed mostly on task
- produced a clear five-part structure

Weaknesses:

- jumped too quickly to a new REST API
- proposed a cron-style refresh rather than integrating cleanly with the
  existing operator pattern
- was not grounded enough in the current repo shape

### Challenger: Qwen2.5-Coder-14B-Instruct-AWQ

Strengths:

- also stayed on task
- recognized the need for a new operator command

Weaknesses:

- still leaned on generic sync-script thinking
- still proposed periodic refresh mechanics without tying them tightly to the
  repo's existing workflow
- did not materially improve on the baseline's grounding or architectural
  discipline

Verdict:

- not enough better than the baseline to justify promotion

### Challenger: Qwen3-14B-AWQ

Strengths:

- viable on the hardware once served without the rejected reasoning flag
- more detailed architecture answer than the other two
- recognized provenance, ownership, and idempotency concerns

Weaknesses:

- leaked reasoning text with a `<think>` block in the response on this stack
- still invented details that were not grounded in the repo, such as queue-based
  conflict handling and synthetic timestamps
- still treated refresh more abstractly than desired instead of anchoring it in
  the current operator and inventory files

Verdict:

- the most interesting challenger so far, but not clean enough to replace the
  baseline yet

## Recommendation

Keep `Qwen/Qwen2.5-14B-Instruct-AWQ` as the stable `homelab-strong` default for
now.

Do not promote either challenger from this first round.

## Next bakeoff step

The next useful round should focus on better grounding rather than wider model
sampling:

1. tighten the prompt packet with exact file excerpts and explicit anti-goals
2. rerun `Qwen/Qwen3-14B-AWQ` with prompt controls aimed at suppressing visible
   reasoning output on this serving path
3. compare the baseline and Qwen3 again on the same inventory-memory task
4. if Qwen3 still leaks reasoning or invents too much repo structure, keep it in
   the research bucket and move on to the real implementation task
