"""Statistical helpers and hardware-metric snapshots."""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from typing import Iterable


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    if len(values) == 1:
        v = float(values[0])
        return {
            "n": 1,
            "mean": v,
            "stdev": 0.0,
            "cv": 0.0,
            "p50": v,
            "p95": v,
            "p99": v,
            "min": v,
            "max": v,
        }
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    return {
        "n": len(values),
        "mean": round(mean, 4),
        "stdev": round(stdev, 4),
        "cv": round(stdev / mean, 4) if mean else 0.0,
        "p50": round(percentile(values, 0.50), 4),
        "p95": round(percentile(values, 0.95), 4),
        "p99": round(percentile(values, 0.99), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


@dataclass
class GpuSnapshot:
    raw: list[dict[str, float]]

    def peak_vram_mib(self) -> float:
        return max((g.get("memory.used_mib", 0.0) for g in self.raw), default=0.0)

    def peak_power_w(self) -> float:
        return max((g.get("power.draw_w", 0.0) for g in self.raw), default=0.0)

    def to_dict(self) -> list[dict[str, float]]:
        return self.raw


def gpu_snapshot() -> GpuSnapshot:
    if not shutil.which("nvidia-smi"):
        return GpuSnapshot(raw=[])
    out = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,power.draw,clocks.sm,clocks.mem,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip()
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        rows.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "memory.used_mib": float(parts[2]),
                "memory.total_mib": float(parts[3]),
                "util.gpu_pct": float(parts[4]),
                "power.draw_w": float(parts[5]),
                "clock.sm_mhz": float(parts[6]),
                "clock.mem_mhz": float(parts[7]),
                "temp.gpu_c": float(parts[8]),
            }
        )
    return GpuSnapshot(raw=rows)


def diff_gpu(before: GpuSnapshot, after: GpuSnapshot) -> dict[str, list[dict[str, float]]]:
    return {"before": before.to_dict(), "after": after.to_dict()}


def fetch_vllm_metrics(base_url: str, timeout: float = 5.0) -> dict[str, float]:
    """Pull a small set of vLLM Prometheus metrics if available.

    Returns an empty dict if /metrics is not reachable.
    """
    import urllib.error
    import urllib.request

    metrics_url = base_url.rstrip("/") + "/metrics"
    keys_of_interest = (
        "vllm:gpu_cache_usage_perc",
        "vllm:cpu_cache_usage_perc",
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:num_preemptions_total",
        "vllm:prefix_cache_hits_total",
        "vllm:prefix_cache_queries_total",
        "vllm:spec_decode_num_accepted_tokens_total",
        "vllm:spec_decode_num_emitted_tokens_total",
        "vllm:spec_decode_num_draft_tokens_total",
    )
    out: dict[str, float] = {}
    try:
        req = urllib.request.Request(metrics_url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return out
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        for key in keys_of_interest:
            if line.startswith(key):
                try:
                    val = float(line.rsplit(" ", 1)[-1])
                except ValueError:
                    continue
                out.setdefault(key, val)
                break
    return out


def write_jsonl(path, records: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def write_json(path, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
