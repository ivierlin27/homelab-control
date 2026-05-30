# Qwopus3.6 / Qwen3.6 benchmark suite

Phased evaluation of Qwopus community fine-tunes vs Qwen3.6 base models and the
current `Qwen3-Coder-30B-A3B-AWQ` daily driver. See
[`docs/model-lab/qwopus36-benchmark-plan.md`](../../../docs/model-lab/qwopus36-benchmark-plan.md).

Run on **Alienware** (`/home/kenns/git/homelab-control`). Stop production inference
before lab work:

```bash
systemctl --user stop alienware-vllm-strong-long.service
```

## Quick start

```bash
cd ~/git/homelab-control
export QWOPUS36_DATE=2026-05-26   # artifact date prefix

# Phase 1: download + smoke
bash scripts/model-lab/qwopus36/run_phase.sh 1

# Phase 2: harness (long; unattended)
bash scripts/model-lab/qwopus36/run_phase.sh 2

# Phase 3: agent tasks
bash scripts/model-lab/qwopus36/run_phase.sh 3

# Phase 4: MXFP4 topologies (optional)
bash scripts/model-lab/qwopus36/run_phase.sh 4

# Phase 5: base 35B agent control
bash scripts/model-lab/qwopus36/run_phase.sh 5

# Phase 6: verdict markdown
bash scripts/model-lab/qwopus36/run_phase.sh 6
```

## MTP / speculative decoding (2026-05-27)

Requires native llama.cpp build (stock container lacks Qwen3.6 GDN tensors):

```bash
export MTP_DATE=2026-05-27
bash scripts/model-lab/qwopus36/run_mtp_phase.sh 1   # download + build llama.cpp
bash scripts/model-lab/qwopus36/run_mtp_phase.sh 2   # llama.cpp MTP matrix
bash scripts/model-lab/qwopus36/run_mtp_phase.sh 3   # vLLM TP=1 (may OOM on 3090)
bash scripts/model-lab/qwopus36/run_mtp_phase.sh 4   # verdict
```

Verdict: [`docs/model-lab/2026-05-27-mtp-speculative-verdict.md`](../../../docs/model-lab/2026-05-27-mtp-speculative-verdict.md)

## Model registry

| Key | HF model | Quant | TP |
|-----|----------|-------|-----|
| `qwopus36-35b-a3b-v1` | `Avesed/Qwopus3.6-35B-A3B-v1-int4-mixed` | compressed-tensors | 2 |
| `qwen36-35b-a3b-awq` | `QuantTrio/Qwen3.6-35B-A3B-AWQ` | awq_marlin | 2 |
| `qwopus36-27b-v2` | `mconcat/Qwopus3.6-27B-v2-AWQ-4bit` | awq_marlin | 2 |
| `qwen36-27b-awq` | `QuantTrio/Qwen3.6-27B-AWQ` | awq_marlin | 2 |
| `qwopus36-27b-v2-mtp` | GGUF via llama.cpp | Q4_K_M | 1 |
| `qwopus36-35b-mxfp4` | `noctrex/...MXFP4_MOE-GGUF` | llama.cpp | 1–2 |

Artifacts: `/mnt/data/bench-artifacts/${QWOPUS36_DATE}-<key>/` and
`docs/model-lab/bench-artifacts/` (committed summaries).
