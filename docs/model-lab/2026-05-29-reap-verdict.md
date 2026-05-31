# REAP benchmark verdict (2026-05-29)

Roll-up from `scripts/model-lab/reap/` harness vs production
`QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` (strong-long).

Reference: [REAP paper](https://arxiv.org/abs/2510.13999),
[Cerebras collection](https://huggingface.co/collections/cerebras/cerebras-reap).

_Partial render (harness may still be running)._

## Smoke (phase 2)

| Key | passed | notes |
|-----|--------|-------|
| `baseline` | True | all cases ok |
| `test-a-llamacpp` | True | all cases ok |
| `test-a-vllm` | True | all cases ok |
| `test-b-llamacpp` | True | all cases ok |
| `test-b-vllm` | True | all cases ok |
| `test-c` | False | short_ping, tool_call, json_contract |
| `test-e` | True | all cases ok |
| `dual-fast` | — | _no smoke phase_ |
| `dual-strong-glm47` | — | _no smoke phase_ |

## Harness micro (p50 / decode tok/s)

| Model | code_config_review | short_ops_summary | tool_call_contract |
|-------|-------------------|-------------------|---------------------|
| `baseline` | 1714.0ms / 175.03 tok/s | 1116.5ms / 175.16 tok/s | 212.0ms / 165.09 tok/s match=1.0 |
| `test-a-llamacpp` | 2279.5ms / 131.61 tok/s | 1675.5ms / 131.305 tok/s | 286.0ms / 129.37 tok/s match=1.0 |
| `test-a-vllm` | Nonems / None tok/s | Nonems / None tok/s | Nonems / None tok/s match=— |
| `test-b-llamacpp` | 1725.0ms / 173.91 tok/s | 1012.0ms / 174.495 tok/s | 216.0ms / 162.04 tok/s match=1.0 |
| `test-b-vllm` | — / — / — | — / — / — | — / — / — |
| `test-c` | Nonems / None tok/s | Nonems / None tok/s | Nonems / None tok/s match=— |
| `test-e` | 2082.5ms / 144.055 tok/s | 1533.5ms / 143.04 tok/s | 314.0ms / 130.57 tok/s match=1.0 |
| `dual-fast` | 5865.0ms / 51.15 tok/s | 1664.0ms / 51.17 tok/s | 372.0ms / 48.39 tok/s match=0.0 |
| `dual-strong-glm47` | — / — / — | — / — / — | — / — / — |

## BFCL-lite

| Model | selection / args |
|-------|------------------|
| `baseline` | 1.0 / 1.0 |
| `test-a-llamacpp` | 1.0 / 1.0 |
| `test-a-vllm` | — |
| `test-b-llamacpp` | 1.0 / 1.0 |
| `test-b-vllm` | — |
| `test-c` | — |
| `test-e` | 1.0 / 1.0 |
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

