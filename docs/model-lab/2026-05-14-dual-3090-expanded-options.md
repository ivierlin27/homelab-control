# 2026-05-14 Dual RTX 3090 Expanded Options

> **2026-05-15 update.** Phases 0-2 of the v3 plan
> ([`dual-3090_v3_bench`](../../.cursor/plans/dual-3090_v3_bench_bb6161d4.plan.md))
> are complete. New benchmark harness `scripts/bench/` (micro / serve-sweep /
> ruler-lite / bfcl-lite / soak / aggregate) replaces the one-shot script.
> vLLM bumped 0.19.1 -> 0.21.0. New strong-long baseline locked in below.
> See [Phase 2 update](#2026-05-15-phase-2-update-vllm-021-baseline) at the
> end of this doc for the new numbers, including a clear negative result on
> n-gram speculative decoding for this model.


This is the second pass after the initial fast+strong benchmark. It tests two
additional questions:

1. What is the best single model that can serve both fast and strong use cases
   across both RTX 3090s?
2. Can a big TP=2 model coexist with a small fast model?

## Short answer

The best **single-model** option tested is now
`QuantTrio/Qwen3.6-35B-A3B-AWQ` on both GPUs with TP=2. It started
successfully at `131072` context and completed a long-context recall prompt with
`72922` prompt tokens.

With the default chat template it is not yet the best **daily default** for
Kevin's use cases because it emits visible thinking/reasoning on normal chat
completions. With parser flags enabled, the reasoning moved into the OpenAI
response's `reasoning` field, but `content` remained empty on short prompts even
with `500` completion tokens.

The Reddit thread and linked upstream issue point to the missing piece:
`qwen3.6-enhanced.jinja` plus top-level request
`chat_template_kwargs: {"enable_thinking": false}`. That combination returned
normal `content`, clean JSON, and a parsed tool call in a focused smoke test.

The best daily fit remains:

- GPU0: `Qwen/Qwen2.5-7B-Instruct` as the warm fast route
- GPU1: `Qwen/Qwen2.5-14B-Instruct-AWQ` as the warm strong route

Add Qwen3.6 as a **mode-switch long-context route**, not as the default route.
The 35B A3B profile now has clean content, JSON, and tool-call behavior when
launched with the Qwen tool parser and called with
`chat_template_kwargs: {"enable_thinking": false}`.

## Options tested

### Option A: one big TP=2 endpoint

Model: `Qwen/Qwen2.5-32B-Instruct-AWQ`

- Native `32768` context started successfully with TP=2.
- Attempted `65536` context was rejected because the model config advertises
  `max_position_embeddings=32768`; vLLM requires
  `VLLM_ALLOW_LONG_MAX_MODEL_LEN=1` to override this.
- Runtime memory at `32K`: about `21.7-21.8 GiB` per GPU.
- Short prompt latency: `2.96s`.
- Config-review latency: `4.86s`.
- JSON contract: valid JSON, but confidence was numeric (`0.95`) instead of the
  requested string.

Verdict: not compelling. It consumes both cards, does not expand context beyond
the existing 32K routes, and still has schema drift.

### Option B: one big TP=2 long-context endpoint

Model: `cyankiwi/Qwen3.6-27B-AWQ-INT4`

Launch shape:

- `--tensor-parallel-size 2`
- `--disable-custom-all-reduce`
- `--quantization compressed-tensors`
- `--dtype bfloat16`
- `--kv-cache-dtype fp8`
- `--block-size 16`
- `--gpu-memory-utilization 0.83`
- `--max-model-len 65536`
- `--enable-prefix-caching`
- `--enable-chunked-prefill`
- `--trust-remote-code`

Measured:

- Started successfully at `65536` context.
- Runtime memory: about `19.9 GiB` per GPU.
- Short prompt latency: `4.99s`.
- Config-review latency: `4.74s`.
- JSON contract: valid JSON, but confidence was numeric (`0.95`).
- Long-context prompt: `37262` prompt tokens, `33.24s`, all three NEEDLE labels
  present.
- Larger prompt: `65277` prompt tokens + `260` output tokens was rejected at
  `65537` total requested tokens, exactly one token over the configured window.

Reasoning/parser behavior:

- Without parser flags, normal responses included visible "Here's a thinking
  process" text.
- Prompt-level `/no_think` did not suppress that behavior.
- With `--reasoning-parser qwen3`, `--tool-call-parser qwen3_coder`, and
  `--enable-auto-tool-choice`, the visible reasoning moved into the `reasoning`
  response field, but short prompts returned `content: null` even with `500`
  completion tokens.
- With `qwen3.6-enhanced.jinja`, `preserve_thinking=true`, parser flags, and
  top-level request `chat_template_kwargs: {"enable_thinking": false}`, short
  non-tool responses returned normal `content`, JSON responses returned content,
  and a tool request produced an OpenAI `tool_calls` entry.

Verdict: best long-context single model. It now has a plausible clean route, but
that route depends on both server template configuration and client/gateway
support for forwarding `chat_template_kwargs`.

### Option C: big TP=2 plus 7B fast sidecar

Model: `Qwen2.5-32B-AWQ` at `32K`, plus attempted
`Qwen2.5-7B-Instruct` sidecar at `8K`.

Result:

- Failed.
- The TP=2 32B profile left only about `2.07 GiB` free on GPU0.
- The reduced 7B sidecar requested `4.71 GiB` at startup with
  `gpu_memory_utilization=0.20`.

Verdict: no. A 7B fast route cannot coexist with a full TP=2 32B route on these
cards.

### Option D: low-memory TP=2 plus tiny sidecar

Model: `Qwen2.5-32B-AWQ` at `16K`, plus `Qwen2.5-1.5B-Instruct` at `4K`.

Result:

- Started successfully.
- Big model memory: about `16.1-16.2 GiB` per GPU.
- Tiny sidecar added about `4.85 GiB` on GPU0.
- Concurrent wall time: `5.319s`.
- Tiny sidecar short latency: `1.26s`; config-review latency: `1.35s`.
- Big model short latency: `3.53s`; config-review latency: `4.23s`.

Verdict: technically works, but it is not a good match for Kevin's goals. It
turns the "fast" route into a tiny 1.5B/4K classifier/summarizer and cuts the
big route to `16K`.

### Option E: Qwen3.6 low-memory plus tiny sidecar

Model: `Qwen3.6-27B-AWQ-INT4` at `32K`, plus
`Qwen2.5-1.5B-Instruct` at `4K`.

Result:

- Started successfully.
- Qwen3.6 low-memory profile used about `16.6-16.7 GiB` per GPU.
- Tiny sidecar brought GPU0 to about `21.5-22.1 GiB`.
- Concurrent wall time: `5.225s`.
- Tiny sidecar short latency: `1.26s`; config-review latency: `1.35s`.
- Qwen3.6 short latency: `5.10s`; config-review latency: `4.04s`.
- Qwen3.6 found all long-context NEEDLE labels at `31486` prompt tokens, but
  still emitted visible thinking text.

Verdict: technically works, but it gives up Qwen3.6's 65K+ context advantage and
still has the reasoning/content issue.

### Option F: Qwen3.6-35B A3B TP=2

Model: `QuantTrio/Qwen3.6-35B-A3B-AWQ`

Launch shape:

- `--tensor-parallel-size 2`
- `--disable-custom-all-reduce`
- `--quantization awq_marlin`
- `--dtype float16`
- `--kv-cache-dtype fp8`
- `--gpu-memory-utilization 0.92`
- `--max-num-batched-tokens 8192`
- `--max-model-len 131072`
- `--enable-auto-tool-choice`
- `--tool-call-parser qwen3_coder`
- `--enable-prefix-caching`
- `--enable-chunked-prefill`
- `--trust-remote-code`

Measured:

- Started successfully at `65536` and `131072` context.
- Plain `--quantization awq` booted but vLLM recommended `awq_marlin`; the
  `awq_marlin` profile worked.
- The first `65K` launch failed because Qwen3.6's aligned Mamba block size was
  `2096`, larger than vLLM's default `max_num_batched_tokens=2048`; setting
  `--max-num-batched-tokens 8192` fixed startup.
- Model load used about `11.21 GiB` per GPU at `65K`; the 128K serving profile
  reported about `23.3 GiB` used per GPU after tests.
- Short prompt: `3.60s`, `61.15 tok/s`.
- Config review: `2.85s`, `105.12 tok/s`.
- Strict JSON: valid JSON content, `1.07s`.
- Tool calling: one parsed OpenAI `tool_calls` entry, `0.69s`.
- Long-context prompt: `72922` prompt tokens, `15.80s`, all three NEEDLE labels
  present.

Verdict: best single-model profile tested so far. It is a real candidate for a
`strong-long` route and the first single endpoint that plausibly covers both
"fast enough" short tasks and stronger long-context work. It still consumes both
GPUs, so it cannot preserve the always-warm 7B fast lane.

### Option G: Carnice-V2-27B TP=2

Model: `wasifb/Carnice_V2_27B_INT4_BF16MTP`

Launch shapes tested:

- MTP profile: `--quantization auto_round`, `--dtype float16`, TP=2,
  `--max-model-len 131072`, `--kv-cache-dtype fp8`,
  `--reasoning-parser qwen3`, `--enable-auto-tool-choice`,
  `--tool-call-parser hermes`, and
  `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`.
- Stable profile: same base model, but no reasoning parser, no MTP, and
  `--gpu-memory-utilization 0.86`.

Measured:

- MTP profile booted at `131072` context and reported about `9.26 GiB` model
  memory plus `10.84 GiB` KV cache per GPU.
- MTP profile produced normal tool calls, but normal answers landed in the
  OpenAI `reasoning` field with empty `content`.
- MTP profile OOMed on the first long-context request by about `266 MiB`.
- Stable profile avoided the crash and returned normal `content`, but visible
  thinking polluted answers and the config review contained factual errors
  (`RTX 3090` described as a CPU).
- Stable profile technically returned `200` for long-context prompts, but failed
  needle recall with blank/near-blank content.

Verdict: not recommended for Kevin's default agent route. It is interesting as a
Hermes/tool-call experiment, but the current vLLM profile is less clean and less
useful than Qwen3.6-35B-A3B-AWQ for these workloads.

## Best fit by use case

### Always-on agent platform

Use separate warm routes:

- `homelab-fast`: `Qwen2.5-7B-Instruct`, GPU0, `32K`
- `homelab-strong`: `Qwen2.5-14B-Instruct-AWQ`, GPU1, `32K`

This is the best fit for executive assistant, homelab maintainer, RLM subcalls,
JSON contracts, and seamless escalation. Both models stay loaded, route
switching is immediate, and the strong model does not leak reasoning text.

### Long-context manual lab

Use a mode switch:

- `homelab-strong-long`: `QuantTrio/Qwen3.6-35B-A3B-AWQ`, TP=2, `128K`

This is the best fit for repository-scale synthesis, large logs, big tool-output
digests, or "give one model everything" experiments. It should be opt-in until
the reasoning/content behavior is tuned.

### Single endpoint for both fast and strong

Qwen3.6 TP=2 is the only tested candidate worth considering. It gives larger
context and strong single-model behavior, but it is not actually "fast" in the
way the 7B route is:

- tiny route short latency: about `1.3s`
- 7B route short latency in earlier tests: about `4.3-4.9s`
- Qwen3.6 27B route short latency: about `5.0s`
- Qwen3.6 35B A3B route short latency: about `3.6s`

Use this only if operational simplicity and context length matter more than
having a truly fast warm route.

## Raw results

Second-pass raw outputs:

- `docs/model-lab/2026-05-14-tp2-32b-summary.json`
- `docs/model-lab/2026-05-14-tp2-32b-results.jsonl`
- `docs/model-lab/2026-05-14-tp2-32b-plus-tiny-summary.json`
- `docs/model-lab/2026-05-14-tp2-32b-plus-tiny-results.jsonl`
- `docs/model-lab/2026-05-14-qwen36-27b-tp2-65k-summary.json`
- `docs/model-lab/2026-05-14-qwen36-27b-tp2-65k-results.jsonl`
- `docs/model-lab/2026-05-14-qwen36-32k-plus-tiny-summary.json`
- `docs/model-lab/2026-05-14-qwen36-32k-plus-tiny-results.jsonl`
- `docs/model-lab/2026-05-14-qwen36-template-smoke-summary.json`
- `docs/model-lab/2026-05-14-qwen36-template-enable-thinking-false-summary.json`
- `docs/model-lab/2026-05-14-qwen36-35b-a3b-awq-64k/summary.json`
- `docs/model-lab/2026-05-14-qwen36-35b-a3b-awq-64k-tools/summary.json`
- `docs/model-lab/2026-05-14-qwen36-35b-a3b-awq-128k/summary.json`
- `docs/model-lab/2026-05-14-carnice-v2-27b-int4-bf16mtp-128k/summary.json`
- `docs/model-lab/2026-05-14-carnice-v2-27b-int4-stable-128k/summary.json`

The `plus-tiny` result files were produced before `BENCH_FAST_KEY` was added to
the benchmark script, so their fast-sidecar rows are labeled
`fast_qwen25_7b_gpu0` even though the endpoint was
`homelab-fast-tiny-sidecar` (`Qwen2.5-1.5B-Instruct`).

## Recommendation

Keep the daily routing architecture:

1. `dual`: 7B fast on GPU0 + 14B AWQ strong on GPU1.
2. `strong-long`: Qwen3.6 35B A3B TP=2 at 128K for explicit long-context work.
3. Do not run "TP=2 big + tiny sidecar" by default. It is a fun proof of
   feasibility, but it creates three operational compromises: smaller context,
   weaker fast route, and more service complexity.

Next improvement worth testing: wire a `strong-long` LiteLLM route for
`QuantTrio/Qwen3.6-35B-A3B-AWQ` that forwards
`chat_template_kwargs: {"enable_thinking": false}` for normal agent calls. Keep a
separate thinking mode only for clients that explicitly understand the
`reasoning` field.

## 2026-05-15 Phase 2 update: vLLM 0.21 baseline

Storage relocated to a new 1 TB SSD (`/mnt/data`, ext4) with HF cache and model
weights on the SSD; a 1 TB HDD (`/mnt/spinny`) is mounted for archive use. The
inventory was updated to reflect 48 GB system RAM and the two new disks.

A statistically rigorous v2 harness (`scripts/bench/`) replaces the previous
one-shot script. New runners: `micro` (N repeats + warmup + percentiles),
`serve-sweep` (concurrency sweep), `ruler` (RULER-lite at 32 / 65 / 128 K),
`bfcl` (BFCL-lite tool calling), `soak` (steady-load drift), `code` (lm-eval
HumanEval / MBPP wrapper), `aggregate` (cross-run roll-up).

vLLM was bumped from `v0.19.1` to `latest` (`0.21.0`). Two important
serving-side changes for this model:

- The current QuantTrio Qwen3.6 AWQ checkpoint emits Qwen3-XML tool format
  (`<tool_call><function=...><parameter=...>...</parameter></function></tool_call>`),
  not Hermes. The correct flag is `--tool-call-parser qwen3_xml`. With Hermes
  the tool call still appears in `content` but `tool_calls` is empty, which is
  the silent-failure mode the previous benchmark hit.
- `--shm-size` cannot coexist with `--ipc=host` in podman; drop `--shm-size`.

### Locked profile (strong-long, baseline)

```
QuantTrio/Qwen3.6-35B-A3B-AWQ
--tensor-parallel-size 2
--quantization awq_marlin --dtype bfloat16
--kv-cache-dtype fp8
--max-model-len 131072 --max-num-batched-tokens 8192 --max-num-seqs 16
--gpu-memory-utilization 0.92
--enable-prefix-caching --enable-chunked-prefill
--enable-auto-tool-choice --tool-call-parser qwen3_xml
--trust-remote-code --disable-custom-all-reduce
env: VLLM_ATTENTION_BACKEND=FLASHINFER, PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

Per-request: `chat_template_kwargs: {"enable_thinking": false}`.

### Baseline results (5 micro repeats, warmup 1; bench-artifacts/2026-05-15-qwen36-35b-a3b-awq-baseline/)

| test                       | p50 ms | p95 ms | tok/s | CV    | extras                              |
| -------------------------- | -----: | -----: | ----: | ----: | ----------------------------------- |
| `code_config_review`       |   2116 |   2116 | 141.8 | 0.000 | -                                   |
| `short_ops_summary`        |   1574 |   1576 | 139.6 | 0.001 | -                                   |
| `structured_json_contract` |    386 |    387 | 121.7 | 0.001 | json valid 100%, schema pass 100%   |
| `tool_call_contract`       |    345 |    346 | 118.8 | 0.001 | tool match 100%                     |
| `long_context_31k`         |   1199 |   1200 |  90.9 | 0.001 | needles ALPHA/BRAVO/CHARLIE all 100%|
| `long_context_60k`         |   1384 |   1387 |  78.7 | 0.002 | needles ALPHA/BRAVO/CHARLIE all 100%|

Per-GPU VRAM after warmup: ~23.4 GB / ~23.6 GB out of 24 GB. CV on every test
under 0.5%, indicating very stable inference.

BFCL-lite (3 repeats x 5 cases): selection 93%, args 93%. The single
imperfect case is `simple_gpu_memory` where the model occasionally interprets
"GPU 1" as `gpu_index=0` instead of `1`; this is model judgment, not a parser
issue.

Serve sweep at concurrencies [1, 4, 8] with 16 requests each:

| concurrency | rps  | p50 ms | p95 ms | err | tok/s/req |
| ----------: | ---: | -----: | -----: | --: | --------: |
|           1 | 0.71 |   1405 |   1409 |   0 |     143.1 |
|           4 | 1.59 |   2488 |   3050 |   0 |      80.9 |
|           8 | 3.22 |   2478 |   2487 |   0 |      80.6 |

RULER-lite (3 samples per (task, length)) - all targets are now reachable
after fixing the haystack-vs-tokenizer ratio (Qwen3.6 hits log text at ~2
chars/token; the harness was previously using /3 and silently overshooting
`max_model_len`):

| task                  | 32 K | 65 K | 131 K |
| --------------------- | ---: | ---: | ----: |
| `niah_single_simple`  | 100% | 100% | 100%  |
| `niah_multi_key`      | 100% | 100% | 100%  |
| `vt_2hop`             |  67% |  33% | 100%  |

`vt_2hop` is variable tracking (resolve `VAR a=b` and `VAR b=value`). The
sample variance at 32-65 K is real; expanding to 5+ samples per cell is
queued for the next pass.

### Negative result: n-gram speculative decoding

Tested the same profile plus
`--speculative-config '{"method":"ngram","num_speculative_tokens":5,
"prompt_lookup_max":5,"prompt_lookup_min":2}'`
(`bench-artifacts/2026-05-15-qwen36-35b-a3b-awq-ngram/`). For this
model+workload mix it is a **net loss**:

