# 2026-05-14 Dual RTX 3090 vLLM Report

## Executive recommendation

Run the daily fast+strong posture as two single-GPU vLLM services:

- GPU0: `Qwen/Qwen2.5-7B-Instruct` as `homelab-fast-vllm`
- GPU1: `Qwen/Qwen2.5-14B-Instruct-AWQ` as `homelab-strong-vllm`

This is the best current fit for seamless escalation because both routes can
stay loaded at the same time, both answer through OpenAI-compatible vLLM
endpoints, and the strong route keeps clean JSON behavior without visible
reasoning tokens.

Do not run both services with `--device nvidia.com/gpu=all`. The installed
strong service was failing while fast was active because both units exposed all
GPUs. Explicit CDI pinning works:

- fast: `--device nvidia.com/gpu=0`
- strong: `--device nvidia.com/gpu=1`

After the tests, I left a temporary strong backend running on GPU1 at port
`8001` with the recommended `Qwen2.5-14B-AWQ` profile. The installed systemd
unit still needs to be updated before this survives a reboot or service restart.

## Hardware observations

- Host: Alienware Fedora host at `192.168.1.45`
- GPUs: 2x RTX 3090, 24 GB each, driver `580.159.03`
- Topology: `PHB` between cards, no NVLink-class path
- Fast loaded on GPU0 used about `22.7 GiB`
- Strong loaded on GPU1 used about `21.4-22.2 GiB`

Because the cards are PCIe/PHB connected, tensor-parallel strong models are
useful for long-context single-model labs, but they are not the right default
for "fast and strong loaded at the same time." TP=2 consumes both cards and
removes the always-warm fast lane.

## Models tested

### Current fast route

- Model: `Qwen/Qwen2.5-7B-Instruct`
- GPU: GPU0
- Context: `32768`
- Result: stays as the fast route
- Notes: fast enough for summaries, routing, simple JSON, and short agent
  tasks. It passed the near-32K recall test but is not reliable enough for
  strong escalation decisions.

### Current strong route

- Model: `Qwen/Qwen2.5-14B-Instruct-AWQ`
- GPU: GPU1
- Context: `32768`
- Result: best current strong default
- Notes: fastest strong candidate in this suite, clean JSON, and the only
  strong candidate that explicitly cited all long-context NEEDLE labels.

### Qwen3 candidate

- Model: `Qwen/Qwen3-14B-AWQ`
- GPU: GPU1
- Context: `32768`
- Result: promising, not a drop-in default on the current runtime
- Notes: starts only without `--enable-reasoning` on this pinned image. It
  emitted visible `<think>` blocks and returned numeric confidence for a string
  schema field. It did catch the long-context needles.

### Code-specialized candidate

- Model: `Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`
- GPU: GPU1
- Context: `32768`
- Result: not recommended as the general strong route
- Notes: short-prompt speed was similar, but long-context latency was slow and
  the config-review answer was not materially better than the general 14B.

## Benchmark suite

Each strong candidate was tested while the fast model stayed loaded on GPU0.
The suite covered:

- short operations summary
- service/config review
- strict JSON response contract
- near-limit long-context recall with three needles
- oversized context rejection behavior
- simultaneous fast+strong calls

Raw outputs are recorded in:

- `docs/model-lab/2026-05-14-dual-3090-summary.json`
- `docs/model-lab/2026-05-14-dual-3090-results.jsonl`
- `docs/model-lab/2026-05-14-dual-3090-qwen3-14b-summary.json`
- `docs/model-lab/2026-05-14-dual-3090-qwen3-14b-results.jsonl`
- `docs/model-lab/2026-05-14-dual-3090-coder14b-summary.json`
- `docs/model-lab/2026-05-14-dual-3090-coder14b-results.jsonl`

## Key measured results

Short/code latency:

- `Qwen2.5-14B-AWQ`: `3.29s` short, `3.28s` config review
- `Qwen3-14B-AWQ`: `3.22s` short, `3.20s` config review
- `Qwen2.5-Coder-14B-AWQ`: `3.31s` short, `3.30s` config review

Strict JSON:

- `Qwen2.5-14B-AWQ`: valid object, string confidence
- `Qwen3-14B-AWQ`: valid object, numeric confidence despite requested contract
- `Qwen2.5-Coder-14B-AWQ`: valid object, string confidence

Near-limit long context:

- Prompt tokens: `31483`
- `Qwen2.5-14B-AWQ`: `23.04s`, all three NEEDLE labels present
- `Qwen3-14B-AWQ`: `22.46s`, all three NEEDLE labels present, but response was
  cut off in visible thinking text
- `Qwen2.5-Coder-14B-AWQ`: `22.07s`, facts present, labels omitted

Oversized context:

- All models rejected `32509` input tokens plus `260` output tokens with a
  clear vLLM 400: requested total `32769` exceeds `32768`.
- Practical cap should reserve output headroom. Treat about `31K` prompt tokens
  as the reliable ceiling for these 32K profiles.

Concurrent fast+strong:

- Fast + `Qwen2.5-14B-AWQ`: combined wall `4.346s`
- Fast + `Qwen3-14B-AWQ`: combined wall `4.338s`
- Fast + `Qwen2.5-Coder-14B-AWQ`: combined wall `4.348s`

This confirms the two-card setup supports simultaneous fast and strong requests
when the services are pinned to separate GPUs.

## Qwen3.6 / 27B research

Recent dual-3090 writeups point to `Qwen3.6-27B-AWQ-INT4` as an exciting
single strong model for agentic coding, typically with:

- `--tensor-parallel-size 2`
- `--disable-custom-all-reduce`
- FP8 KV cache
- `--block-size 16`
- FlashInfer attention/sampler
- MTP speculative decoding
- long context targets around `160K`

That shape is a strong lab target, but it is not the default answer for seamless
escalation. It uses both GPUs, so the fast route cannot remain warm at the same
time. It also depends on runtime flags and model/parser behavior that the current
pinned image does not fully support yet: this image rejected `--enable-reasoning`
during the Qwen3-14B test.

Recommended next lab:

1. Keep the default always-on stack as `7B fast + 14B AWQ strong`.
2. Add an opt-in "strong-long" profile for `Qwen3.6-27B-AWQ-INT4` or equivalent
   community build on TP=2.
3. Test it separately for repository-scale context, Cline/Roo-style tool calling,
   and 100K+ prompt stability.
4. Promote it only as a mode switch, not as the always-on escalation path, unless
   you decide the warm fast lane matters less than a 160K strong context.

## Operational follow-up

Persist the dual-service posture by changing the systemd units to accept a GPU
device variable and setting:

- `VLLM_FAST_GPU_DEVICE=0`
- `VLLM_STRONG_GPU_DEVICE=1`

Then remove the one-model-mode assumption from the docs and mode switcher, or
replace it with explicit modes:

- `dual`: fast on GPU0 and strong on GPU1
- `fast-only`: fast on one GPU, strong stopped
- `strong-long`: TP=2 lab profile for Qwen3.6/27B-style models
