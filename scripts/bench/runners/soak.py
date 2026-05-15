"""Steady-load soak test.

Runs a constant-concurrency loop for N minutes. Records:
    - VRAM peak per GPU each minute
    - p95 latency per minute window
    - empty-completion rate per minute
    - tool/JSON success rate per minute (if those tests are in the fixture mix)

Designed to catch leak-like growth, scheduler stalls, and silent output
degeneration that single-shot benchmarks miss.

Env:
    BENCH_BASE_URL, BENCH_API_KEY, BENCH_MODEL, BENCH_MODEL_KEY
    BENCH_SOAK_MINUTES  default 60
    BENCH_SOAK_CONC     default 4
    OUT                 results dir
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from pathlib import Path

from .. import client, fixtures, metrics


def _build_workload() -> list[dict]:
    return [t for t in fixtures.micro_tests() if not t["id"].startswith("long_context")]


def main() -> int:
    base_url = os.environ["BENCH_BASE_URL"].rstrip("/")
    api_key = os.environ["BENCH_API_KEY"]
    model = os.environ["BENCH_MODEL"]
    model_key = os.environ.get("BENCH_MODEL_KEY", model)
    minutes = float(os.environ.get("BENCH_SOAK_MINUTES", "60"))
    conc = int(os.environ.get("BENCH_SOAK_CONC", "4"))
    out_dir = Path(os.environ["OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = client.ClientConfig(base_url=base_url, api_key=api_key, model=model)
    if not client.health_check(cfg):
        metrics.write_json(out_dir / "summary.json", {"error": "health check failed"})
        return 2

    tests = _build_workload()
    deadline = time.monotonic() + minutes * 60
    minute_buckets: list[dict] = []
    log_path = out_dir / "soak.jsonl"
    log_path.write_text("")

    submitted = 0
    next_minute = time.monotonic() + 60.0

    bucket: dict = {
        "minute_index": 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": 0,
        "errors": 0,
        "empty": 0,
        "latencies_ms": [],
        "tool_match": 0,
        "tool_attempts": 0,
        "json_valid": 0,
        "json_attempts": 0,
        "vram_peak_mib": 0.0,
        "power_peak_w": 0.0,
    }

    def _flush(b: dict) -> dict:
        snap = {
            "minute_index": b["minute_index"],
            "started_at": b["started_at"],
            "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ok": b["ok"],
            "errors": b["errors"],
            "empty": b["empty"],
            "p50_ms": metrics.percentile(b["latencies_ms"], 0.5),
            "p95_ms": metrics.percentile(b["latencies_ms"], 0.95),
            "p99_ms": metrics.percentile(b["latencies_ms"], 0.99),
            "vram_peak_mib": b["vram_peak_mib"],
            "power_peak_w": b["power_peak_w"],
        }
        if b["tool_attempts"]:
            snap["tool_match_rate"] = round(b["tool_match"] / b["tool_attempts"], 4)
        if b["json_attempts"]:
            snap["json_valid_rate"] = round(b["json_valid"] / b["json_attempts"], 4)
        return snap

    def _do_test(test: dict) -> dict:
        return {
            "test_id": test["id"],
            "result": client.post_chat(
                cfg,
                test["messages"],
                max_tokens=test["max_tokens"],
                temperature=test["temperature"],
                response_format=test.get("response_format"),
                tools=test.get("tools"),
                tool_choice=test.get("tool_choice"),
            ),
            "expected_tool": test.get("tool_call_expected"),
            "is_json": "response_format" in test,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
        in_flight: set[concurrent.futures.Future] = set()

        def _seed():
            nonlocal submitted
            while len(in_flight) < conc:
                t = tests[submitted % len(tests)]
                fut = pool.submit(_do_test, t)
                in_flight.add(fut)
                submitted += 1

        _seed()
        while time.monotonic() < deadline:
            done, _ = concurrent.futures.wait(in_flight, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                in_flight.discard(fut)
                try:
                    out = fut.result()
                except Exception as exc:  # noqa: BLE001
                    bucket["errors"] += 1
                    continue
                r = out["result"]
                if r.ok:
                    bucket["ok"] += 1
                    bucket["latencies_ms"].append(r.latency_ms)
                    if not r.content and not r.tool_calls:
                        bucket["empty"] += 1
                    if out["expected_tool"]:
                        bucket["tool_attempts"] += 1
                        names = [(tc.get("function") or {}).get("name") for tc in r.tool_calls]
                        if out["expected_tool"] in names:
                            bucket["tool_match"] += 1
                    if out["is_json"]:
                        bucket["json_attempts"] += 1
                        try:
                            json.loads(r.content)
                            bucket["json_valid"] += 1
                        except Exception:
                            pass
                else:
                    bucket["errors"] += 1
            if time.monotonic() < deadline:
                _seed()

            if time.monotonic() >= next_minute:
                snap = metrics.gpu_snapshot()
                bucket["vram_peak_mib"] = max(bucket["vram_peak_mib"], snap.peak_vram_mib())
                bucket["power_peak_w"] = max(bucket["power_peak_w"], snap.peak_power_w())
                flushed = _flush(bucket)
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(flushed, sort_keys=True) + "\n")
                minute_buckets.append(flushed)
                bucket = {
                    "minute_index": flushed["minute_index"] + 1,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "ok": 0,
                    "errors": 0,
                    "empty": 0,
                    "latencies_ms": [],
                    "tool_match": 0,
                    "tool_attempts": 0,
                    "json_valid": 0,
                    "json_attempts": 0,
                    "vram_peak_mib": 0.0,
                    "power_peak_w": 0.0,
                }
                next_minute += 60.0

        for fut in in_flight:
            fut.cancel()

    summary = {
        "model_key": model_key,
        "model": model,
        "minutes_target": minutes,
        "concurrency": conc,
        "buckets": minute_buckets,
        "vram_peak_mib_overall": max((b["vram_peak_mib"] for b in minute_buckets), default=0.0),
        "p95_drift": (
            metrics.percentile([b["p95_ms"] for b in minute_buckets[-3:]], 0.5)
            - metrics.percentile([b["p95_ms"] for b in minute_buckets[:3]], 0.5)
            if len(minute_buckets) >= 6
            else None
        ),
    }
    metrics.write_json(out_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
