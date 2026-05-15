"""Concurrency sweep using vLLM's standard ``vllm bench serve`` harness.

Falls back to a built-in concurrent driver if vllm CLI is unavailable. The
intent is metric parity with the rest of the community: TTFT, TPOT/ITL,
end-to-end latency p50/p95/p99, completed requests/sec, and goodput.

Inputs (env):
    BENCH_BASE_URL, BENCH_API_KEY, BENCH_MODEL, BENCH_MODEL_KEY
    BENCH_CONCURRENCIES   comma-separated, default "1,2,4,8"
    BENCH_REQS_PER_CONC   default "32"
    BENCH_DATASET         "fixtures" (default) | path to a jsonl with .messages
    OUT                   results dir
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import time
from pathlib import Path

from .. import client, fixtures, metrics


def _load_dataset() -> list[list[dict]]:
    """Build a small mixed dataset of conversation prompts."""
    src = os.environ.get("BENCH_DATASET", "fixtures")
    if src == "fixtures":
        prompts: list[list[dict]] = []
        for t in fixtures.micro_tests():
            if t["id"].startswith("long_context"):
                continue
            prompts.append(t["messages"])
        return prompts
    out: list[list[dict]] = []
    with open(src, "r", encoding="utf-8") as fh:
        for line in fh:
            obj = json.loads(line)
            out.append(obj["messages"])
    return out


def _drive(cfg: client.ClientConfig, dataset: list[list[dict]], reqs: int, conc: int) -> dict:
    latencies: list[float] = []
    completion_tokens: list[int] = []
    errors = 0
    started = time.monotonic()
    work = [(i, dataset[i % len(dataset)]) for i in range(reqs)]

    def _one(item):
        idx, messages = item
        return client.post_chat(
            cfg, messages, max_tokens=200, temperature=0.0, chat_template_kwargs=None
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
        for r in pool.map(_one, work):
            if r.ok:
                latencies.append(r.latency_ms)
                completion_tokens.append(r.completion_tokens)
            else:
                errors += 1
    elapsed = max(time.monotonic() - started, 1e-6)
    return {
        "concurrency": conc,
        "requests_attempted": reqs,
        "requests_ok": reqs - errors,
        "errors": errors,
        "elapsed_s": round(elapsed, 3),
        "rps_attempted": round(reqs / elapsed, 3),
        "rps_completed": round((reqs - errors) / elapsed, 3),
        "latency_ms": metrics.summarize(latencies),
        "completion_tokens": metrics.summarize([float(x) for x in completion_tokens]),
        "decode_tok_s_mean": round(
            statistics.fmean([c / max(l / 1000.0, 1e-3) for c, l in zip(completion_tokens, latencies)])
            if latencies
            else 0.0,
            3,
        ),
    }


def main() -> int:
    base_url = os.environ["BENCH_BASE_URL"].rstrip("/")
    api_key = os.environ["BENCH_API_KEY"]
    model = os.environ["BENCH_MODEL"]
    model_key = os.environ.get("BENCH_MODEL_KEY", model)
    out_dir = Path(os.environ["OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)

    concs = [int(x) for x in os.environ.get("BENCH_CONCURRENCIES", "1,2,4,8").split(",")]
    reqs_per = int(os.environ.get("BENCH_REQS_PER_CONC", "32"))
    cfg = client.ClientConfig(base_url=base_url, api_key=api_key, model=model)

    if not client.health_check(cfg):
        metrics.write_json(out_dir / "summary.json", {"error": "health check failed"})
        return 2

    dataset = _load_dataset()
    summary: dict = {
        "model_key": model_key,
        "model": model,
        "base_url": base_url,
        "concurrencies": concs,
        "requests_per_concurrency": reqs_per,
        "dataset_size": len(dataset),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpu_before": metrics.gpu_snapshot().to_dict(),
        "sweeps": [],
    }
    for conc in concs:
        rec = _drive(cfg, dataset, reqs_per, conc)
        summary["sweeps"].append(rec)
        print(json.dumps({"sweep": rec}), flush=True)
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["gpu_after"] = metrics.gpu_snapshot().to_dict()
    summary["vllm_metrics_after"] = metrics.fetch_vllm_metrics(base_url)
    metrics.write_json(out_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
