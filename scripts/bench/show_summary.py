#!/usr/bin/env python3
"""Pretty-print a bench summary.json."""
import json
import sys
from pathlib import Path


def show_micro(d: dict) -> None:
    print(f"=== micro: {d['meta'].get('model_key')} ===")
    print(f"repeats={d['meta']['repeats']} warmup={d['meta']['warmup']}")
    for tid, vals in d.get("per_test", {}).items():
        lat = vals.get("latency_ms") or {}
        tok = vals.get("decode_tok_s") or {}
        extras = []
        for k in ("json_validity_rate", "schema_pass_rate", "tool_call_match_rate"):
            if k in vals:
                extras.append(f"{k}={vals[k]}")
        for k, v in vals.items():
            if k.startswith("needle_hit_rate"):
                extras.append(f"{k}={v}")
        if lat.get("p50") is None:
            print(
                f"  {tid:30s}  no successful runs  ok_rate={vals.get('ok_rate')}"
            )
            continue
        print(
            f"  {tid:30s}  p50={lat['p50']:>6.0f}ms  p95={lat['p95']:>6.0f}ms"
            f"  tok/s={tok.get('mean', 0):>6.1f} cv={tok.get('cv', 0):.3f}"
            f"  ok={vals['ok_rate']}  " + " ".join(extras)
        )
    if "gpu_after" in d:
        peaks = [g["memory.used_mib"] for g in d["gpu_after"]]
        print(f"  vram per GPU after: {[round(p) for p in peaks]} MiB")
    if d.get("vllm_metrics_after"):
        print("  vllm metrics:", json.dumps(d["vllm_metrics_after"]))


def show_ruler(d: dict) -> None:
    print(f"=== ruler: {d.get('model_key')} ===")
    for task, lens in d.get("by_task_length", {}).items():
        for L, vals in lens.items():
            lat = vals.get("latency_ms") or {}
            print(
                f"  {task:24s}  L={L:>7s}  match={vals['match_rate']*100:5.1f}%"
                f"  p50={lat.get('p50','-')}  attempts={vals['attempts']}"
            )


def show_bfcl(d: dict) -> None:
    print(f"=== bfcl: {d.get('model_key')} ===")
    print(f"  overall selection={d.get('overall_selection_rate')}  args={d.get('overall_args_rate')}")
    for case_id, vals in d.get("per_case", {}).items():
        print(f"  {case_id:30s}  sel={vals['selection_rate']}  args={vals['args_rate']}")


def show_sweep(d: dict) -> None:
    print(f"=== serve-sweep: {d.get('model_key')} ===")
    for s in d.get("sweeps", []):
        lat = s["latency_ms"]
        print(
            f"  c={s['concurrency']:>2d}  rps={s['rps_completed']:>5.2f}"
            f"  p50={lat['p50']:>6.0f}  p95={lat['p95']:>6.0f}  err={s['errors']}"
            f"  tok/s={s['decode_tok_s_mean']:>6.1f}"
        )


def show(path: Path) -> None:
    d = json.loads(path.read_text())
    name = path.parent.name
    if "per_test" in d or "per_test" in d.get("meta", {}):
        show_micro(d)
    elif "by_task_length" in d:
        show_ruler(d)
    elif "per_case" in d:
        show_bfcl(d)
    elif "sweeps" in d:
        show_sweep(d)
    else:
        print(f"=== {name} ===")
        for k, v in d.items():
            if isinstance(v, (int, float, str, bool)) or v is None:
                print(f"  {k}: {v}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        show(Path(arg))
        print()
