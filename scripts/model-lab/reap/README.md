# REAP model lab (dual RTX 3090)

Benchmark harness for [REAP](https://arxiv.org/abs/2510.13999) (Router-weighted Expert
Activation Pruning) checkpoints against the production
`QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ` strong-long baseline.

Run on the Alienware from the homelab-control repo root:

```bash
cd ~/homelab-control   # or your clone path on alienware-r10
export REAP_DATE=2026-05-29

# Phase 1: download GGUF / HF weights (first run may take hours)
bash scripts/model-lab/reap/run_reap_phase.sh 1

# Phase 2: smoke all candidates + baseline
bash scripts/model-lab/reap/run_reap_phase.sh 2

# Phase 3: full harness (micro, serve-sweep, ruler, bfcl, soak)
bash scripts/model-lab/reap/run_reap_phase.sh 3

# Phase 4: optional dual-route (fast 7B on GPU0 + strong REAP on GPU1)
bash scripts/model-lab/reap/run_reap_phase.sh 4

# Phase 5: render verdict markdown
bash scripts/model-lab/reap/run_reap_phase.sh 5
```

Artifacts land under `/mnt/data/bench-artifacts/${REAP_DATE}-<key>/` with copies of
`summary-*.json` in `docs/model-lab/bench-artifacts/`.

## Tests (plan mapping)

| Phase | Key | Model | Engine |
|-------|-----|-------|--------|
| A | `glm47-flash-reap23-llamacpp` | unsloth GLM-4.7-Flash-REAP-23B GGUF UD-Q4_K_XL | llama.cpp TP=2 |
| A (promote) | `glm47-flash-reap23-vllm` | cerebras/GLM-4.7-Flash-REAP-23B-A3B | vLLM TP=2 |
| B | `qwen3-coder-reap25-vllm` | cerebras/Qwen3-Coder-REAP-25B-A3B + AWQ | vLLM TP=2 |
| C | `glm45air-reap82-llamacpp` | bartowski GLM-4.5-Air-REAP-82B IQ4_XS (GPU-resident) | llama.cpp TP=2 |
| E | `qwen36-28b-reap-llamacpp` | 0xSero Qwen3.6-28B-REAP Q4_K_M | llama.cpp TP=2 |
| baseline | `qwen3-coder-30b-awq-baseline` | production checkpoint | vLLM TP=2 |

Test D (`dual-fast-strong`) runs only after Test A or B passes smoke; see
`profiles/dual-*.env` and `docs/model-lab/2026-05-29-reap-benchmark.md`.