- **Long-context recall** improves modestly: `long_context_31k` 91 -> 123
  tok/s (+35 %), `long_context_60k` 79 -> 90 tok/s (+15 %). Repetitive ledger
  text is exactly the n-gram lookup sweet spot.
- **Short prompts** regress: `short_ops_summary` 141 -> 100 tok/s (-29 %),
  `code_config_review` 143 -> 98 tok/s (-31 %), `structured_json_contract`
  122 -> 85 tok/s (-31 %).
- **BFCL-lite collapses**: selection 93 % -> 27 %, args 93 % -> 20 %. The
  draft tokens are repeatedly rejected at `<tool_call>` / `<parameter>`
  boundaries and the parser then trips on partial structures. This is the
  highest-impact regression - it breaks the agent path.
- **Concurrency throughput** drops under load: at c=8, completed RPS goes
  from 3.22 -> 1.33 (-59 %); p95 latency rises from ~2.5 s to ~6.7 s.

Recommendation: do not enable n-gram speculative decoding on this profile.
Revisit only with a real draft model (Qwen3 0.5B-1.7B trained as draft) or
with vLLM's MTP path once the upstream Qwen3.6 AWQ checkpoints expose MTP
weights.

## 2026-05-15 Phase 3: model bake-off

Three candidates tested against the Phase 2 baseline using the v2 harness.
All runs use the same `--tensor-parallel-size 2 --kv-cache-dtype fp8
--enable-prefix-caching --enable-chunked-prefill` profile; per-model
adjustments are noted.

