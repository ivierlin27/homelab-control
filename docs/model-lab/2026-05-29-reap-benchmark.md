# 2026-05-29 REAP model benchmark

Router-weighted Expert Activation Pruning ([REAP](https://arxiv.org/abs/2510.13999))
benchmark on the Alienware dual RTX 3090 stack. Harness lives in
[`scripts/model-lab/reap/`](../../scripts/model-lab/reap/).

## Prerequisites

- Run on **alienware-r10** (not the Mac Cursor host).
- `/mnt/data/hf-cache` and `/mnt/data/models/gguf` mounted.
- `podman`, `nvidia-smi`, `huggingface-cli`, Python 3.11+.
- Stop production strong-long during lab phases (script does this automatically).

## Quick run

```bash
cd ~/homelab-control
export REAP_DATE=2026-05-29

bash scripts/model-lab/reap/run_reap_phase.sh 1   # downloads
bash scripts/model-lab/reap/run_reap_phase.sh 2   # smoke
bash scripts/model-lab/reap/run_reap_phase.sh 3   # harness
bash scripts/model-lab/reap/run_reap_phase.sh 5   # verdict + restart prod
```

Optional dual-route test (Test D) after Test A smoke passes:

```bash
bash scripts/model-lab/reap/run_reap_phase.sh 4
```

## Tests

| ID | Profile | Model | Engine |
|----|---------|-------|--------|
| A | `test-a-glm47-flash-reap23-llamacpp.env` | GLM-4.7-Flash-REAP-23B UD-Q4_K_XL | llama.cpp TP=2 |
| A′ | `test-a-glm47-flash-reap23-vllm.env` | cerebras/GLM-4.7-Flash-REAP-23B-A3B | vLLM TP=2 |
| B | `test-b-qwen3-coder-reap25-*.env` | Qwen3-Coder-REAP-25B | vLLM BF16 or GGUF |
| C | `test-c-glm45air-reap82-llamacpp.env` | GLM-4.5-Air-REAP-82B IQ4_XS | llama.cpp GPU-resident |
| E | `test-e-qwen36-28b-reap-llamacpp.env` | 0xSero Qwen3.6-28B-REAP | llama.cpp (curiosity) |
| — | `baseline-qwen3-coder-30b-awq.env` | Qwen3-Coder-30B-A3B-AWQ | vLLM TP=2 |

Test **C** intentionally omits `-ot .ffn_.*_exps.=CPU` used in
[Phase 5 GLM-4.5-Air offload](2026-05-15-moe-offload-glm45air/profile.env).

## Test D: dual fast + strong

Topology when GLM-4.7-REAP weights are small enough:

- **GPU0**: `Qwen2.5-7B-Instruct` vLLM on port **8000** (`dual-fast-gpu0.env`)
- **GPU1**: GLM-4.7-Flash-REAP-23B llama.cpp on port **8002** (`dual-strong-glm47-reap-gpu1.env`)

Launcher: `scripts/model-lab/reap/launch_dual.sh` (separate podman names from lab defaults).

Production systemd units `alienware-vllm-fast` and `alienware-vllm-strong-long` remain
**mutually exclusive** by design. Dual mode is lab-only unless you add a dedicated
unit file after Test D passes.

Example production-style env snippets:
[`config/examples/vllm-reap-dual-fast.env.example`](../../config/examples/vllm-reap-dual-fast.env.example)

## Artifacts

| Path | Contents |
|------|----------|
| `/mnt/data/bench-artifacts/${REAP_DATE}-<key>/` | harness output |
| `docs/model-lab/bench-artifacts/${REAP_DATE}-<key>-summary-*.json` | copied summaries |
| `docs/model-lab/${REAP_DATE}-reap-smoke/` | smoke.json per candidate |
| `docs/model-lab/${REAP_DATE}-reap-verdict.md` | human-facing verdict |

## Promotion bars

See [`2026-05-29-reap-verdict.md`](2026-05-29-reap-verdict.md) after phase 5.
