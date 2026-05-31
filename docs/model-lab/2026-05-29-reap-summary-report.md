# REAP benchmark summary report (2026-05-29)

Human-readable roll-up of the Alienware dual-RTX-3090 REAP model lab. Raw JSON lives under `docs/model-lab/bench-artifacts/` and `docs/model-lab/2026-05-29-reap-smoke/`; full harness trees are on Alienware at `/mnt/data/bench-artifacts/2026-05-29-*`.

**References:** [Runbook](2026-05-29-reap-benchmark.md) · [Verdict tables](2026-05-29-reap-verdict.md) · [REAP paper](https://arxiv.org/abs/2510.13999)

---

## Executive summary

| Question | Answer |
|----------|--------|
| **Keep daily strong-long?** | **Yes — stay on `Qwen3-Coder-30B-A3B-Instruct-AWQ`** until a REAP candidate clears long-context + RULER + gateway soak. |
| **Best REAP candidate?** | **Test A — `GLM-4.7-Flash-REAP-23B` (llama.cpp Q4_K_XL)** — passes smoke, BFCL, tools; **~9–12 GB/GPU** vs **~23.5 GB** baseline; code **~25% slower**; RULER/JSON-schema gaps. |
| **Test B (Qwen3-Coder-REAP-25B)?** | Speed ≈ baseline and **~14 GB/GPU**, but **long-context micro failed**; modest VRAM win vs Test A. |
| **Test C (82B REAP)?** | **Not evaluated** — server never became ready (20 min load timeout). |
| **Test E (Qwen3.6 REAP)?** | **Curiosity only** — good short tasks + BFCL; RULER/long-context failed. |
| **Dual fast+strong (Test D)?** | **Lab-only** — 7B fast path **~51 tok/s**, **tool-call match 0%** under dual micro. |

---

## Setup

| Item | Value |
|------|--------|
| Host | Alienware R10, 2× RTX 3090 (24 GB each), 48 GB system RAM |
| Baseline | `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ`, vLLM TP=2, 131K ctx |
| Harness | `scripts/model-lab/reap/` — smoke, micro, serve-sweep, RULER, BFCL-lite, soak (baseline) |
| Date | 2026-05-29 lab ID; runs completed 2026-05-30 |

---

## Smoke tests (quick API health)

| Model | Passed | Notes |
|-------|--------|-------|
| Baseline 30B AWQ | Yes | ping, tool, JSON |
| Test A GLM-4.7-REAP (llama.cpp) | Yes | JSON wrapped in markdown fences (GLM style) |
| Test A GLM-4.7-REAP (vLLM) | Yes | (harness later failed — see below) |
| Test B Qwen3-Coder-REAP-25B | Yes | |
| Test C GLM-4.5-Air-REAP-82B | **No** | **Connection refused** — llama.cpp did not listen in time (phase 2 + rerun) |
| Test E Qwen3.6-28B-REAP | Yes | |

---

## VRAM footprint (after micro benchmark)

Approximate **per-GPU** `memory.used_mib` from harness snapshots:

| Model | GPU0 | GPU1 | vs baseline |
|-------|------|------|-------------|
| **Baseline** 30B AWQ | 23,761 | 23,473 | — |
| **Test A** GLM-4.7-REAP-23B | 11,923 | 11,349 | **~−12 GB / GPU** |
| **Test B** Qwen3-Coder-REAP-25B | 14,731 | 13,677 | **~−9 GB / GPU** |
| **Test E** Qwen3.6-28B-REAP | 9,501 | 9,249 | **~−14 GB / GPU** |
| **Dual fast** 7B (GPU0 only loaded) | 19,399 | 19,163 | N/A (split topology) |

Test A frees the most headroom for a second model or longer KV cache without dual-route complexity.

---

## Micro benchmark (coding & tools)

Median latency (p50) and decode speed (p50 tok/s). **ok_rate** = fraction of successful completions.

### Code generation (`code_config_review`, 300 tokens)

| Model | p50 latency | Decode tok/s | ok_rate |
|-------|-------------|--------------|---------|
| Baseline | 1,714 ms | **175** | 100% |
| Test A GLM-4.7-REAP | 2,280 ms | 132 | 100% |
| Test B Qwen3-Coder-REAP | 1,725 ms | **174** | 100% |
| Test E Qwen3.6-REAP | 2,083 ms | 144 | 100% |
| Dual fast 7B | 5,865 ms | 51 | 100% |

Test A is **~25% slower** than baseline on sustained code gen; Test B matches baseline speed.

### Tool calling (`tool_call_contract`)

| Model | p50 latency | Decode tok/s | Tool match |
|-------|-------------|--------------|------------|
| Baseline | 212 ms | 165 | **100%** |
| Test A | 286 ms | 129 | **100%** |
| Test B | 216 ms | 162 | **100%** |
| Test E | 314 ms | 131 | **100%** |
| Dual fast 7B | 372 ms | 48 | **0%** |

### Structured JSON (`structured_json_contract`)

| Model | p50 latency | JSON valid | Schema pass |
|-------|-------------|------------|-------------|
| Baseline | 238 ms | 100% | **0%** |
| Test A | 367 ms | 100% | **0%** (often markdown-wrapped) |
| Test B | 263 ms | 100% | **0%** |
| Test E | 362 ms | 100% | **0%** |

Schema pass is **0% across the board** — likely a harness/GLM formatting issue, not unique to REAP.

### Long context (needle tests)

| Model | 31K prompt ok_rate | 60K prompt ok_rate |
|-------|-------------------|-------------------|
| Baseline | **100%** | **100%** |
| Test A (131K ctx rerun) | **100%** | **100%** |
| Test B | **0%** | **0%** |
| Test E | **0%** | **0%** |

After bumping Test A to **131K** context, long-context micro **passed** on rerun. Test B/E still failed (profile/context limits not retuned).

---

## BFCL-lite (tool selection & arguments)

| Model | Selection rate | Args rate |
|-------|----------------|-----------|
| Baseline | **1.0** | **1.0** |
| Test A llama.cpp | **1.0** | **1.0** |
| Test B llama.cpp | **1.0** | **1.0** |
| Test E | **1.0** | **1.0** |
| Test A vLLM | — | health check failed |
| Test C | — | health check failed |

---

## RULER (retrieval @ 32K context)

**32K match rate** by task (1.0 = perfect):

| Task | Baseline | Test A | Test B | Test E |
|------|----------|--------|--------|--------|
| niah_multi_key | 1.0 | 0.0 | 1.0 | 0.0 |
| vt_2hop | 0.6 | 0.0 | 1.0 | 0.0 |
| niah_single_simple | 0.4 | 0.0 | 0.8 | 0.0 |
| niah_multi_value | 0.6 | 0.0 | 0.4 | 0.0 |
| common_words_extract | 0.4 | 0.0 | 0.4 | 0.0 |

Baseline is **mixed** (0.4–1.0). Test A and Test E scored **0% on all RULER tasks** — likely chat template / thinking-mode / output format, not proof the weights are useless. Test B was **partially usable** at 32K (several tasks at 0.8–1.0).

Test A RULER at 65K/131K was run but **0% match** across lengths — treat as **needs template/tuning**, not a promotion blocker alone if micro+BFCL are good enough for your coding route.

---

## Serve sweep (concurrency, p95 latency ms)

| Concurrency | Baseline p95 | Test A p95 |
|-------------|--------------|------------|
| 1 | 1,129 | 1,554 |
| 2 | 1,299 | 3,215 |
| 4 | 1,570 | 2,758 |
| 8 | 1,957 | 5,765 |

Test A is comparable at low concurrency but **degrades more** at c8 — expect less batch headroom than 30B AWQ under load.

---

## Soak (baseline only, 10 min @ c4)

| Metric | Value |
|--------|--------|
| VRAM peak | 23,761 MiB |
| Tool match rate | 100% |
| Errors | 0 |
| p95 drift | −1 ms (stable) |

---

## Test-by-test verdict

### Baseline — Qwen3-Coder-30B-A3B-AWQ

Production reference. Strong code speed, full long-context micro, BFCL perfect, RULER middling. Fills both GPUs (~23.5 GB each).

### Test A — GLM-4.7-Flash-REAP-23B (llama.cpp UD-Q4_K_XL)

**Pros:** Smoke OK; BFCL 100%; tools 100%; long-context micro OK after 131K ctx; **large VRAM savings** (~12 GB/GPU).  
**Cons:** ~25% slower code tok/s; RULER 0% without tuning; JSON schema harness 0%; serve-sweep worse at high concurrency.  
**vLLM path:** Did not stay healthy for harness (BF16 TP=2 likely VRAM/startup) — **promote llama.cpp path only** for now.

**Promotion bar status:** Partial — meets tools/BFCL/VRAM; misses RULER parity and production soak.

### Test B — Qwen3-Coder-REAP-25B (llama.cpp Q4_K_M)

**Pros:** Code speed ≈ baseline; BFCL 100%; ~9 GB/GPU saved vs baseline.  
**Cons:** Long-context micro **failed**; RULER weak vs baseline on some tasks.  
**Use case:** Alternative if you want Qwen-coder family + REAP but **Test A saves more VRAM** with acceptable speed tradeoff.

### Test C — GLM-4.5-Air-REAP-82B IQ4_XS

**Not benchmarked.** 44 GB GGUF did not reach `/v1/models` within 20 minutes (load timeout). Prior non-REAP 82B needed MoE CPU offload; REAP shrink may still be too heavy for interactive lab without longer timeout or `-c`/parallel tuning.

### Test D — Dual 7B fast + GLM-4.7-REAP strong

Micro-only. Fast leg: **~51 tok/s**, tool match **0%**. Strong leg not fully captured in committed summaries. **Do not enable prod dual** from this run.

### Test E — Qwen3.6-28B-REAP (0xSero Q4)

**Pros:** Solid short-task micro, BFCL 100%, low VRAM.  
**Cons:** Long-context micro failed; RULER 0%. Document for curiosity / MLX comparison per plan — **not a daily driver candidate**.

---

## Recommendations

1. **Restore production** if still stopped:  
   `REAP_LEAVE_PROD_STOPPED=0 bash scripts/model-lab/reap/run_reap_phase.sh prod`

2. **Daily strong-long:** Keep **Qwen3-Coder-30B-A3B-AWQ** until Test A completes gateway + 24h soak with your real prompts.

3. **Optional next experiment (Test A trial):**  
   - Point a **non-prod** gateway route at `lab-glm47-flash-reap23-llamacpp` (llama.cpp, 131K).  
   - Fix RULER/template (GLM thinking, `--jinja`, JSON fence stripping).  
   - Re-run harness `ruler` only after template fix.

4. **Defer:** Test C until load strategy works (longer timeout, lower `-c`, or partial CPU offload). Test A vLLM until BF16 fits or quant path exists.

5. **Ignore for prod:** Dual-route (Test D) and Test E unless you explicitly want a research sidebar.

---

## Artifact index

| What | Mac path | Alienware path |
|------|----------|----------------|
| This report | `docs/model-lab/2026-05-29-reap-summary-report.md` | same |
| Verdict (auto tables) | `docs/model-lab/2026-05-29-reap-verdict.md` | same |
| Smoke JSON | `docs/model-lab/2026-05-29-reap-smoke/*/smoke.json` | same |
| Summary JSON (partial) | `docs/model-lab/bench-artifacts/2026-05-29-*-summary.json` | same |
| Full harness | — | `/mnt/data/bench-artifacts/2026-05-29-<key>/` |
| Logs | gitignored `*.log` | `docs/model-lab/2026-05-29-reap-smoke/*.log` |

---

*Generated from Alienware harness artifacts after phase 2–5 and optional `rerun` (2026-05-30).*
