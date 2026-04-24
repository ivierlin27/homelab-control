# Model Optimizer Agent

This agent exists to keep the local model stack current without turning model
selection into a recurring manual research project.

## Mission

On a regular cadence, the model optimizer agent should:

- track new model releases, quantizations, and runtime changes
- compare them against the actual constraints of the Alienware RTX 3090
- run a small number of controlled experiments
- update the knowledge base with what changed and what was learned
- propose architecture or routing changes for human approval

## Cadence

Run weekly by default, plus manually when:

- a major `vllm` release lands
- a high-signal model family ships a new `14B` or `32B` candidate
- a current route becomes unstable or materially slower
- the underlying hardware changes

## Inputs

Each run should gather:

- upstream runtime changes: `vllm`, `transformers`, CUDA/container guidance
- model-card updates and high-signal Hugging Face discussions
- current repo strategy in `docs/STRONG_MODEL_STRATEGY.md`
- prior experiment notes under `docs/model-lab/`
- live service constraints: GPU VRAM, route layout, one-model mode, latency
- local benchmark results from the current fast and strong baselines

## Outputs

Each run should produce:

1. A dated lab note under `docs/model-lab/`
2. Any necessary update to `docs/STRONG_MODEL_STRATEGY.md`
3. A PR or draft PR if config changes are warranted
4. A short recommendation summary for Planka / human review

If no change is recommended, the agent should still write down what was checked
and why the current baseline remains the best choice.

## Workflow

1. Reconfirm the active hardware and runtime envelope.
2. Build a shortlist of at most two high-value candidates for the week.
3. Select the context tier to test: `8K`, `16K`, or `32K`.
4. Run the benchmark prompts against the stable baseline and the candidate.
5. Compare quality, latency, fit, and operational complexity.
6. Update the knowledge base and open a proposal if the candidate wins.

The goal is continuous improvement, not maximum churn.

## Benchmark set

Every run should include representative homelab tasks:

- summarize a service failure from journal output and propose remediation
- review a multi-file config diff and identify risk
- draft a PR summary and test plan
- plan a small homelab change that touches multiple files or services
- extend the homelab memory layer with new structured inventory knowledge
- answer a capacity or observability question using mixed structured inputs

Run the same prompts across all candidates so the comparisons stay meaningful.

## Guardrails

- Never merge directly to `main`.
- Never replace the stable route without a validated fallback.
- Do not test more than two new candidates in one weekly cycle.
- Do not promote a model based on benchmark scores alone; include startup
  stability, gateway behavior, and operator friction.
- Prefer retrieval and better prompt packing before asking for longer context.
- Treat CPU or KV offload as an explicit latency trade-off, not a free win.

## Knowledge base updates

The agent should write results in two places:

- Git-backed markdown in this repo for reproducibility and review
- the shared memory system under a distinct principal such as
  `agent:model-optimizer`

The memory entry should capture:

- model id
- runtime version
- key flags
- context tier tested
- result summary
- recommendation status

## Proposal threshold

Open a change proposal only when at least one of the following is true:

- quality materially improves at the same context tier
- the same quality fits at a longer context tier
- latency or stability improves without quality loss
- a runtime upgrade removes a current blocker
- the candidate simplifies the overall routing architecture
