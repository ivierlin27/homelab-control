# Strong Model Strategy

This document captures the practical strong-route envelope for the Alienware
host and the decision rules for future model upgrades.

## What the strong route is for

The `homelab-strong` route should handle the tasks where the fast route is most
likely to underperform:

- multi-file code and config edits
- PR review and remediation planning
- incident investigation that mixes logs, runbooks, and config context
- architectural proposals and agent task planning

It is not the right place to dump an entire repository or giant raw log bundles.
For those cases, use retrieval, search, and summarization first.

## Context target

For this workload, the practical tiers are:

- `8K`: single-file edits, focused shell troubleshooting, short PR reviews
- `16K`: normal incidents, 2-4 related files, one runbook plus selected logs
- `32K`: the default target for the strong route; enough room for instructions,
  tool output, several files, and the active problem statement without heavy
  trimming
- `>32K`: only when the task truly requires it; prefer retrieval before forcing
  a longer monolithic context on a single 24 GB GPU

The Qwen model cards consistently treat `32768` as the native operating window
and recommend enabling YaRN only when longer context is actually required,
because static YaRN can hurt shorter-prompt quality.

## Current 3090 envelope

Validated on the Alienware RTX 3090 with `vllm` `v0.19.1`:

- `homelab-fast`: `Qwen/Qwen2.5-7B-Instruct` at `32768`
- `homelab-strong`: `Qwen/Qwen2.5-14B-Instruct-AWQ` at `32768`

The strong profile is currently the reliable top end of the stack:

- AWQ 4-bit weights
- FP8 KV cache
- prefix caching enabled
- chunked prefill enabled
- bounded scheduler settings

Working assumption: a dense `14B` AWQ model is the reliable `32K` ceiling for a
single 24 GB 3090 today. A dense `32B` AWQ model is still an experiment on this
hardware and should be treated as a shorter-context or offloaded profile, not as
the default `32K` strong route.

## Candidate order

1. `Qwen/Qwen2.5-14B-Instruct-AWQ`
   Keep as the stable fallback because it is already live through the gateway.

2. `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
   Best next candidate if the strong route is mostly used for repo changes, PR
   review, and code remediation. Qwen positions this line specifically for code
   generation, code fixing, and code-agent use.

3. `Qwen/Qwen3-14B-AWQ`
   Best next candidate if agentic planning and tool use matter more than raw
   coding specialization. Qwen3 adds native thinking/non-thinking mode switching
   and stronger tool/agent claims while keeping a native `32768` context.

4. `Qwen/QwQ-32B-AWQ` or a `DeepSeek-R1-Distill-Qwen-32B-AWQ`
   Treat as stretch experiments for reasoning-heavy tasks. Do not start at `32K`
   on this GPU. Try `8K`, `12K`, or `16K` first and only then consider offload.

5. `google/gemma-3-27b-it`
   Keep as a future candidate, not the next one. The model family is attractive,
   but a `27B` multimodal class is a more awkward fit for the current single-GPU
   envelope than the Qwen `14B` lane.

## Optimization order

Apply optimizations in this order:

1. Pin the runtime version and use `--ipc=host`.
2. Prefer AWQ weights before trying larger unquantized models.
3. Stay at the model's native `32768` context before reaching for YaRN.
4. Enable prefix caching and chunked prefill.
5. Bound `max-num-batched-tokens` and `max-num-seqs`.
6. Use FP8 KV cache only where it is validated.
7. Consider `--kv-offloading-size` or `--cpu-offload-gb` only after the steps
   above are exhausted.

For the current PCIe desktop setup, offload is a fallback lever, not a default
optimization. It can expand fit, but it does so by trading away latency and
predictability.

## Promotion rules

A candidate may replace the current strong default only if it:

- starts cleanly on a single RTX 3090 at the intended context size
- answers through both the local endpoint and the public model gateway
- beats or clearly matches the current baseline on homelab tasks
- keeps latency and stability inside an acceptable range
- lands through a PR and human approval

## Source notes

- Qwen2.5 and Qwen2.5-Coder model cards treat `32768` as the native deployment
  window and recommend YaRN only for genuinely longer contexts.
- Qwen3-14B-AWQ advertises native `32768`, optional YaRN to `131072`, and
  explicit thinking/non-thinking plus agent/tool support.
- `vllm` `v0.19.1` exposes explicit CPU and KV offload controls; the stack now
  prefers those over the older coarse swap-style tuning.
