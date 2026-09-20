# Qwopus3.6 and Qwen3.6 model benchmark

Executable implementation: [`scripts/model-lab/qwopus36/README.md`](../../scripts/model-lab/qwopus36/README.md).

## Goals

- Compare **Qwopus** community fine-tunes vs **Qwen3.6** base vs current daily **Qwen3-Coder-30B-A3B-AWQ**
- Reuse `scripts/bench/` v2 harness plus new **agent task** battery
- Phases 1–6 are independent; run when Alienware is idle

## Candidates

| Key | Model |
|-----|--------|
| `qwopus36-35b-a3b-v1` | `Avesed/Qwopus3.6-35B-A3B-v1-int4-mixed` |
| `qwen36-35b-a3b-awq` | `QuantTrio/Qwen3.6-35B-A3B-AWQ` |
| `qwopus36-27b-v2` | `mconcat/Qwopus3.6-27B-v2-AWQ-4bit` |
| `qwen36-27b-awq` | `QuantTrio/Qwen3.6-27B-AWQ` |
| `qwopus36-27b-v2-mtp` | GGUF + llama.cpp |
| `qwopus36-35b-mxfp4` | noctrex MXFP4_MOE GGUF (Phase 4) |

## Phases

1. Download + smoke (`run_phase.sh 1`)
2. Harness battery (`run_phase.sh 2`) — ~4–5 h
3. Agent tasks (`run_phase.sh 3`)
4. MXFP4 topologies (`run_phase.sh 4`, optional)
5. Base 35B agent control (`run_phase.sh 5`)
6. Verdict markdown (`run_phase.sh 6`)

## Baselines

- Harness: `docs/model-lab/bench-artifacts/2026-05-15-qwen3-coder-30b-a3b-awq/`
- 35B base harness: `2026-05-15-qwen36-35b-a3b-awq-baseline/`

## Promotion

See [STRONG_MODEL_STRATEGY.md](../STRONG_MODEL_STRATEGY.md) and [STRONG_MODEL_BAKEOFF.md](STRONG_MODEL_BAKEOFF.md).
