# Qwopus3.6 benchmark verdict (2026-05-26)

Executed on Alienware dual RTX 3090 via [`scripts/model-lab/qwopus36/`](../../scripts/model-lab/qwopus36/README.md).
Production `alienware-vllm-strong-long` was stopped for lab runs and restarted after completion.

## Harness micro (p50 latency ms from `runs/*.jsonl`)

| Model | code_config_review | short_ops_summary | tool_call_contract |
|-------|------------------:|------------------:|-------------------:|
| `qwen3-coder-30b-a3b-awq` (daily) | 1712 | 1166 | 211 |
| `qwopus36-35b-a3b-v1` | 2137 | 1596 | 374 |
| `qwopus36-27b-v2` | 5692 | 4163 | 1015 |
| `qwen36-27b-awq` | 4817 | 3506 | 893 |

**Note:** `summary.json` in each artifact dir reflects the **soak** runner (last step). Per-test micro stats live under `runs/<test>.jsonl` on the host at `/mnt/data/bench-artifacts/2026-05-26-<key>/`.

Baseline reference (2026-05-15): Qwen3-Coder ~1707 / 918 / 212 ms p50 with ~175 tok/s decode on short tests.

## Agent task rubric (5 tasks, max score per task)

| Model | pr_review | finance | incident | tool_chain | json | Total completion tok |
|-------|-----------|---------|----------|------------|------|---------------------|
| `qwen3-coder-30b-a3b-awq` | 3/3 | 3/3 | 3/3 | 2/2 | 3/3 | ~1456 |
| `qwopus36-35b-a3b-v1` | 3/3 | 3/3 | 3/3 | 2/2 | 3/3 | ~1412 |
| `qwen36-35b-a3b-awq` | 3/3 | 3/3 | 3/3 | 2/2 | 3/3 | ~1599 |
| `qwopus36-27b-v2` | 3/3 | 2/3 | 3/3 | 2/2 | 3/3 | ~922 |
| `qwen36-27b-awq` | 3/3 | 2/3 | 3/3 | 2/2 | 3/3 | ~1101 |

Full transcripts: [`2026-05-26-qwopus36-agent-tasks/`](2026-05-26-qwopus36-agent-tasks/).

## Phase 4: MXFP4 single-GPU

`noctrex/Qwopus3.6-35B-A3B-v1-MXFP4_MOE_BF16` (~21 GB) on GPU0 via llama.cpp:

- Boot OK, `micro` + `bfcl` completed (`2026-05-26-qwopus36-35b-mxfp4-gpu0/`)
- Enables a future **split topology** (strong on GPU0 + fast sidecar on GPU1) if quality matches vLLM AWQ

## Failed / skipped

| Item | Reason |
|------|--------|
| `Qwopus3.6-27B-v2-MTP` GGUF | llama.cpp load error: missing tensor `blk.64.ssm_conv1d.weight` — needs newer llama.cpp or different GGUF build |
| Full 60 min soak on coder rerun | Truncated to 10 min soak for lab matrix; prior May baseline soak unchanged |

## Fine-tune delta

**35B MoE (Qwopus vs base):** Agent rubric tied; Qwopus slightly fewer total completion tokens than base Qwen3.6-35B on this sample. Harness latency between Qwopus INT4-mixed and Qwen3-Coder (not directly comparable — different quants/arch).

**27B dense (Qwopus vs base):** Nearly identical rubric scores; both missed one finance sub-check (reasonable_categories heuristic). Both much **slower** p50 latency than Qwen3-Coder on micro tests in this vLLM TP=2 profile.

**Qwopus vs daily Qwen3-Coder:** Coder remains **fastest** on micro latency and matches or beats on agent rubric. Qwopus 35B is a credible alternative when prioritizing Qwen3.6-35B MoE capabilities + 128K context over pure codegen tuning.

## Recommendation

**Keep `QuantTrio/Qwen3-Coder-30B-A3B-AWQ` as the daily `homelab-strong-long` driver** for agent throughput and proven BFCL/tool stability.

**Optional modes to add (not promotion):**

1. **`Qwopus3.6-35B-A3B-v1`** (`Avesed/...-int4-mixed`) — opt-in route when testing token-efficient reasoning on the same TP=2 slot; re-run BFCL gate before production.
2. **`Qwopus3.6-35B` MXFP4 on GPU0** — experiment with dual-route (MXFP4 strong + 7B/14B fast on GPU1) if a follow-up BFCL pass is clean.
3. **Do not promote 27B pair as daily strong** on this hardware profile — latency regression vs Coder without agent-task wins.

**Next steps if promoting Qwopus 35B:**

1. Update `~/.config/homelab-control/vllm-strong-long.env` model + `--quantization compressed-tensors`
2. Gateway smoke + `apps/_shared/test_gateway_routes.py`
3. 24h soak under real agent traffic
4. PR updating `docs/STRONG_MODEL_STRATEGY.md`

## Artifact index

| Path | Content |
|------|---------|
| `/mnt/data/bench-artifacts/2026-05-26-*` | Full harness runs |
| `docs/model-lab/bench-artifacts/2026-05-26-*-summary.json` | Committed soak summaries |
| `docs/model-lab/2026-05-26-qwopus36-smoke/` | Phase 1 smoke JSON |
| `docs/model-lab/2026-05-26-qwopus36-agent-tasks/` | Phase 3/5 agent runs |
