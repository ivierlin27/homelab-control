# REAP benchmark verdict (2026-05-29)

Roll-up from `scripts/model-lab/reap/` harness vs production
`QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` (strong-long).

Reference: [REAP paper](https://arxiv.org/abs/2510.13999),
[Cerebras collection](https://huggingface.co/collections/cerebras/cerebras-reap).

## Smoke (phase 2)

| Key | passed | notes |
|-----|--------|-------|
| `dual-fast` | — | _no smoke phase_ |
| `dual-strong-glm47` | — | _no smoke phase_ |

## Harness micro (p50 / decode tok/s)

| Model | code_config_review | short_ops_summary | tool_call_contract |
|-------|-------------------|-------------------|---------------------|
| `baseline` | — / — / — | — / — / — | — / — / — |
| `test-a-llamacpp` | — / — / — | — / — / — | — / — / — |
| `test-a-vllm` | — / — / — | — / — / — | — / — / — |
| `test-b-llamacpp` | — / — / — | — / — / — | — / — / — |
| `test-b-vllm` | — / — / — | — / — / — | — / — / — |
| `test-c` | — / — / — | — / — / — | — / — / — |
| `test-e` | — / — / — | — / — / — | — / — / — |
| `dual-fast` | — / — / — | — / — / — | — / — / — |
| `dual-strong-glm47` | — / — / — | — / — / — | — / — / — |

## BFCL-lite

| Model | selection / args |
|-------|------------------|
| `baseline` | — |
| `test-a-llamacpp` | — |
| `test-a-vllm` | — |
| `test-b-llamacpp` | — |
| `test-b-vllm` | — |
| `test-c` | — |
| `test-e` | — |
| `dual-fast` | — |
| `dual-strong-glm47` | — |

## Promotion bars (from plan)

- **test-a-llamacpp**: Matches/beats baseline on BFCL-lite, code micro, RULER 65K; no empty content on plain chat (GLM thinking mode).
- **test-b-llamacpp**: Matches baseline within noise; frees >=2 GB/GPU vs 30B AWQ.
- **test-c**: ≥30 tok/s single-stream, RULER 32K + BFCL pass → strong-deep opt-in.

## Recommendation

_Fill after reviewing artifacts on Alienware:_

1. **Daily strong-long**: keep Qwen3-Coder-30B-A3B-AWQ / promote GLM-4.7-Flash-REAP-23B
2. **strong-deep opt-in**: GLM-4.5-Air-REAP-82B if Test C clears bar
3. **Dual fast+strong**: enable only if Test D micro passes on both ports
4. **Test E**: document reasoning-leakage vs Phase-2 Qwen3.6 baseline

Promotion checklist ([STRONG_MODEL_STRATEGY.md](../STRONG_MODEL_STRATEGY.md)):
- [ ] Clean startup at target context
- [ ] Gateway + local endpoint smoke
- [ ] Matches or beats baseline on harness
- [ ] Tool-call reliability acceptable
- [ ] PR + 24h production soak

