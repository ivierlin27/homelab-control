# Homelab benchmark harness v2

Statistically rigorous, OpenAI-compatible benchmark suite for the dual-RTX-3090
Alienware. Replaces the one-shot `bench_single_vllm_once.py` with N-run
percentiles, schema-aware JSON validation, BFCL-style tool-call scoring, a
RULER-lite long-context evaluator, an `lm-eval-harness` wrapper for code
correctness, and a steady-load soak runner.

## Quick start

```bash
export BENCH_BASE_URL=http://192.168.1.45:8000/v1
export BENCH_API_KEY=...
export BENCH_MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ
export BENCH_MODEL_KEY=q25-14b-awq
export OUT=/mnt/data/bench-artifacts/2026-MM-DD-q25-14b-awq

python -m scripts.bench micro          # repeated micro suite (warmup + N runs)
python -m scripts.bench serve-sweep    # concurrency sweep
python -m scripts.bench ruler          # long-context (RULER-lite)
python -m scripts.bench bfcl           # tool calling (BFCL-lite)
python -m scripts.bench code           # HumanEval/MBPP via lm-eval (needs lm-eval installed)
python -m scripts.bench soak           # multi-minute steady-state stability
python -m scripts.bench aggregate /mnt/data/bench-artifacts/  # collect all
```

## Subcommands

| cmd            | what it does                                                                                  |
| -------------- | --------------------------------------------------------------------------------------------- |
| `micro`        | The classic short tests with N=10 repeats, warmup, p50/p95/p99 latency, decode tok/s + CV.    |
| `serve-sweep`  | Concurrency sweep [1,2,4,8] with p95 latency and completed RPS per level.                     |
| `ruler`        | RULER-lite at 32K/64K/128K: NIAH single, multi-key, multi-value, var-tracking, common-words.  |
| `bfcl`         | 5-case BFCL-lite: simple, parallel, args-correctness, no-tool. Repeats per case, mean +/- sd. |
| `code`         | HumanEval + MBPP-Plus via `lm-eval --model local-completions`.                                |
| `soak`         | 60-minute (configurable) steady-load with per-minute VRAM peak, p95, empty-rate, drift.       |
| `aggregate`    | Walk one or more roots, gather `summary.json` files, emit a flat row-per-run JSON.            |

## Result layout

Each runner writes to the directory in `$OUT`:

```
$OUT/
  summary.json     overall aggregate for this run
  results.jsonl    one record per individual call
  runs/<test>.jsonl  (micro only) per-test stream
  soak.jsonl       (soak only) per-minute bucket records
  lm-eval.{stdout,stderr}.log   (code only) lm-eval CLI output
```

## Hardware metrics

`metrics.gpu_snapshot()` and `metrics.fetch_vllm_metrics()` are called at the
start and end of every runner. Snapshots include per-GPU VRAM used, power,
SM/mem clock, temperature; vLLM metrics include KV-cache usage, prefix-cache
hit rate, spec-decode acceptance counts, and request queue depth (when the
endpoint exposes `/metrics`).

## Stability notes

- The harness uses Python's stdlib `urllib` and `concurrent.futures`. No third
  party deps required for `micro`, `serve-sweep`, `ruler`, `bfcl`, `soak`. The
  `code` runner needs `lm-eval[api]`. JSON-schema validation in `micro` will
  fall back to required-key check if `jsonschema` is not installed.
- For very long contexts (>=64K), HTTP client buffering can dominate measured
  latency on the test host. Cross-validate one config with `vllm bench serve`
  or NVIDIA GenAI-Perf before drawing strong conclusions.
- BFCL-lite is intentionally small for speed; pair it with one full BFCL run
  per release-gate, not per tuning iteration.