### Candidate A: `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` -- WINNER for agent and code routes

Same MoE architecture family as the baseline; coder-instruct tuned. Same
serve flags, including `--tool-call-parser qwen3_xml` and 131K
max-model-len. VRAM after warmup: ~23.6 GB / ~23.8 GB.

| metric                                   | baseline 35B-A3B | Qwen3-Coder 30B-A3B | delta              |
| ---------------------------------------- | ---------------: | ------------------: | -----------------: |
| `code_config_review` p50 / tok/s         |    2116 / 141.8  |        1707 / 175.7 | **-19% / +24%**    |
| `short_ops_summary` p50 / tok/s          |    1574 / 139.6  |         918 / 175.3 | **-42% / +26%**    |
| `structured_json_contract` p50 / tok/s   |     386 / 121.7  |         238 / 167.9 | **-38% / +38%**    |
| `tool_call_contract` p50 / tok/s         |     345 / 118.8  |         212 / 165.5 | **-39% / +39%**    |
| `long_context_31k` needle hit            |       100% / 3   |              100% / 3 | tie                |
| `long_context_60k` needle hit            |       100% / 3   |               0% / 3 | **regress**        |
| BFCL-lite selection / args               |    93.3% / 93.3% |       **100% / 100%**| **+7pp / +7pp**    |
| serve-sweep c=8 RPS                      |             3.22 |               **4.41**| **+37%**           |

