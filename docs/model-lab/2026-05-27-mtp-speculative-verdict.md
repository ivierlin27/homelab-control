# MTP / speculative decoding verdict (2026-05-27)

Evaluation of Multi-Token Prediction on dual RTX 3090 (Alienware).
Primary path: **native llama.cpp** (upstream `aa50b2c`) + Unsloth MTP GGUFs.

## Executive summary

- **27B dense MTP (draft-2)** improves tool-call decode **39.1 → 62.3 tok/s** (+59%), BFCL **100%/100%**, agent rubric unchanged.
- **35B MoE MTP (draft-2)** reaches **~156 tok/s** on tool-call micro.
- **vLLM MTP TP=1** failed: ~20 GiB weights on one 3090 leaves insufficient KV cache.
- **vLLM MTP TP=2** still blocked by [#41190](https://github.com/vllm-project/vllm/issues/41190).
- **Keep `Qwen3-Coder-30B-A3B-AWQ` as daily vLLM driver**; llama.cpp MTP is an opt-in single-user fast path.

## llama.cpp MTP results

| Config | tool_call tok/s | vs 27B baseline | short_ops tok/s | BFCL sel/args | Agent |
|--------|----------------:|----------------:|----------------:|--------------|-------|
| `qwen36-27b-mtp-llamacpp-baseline` | 39.1 | baseline | 40.6 | 100%/100% | pr_review:3/3; tool_chain:2/2 |
| `qwen36-27b-mtp-llamacpp-d2` | 62.3 | +59% | 52.9 | 100%/100% | pr_review:3/3; tool_chain:2/2 |
| `qwen36-27b-mtp-llamacpp-d3` | 67.7 | +73% | 45.7 | 100%/100% | pr_review:3/3; tool_chain:2/2 |
| `qwen36-35b-mtp-llamacpp-d2` | 156.2 | +300% | 142.7 | 100%/100% | pr_review:3/3; tool_chain:1/2 |

**Recommendation:** use `--spec-draft-n-max 2` for agent/tool workloads on 27B dense.

## vLLM MTP (TP=1) — not viable on dual 3090

| Config | Status |
|--------|--------|
| `qwen36-27b-awq-tp1-baseline` | failed (OOM / timeout) |
| `qwen36-27b-mtp-vllm-tp1-k1` | failed (OOM / timeout) |
| `qwen36-27b-mtp-vllm-tp1-k2` | failed (OOM / timeout) |

## Quality gate

| Criterion | Result |
|-----------|--------|
| BFCL ≥93% | **PASS** (100% all llama.cpp runs) |
| Agent tasks | **PASS** (27B: pr_review 3/3, tool_chain 2/2) |
| Context ≥32K | **PASS** |
| Speedup >20% | **PASS** (+59% tool_call @ draft-2) |

## Artifacts

- Alienware: `/mnt/data/bench-artifacts/2026-05-27-*`
- Repo: [`docs/model-lab/bench-artifacts/`](bench-artifacts/), [`2026-05-27-mtp-agent-tasks/`](2026-05-27-mtp-agent-tasks/)

## Future work

- vLLM TP=2 MTP when #41190 fixed
- P-EAGLE for Qwen3-Coder-30B
- 35B MoE llama baseline (no MTP) for MoE speedup ratio