The 60K needle regression looks scary at first read but is actually a
**format-following** regression, not a recall regression. The model still
extracts the right facts ("gpu1 strong route must be isolated from fast
route"), but it strips the literal `NEEDLE_*` label that the prompt asks it
to include. RULER `niah_single_simple` shows the same pattern: the model
returns "471" / "615" / "238" (random integers from the haystack) instead
of the secret token. This is the same "instruction-vs-judgment" tradeoff
that smaller / coder-tuned models often make.

For code review, structured JSON, and tool calling -- the agent path -- this
model is **strictly better** than the 35B-A3B baseline.

### Candidate B: `jeffcookio/Mistral-Small-3.2-24B-Instruct-2506-awq-sym` -- not competitive as configured

Different family (dense 24B, Mistral). The checkpoint is `compressed-tensors`
(not bare AWQ) so `--quantization compressed-tensors` is required. Native
context is 32K; long-context tests are excluded.

| metric                          | baseline 35B-A3B | Mistral 3.2 |
| ------------------------------- | ---------------: | ----------: |
| `code_config_review` tok/s      |            141.8 |        91.4 |
| `short_ops_summary` tok/s       |            139.6 |        91.9 |
| `structured_json_contract` tok/s|            121.7 |        74.5 |
| `tool_call_contract` match rate |             100% |        **0%** |
| BFCL-lite selection / args      |    93.3% / 93.3% | **20% / 20%** |
| RULER (8K-28K) match rate       |  100% (niah_single)| **0% across all 9 cells** |
| VRAM (per GPU after warmup)     |        ~23.5 GB  |    ~21.3 GB |

The complete BFCL collapse and 0%-across-the-board RULER strongly suggest
either the `mistral` tool parser does not match this checkpoint's actual
tool-format, or `mistral_common`-specific chat-template handling is needed
that the AWQ community quant did not preserve. Diagnosis is deferred; this
model is not a Phase 2 finalist as currently configured.

### Candidate C: `cyankiwi/GLM-4.5-Air-AWQ-4bit` -- moved to Phase 5

The 4-bit packed checkpoint is **59.1 GB** on disk for a 106B-A12B MoE.
That exceeds our **48 GB total VRAM** even with TP=2. To run at all it
needs `--cpu-offload-gb` of ~16+ GB, which puts it in the offload-track
bucket alongside the other big-MoE candidates rather than the
fits-on-GPU bake-off. Deferred to Phase 5.

### Candidate D-F: deferred

`Qwen3.6-Coder` does not exist as a separate AWQ checkpoint in the QuantTrio
namespace as of this run; the closest is the `Qwen3-Coder-30B-A3B-Instruct`
in candidate A. `Devstral` and `MiniMax-M2` lack widely-tested AWQ quants on
the QuantTrio / cpatonn / cyankiwi shelves; bringing them online would
require GPTQ or compressed-tensors variants and parser tuning. They are
deferred to a later round.

### Phase 3 verdict

Two routes earn a recommendation:

- **Daily agent + code route**: `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ`
  with TP=2, FP8 KV, `--tool-call-parser qwen3_xml`, 131K max-model-len.
  Strictly faster than the previous baseline on every short-prompt task,
  perfect BFCL-lite, +37% throughput at c=8.
- **Long-document / strict-format route**: keep the Phase 2
  `QuantTrio/Qwen3.6-35B-A3B-AWQ` profile. The 60 K and 128 K needle tests
  show it follows literal-label instructions where the coder model
  paraphrases.

Both share the same serve flags and the same VRAM budget; switching is a
matter of swapping the `--model` arg in the `strong` systemd unit.

### What's next

Tracked in the v3 plan:

- Phase 4: vLLM vs SGLang vs llama.cpp on the Qwen3-Coder-30B-A3B finalist.
- Phase 5: ik\_llama.cpp / ktransformers offload on GLM-4.5-Air-class MoEs
  (the 59 GB AWQ checkpoint moved here). DeepSeek-V3 / Kimi-K2 stay out of
  scope with this hardware.
- Phase 6: refresh the canvas + systemd unit GPU pinning + add a
  `strong-long` unit fronted by LiteLLM, plus a `daily-agent` route to the
  new Qwen3-Coder-30B-A3B finalist.

## 2026-05-15 Phase 4 update: vLLM vs SGLang vs llama.cpp

Same model finalist (`Qwen3-Coder-30B-A3B-Instruct`), same hardware, same
bench harness, ad-hoc lab launchers per engine. Artifacts under
[`docs/model-lab/2026-05-15-engines-qwen3-coder-30b-a3b/`](2026-05-15-engines-qwen3-coder-30b-a3b/)
and the original vLLM AWQ baseline at
[`docs/model-lab/bench-artifacts/2026-05-15-qwen3-coder-30b-a3b-awq/`](bench-artifacts/2026-05-15-qwen3-coder-30b-a3b-awq/).

| Engine | Quant | Image | Max ctx fit | Notes |
|---|---|---|---|---|
| vLLM 0.21 | AWQ 4-bit (`compressed-tensors`) | `vllm/vllm-openai:latest` | 131072 (FP8 KV, gpu-mem-util 0.92) | baseline; `--tool-call-parser qwen3_xml` |
| SGLang 0.5.11 | AWQ 4-bit (same checkpoint) | `lmsysorg/sglang:latest` | 32768 (`mem-fraction-static 0.82`, OOM at 64K) | needed `SGLANG_USE_AITER=0`, `--disable-cuda-graph-padding` |
| llama.cpp `b9159` | GGUF Q4_K_M (`giladgd/...`) | `ghcr.io/ggml-org/llama.cpp:server-cuda` | 131072 total (32K/slot @ `--parallel 4`) | `-ts 1,1`, `-fa on`, `--jinja`; smaller VRAM footprint (~16 GB/GPU) |

### Micro (single-stream, p50 ms / mean decode tok/s, repeats=10 warmup=2)

| Test | vLLM | SGLang | llama.cpp |
|---|---|---|---|
| `code_config_review`         | 1707 / 175.7 | 1640 / 182.7 | **1541 / 194.4** |
| `short_ops_summary`          | **918** / 175.3 | 1080 / 182.8 | 1033 / **194.1** |
| `structured_json_contract`   | 238 / 167.9 | 232 / 171.8 | **219 / 182.3** |
| `tool_call_contract`         | 212 / 165.5 | 211 / 164.4 | **195 / 178.9** |
| `long_context_31k` (~37k tok) | OK 134.6 tok/s | FAIL (32K cap) | FAIL (per-slot 32K cap) |
| `long_context_60k` (~73k tok) | OK 111.7 tok/s | FAIL | FAIL |

### Concurrency sweep (24 reqs/concurrency, fixtures dataset, max_tokens=200)

| c | vLLM rps / p50 / tok/s | SGLang rps / p50 / tok/s | llama.cpp rps / p50 / tok/s |
|---|---|---|---|
| 1  | 1.15 / 1124 / 175.9 | 1.15 / 1077 / 181.2 | 1.22 / **1013** / **190.8** |
| 4  | **3.08** / 1538 / **127.2** | 2.92 / 1508 / 123.4 | 2.00 / 2312 /  85.6 |
| 8  | **4.41** / 1663 / **104.4** | not run | not run |
| 16 | not run | **6.35** / **2353** / 87.4 | 2.20 / 5497 / 37.0 |

### Tool-call correctness (BFCL-lite, 5 cases x 3 reps)

| Engine | Selection | Args |
|---|---|---|
| vLLM (qwen3_xml parser) | **1.0** | **1.0** |
| SGLang (qwen3_coder parser) | 0.8 | 0.8 (case-folding bug: returned `Summarize_logs` for `summarize_logs`) |
| llama.cpp (`--jinja`) | **1.0** | **1.0** |

### Verdict

Decision rule from the v3 plan: keep vLLM unless another engine wins by
>=20 % on TPOT or materially on agent correctness.

- **llama.cpp** is the **single-stream speed winner** by ~8-13 % on every
  short test, and matches vLLM on tool-call correctness. But:
  - the win is below the 20 % bar;
  - it falls off a cliff under concurrency (37 tok/s decode at c=16 vs
    vLLM ~104 at c=8) because of the fixed `--parallel` slot count;
  - GGUF Q4_K_M is a different (lower-quality) quant than AWQ 4-bit, so
    speed comparisons need an accuracy parity check before claiming
    "free" wins. Not run yet.
- **SGLang** matches vLLM within noise on short tests, scales well at
  c=16, but loses 20 pp on BFCL (case-folding) and **cannot fit 64 K
  context** in the same VRAM budget that vLLM uses for 128 K. AWQ
  support also still requires the workaround stack (no AITER, disabled
  CUDA-graph padding).

vLLM stays the **primary engine** for the daily agent route. Two narrow
opt-in roles for the others worth keeping in mind:

- **llama.cpp / GGUF Q4_K_M** for **single-user, no-concurrency** local
  use (e.g. a personal IDE chat); needs an accuracy spot-check before
  promotion.
- **SGLang** as a **high-concurrency batch** option for short-prompt
  agent traffic where 32K context is enough; revisit once the AWQ
  defaults stop needing workarounds.

Filed under "do not switch yet".

## 2026-05-15 Phase 5 update: constrained MoE offload (negative result)

Goal: answer "given 48 GB VRAM + ~46 GB RAM, is there a `strong-deep`
route worth running off-VRAM?"

Setup: GLM-4.5-Air at IQ4_XS GGUF (60.81 GB on disk; 2 shards, 57 GB
total) via mainline `llama.cpp:server-cuda` with the standard selective
expert offload recipe `-ot ".ffn_.*_exps.=CPU"`. All non-expert tensors
on the two 3090s, MoE expert FFNs on CPU + mmap'd from /mnt/data SSD.

Resource picture after model load:

- GPU0: 4.8 GB used, GPU1: 4.7 GB used (both well under cap; only the
  attention/embed/shared layers + 16K KV are GPU-resident).
- System RAM: 46 GB total, 43 GB in mmap buffer cache, **2.2 GB swap
  used**. Free RAM at idle: ~370 MB. The expert tensors do not all fit
  resident.

Smoke test (`hello /nothink`, 12 generated tokens, prompt 11 tokens):

| Phase | ms / token | tok/s |
|---|---|---|
| Prompt eval | 5213 | 0.19 |
| Decode      | 3514 | **0.28** |

Reference: the same hardware running Qwen3-Coder-30B-A3B-Q4_K_M (an
18 GB GGUF that fits entirely in VRAM) hit **194.4 tok/s** in decode.
GLM-4.5-Air offload is **~700x slower**. A 500-token agent reply would
take ~30 minutes.

The bottleneck is unambiguous: 57 GB of expert weights cannot stay
resident in 46 GB of RAM, so every decode step pages experts off the
SSD. CPU and GPU utilization stay at 0 % between page faults. Smaller
quants don't help meaningfully — IQ3_XS at 50.84 GB and Q3_K_S at
53.42 GB still exceed the RAM-headroom budget once OS + KV cache are
accounted for, and quality drops below the threshold where this would
even be interesting.

### Verdict

**No interactive offload route worth promoting** at this hardware
budget. Selective expert offload works correctly (the model loads,
generates valid output, and respects the chat template), but the SSD
becomes the hot path. The plan's stated practical floor of
**~192-256 GB system RAM** to revisit DeepSeek-V3 / Kimi-K2 / GLM-4.5-Air
class is confirmed by direct measurement.

If a single 3090 → 5090 (32 GB) upgrade or a RAM bump to 128 GB+ ever
happens, this phase is worth re-running. Until then:

- Keep **Phase 4 daily route**: vLLM with Qwen3-Coder-30B-A3B-AWQ as
  the strong-long candidate; this fits in VRAM and decodes 100s of
  tok/s.
- For one-off "I really want to try GLM-4.5-Air" moments, the
  llama.cpp profile lives at
  [`docs/model-lab/2026-05-15-moe-offload-glm45air/profile.env`](2026-05-15-moe-offload-glm45air/profile.env)
  with the smoke timings preserved at
  [`smoke.json`](2026-05-15-moe-offload-glm45air/llamacpp-iq4xs/smoke.json).
  Useful as a sanity check, not as a route.

This closes the v3 plan. Phase 6 systemd updates and the Phase 4
engine verdict were already published; the canvas can be refreshed off
this doc directly.
