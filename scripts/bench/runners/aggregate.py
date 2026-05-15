"""Aggregate multiple summary.json files into a single comparison table.

Walks one or more directories, gathers `summary.json` files, and prints both
a JSON aggregate and a human-readable table.

Usage:
    python -m scripts.bench aggregate <root> [<root2> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _find(root: Path) -> list[Path]:
    return [p for p in root.rglob("summary.json")]


def _row(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"path": str(path), "error": repr(exc)}
    row: dict = {"path": str(path)}
    row["model_key"] = (
        data.get("model_key")
        or (data.get("meta") or {}).get("model_key")
        or data.get("model")
    )
    if "per_test" in data:
        for tid, vals in data["per_test"].items():
            lat = vals.get("latency_ms") or {}
            row[f"micro.{tid}.p50_ms"] = lat.get("p50")
            row[f"micro.{tid}.p95_ms"] = lat.get("p95")
            row[f"micro.{tid}.tok_s"] = (vals.get("decode_tok_s") or {}).get("mean")
            if "schema_pass_rate" in vals:
                row[f"micro.{tid}.schema_pass"] = vals["schema_pass_rate"]
            if "tool_call_match_rate" in vals:
                row[f"micro.{tid}.tool_match"] = vals["tool_call_match_rate"]
    if "by_task_length" in data:
        for task, lens in data["by_task_length"].items():
            for L, vals in lens.items():
                row[f"ruler.{task}.{L}.match"] = vals.get("match_rate")
    if "overall_selection_rate" in data:
        row["bfcl.selection"] = data["overall_selection_rate"]
        row["bfcl.args"] = data["overall_args_rate"]
    if "sweeps" in data:
        for s in data["sweeps"]:
            c = s["concurrency"]
            row[f"sweep.c{c}.p95_ms"] = (s.get("latency_ms") or {}).get("p95")
            row[f"sweep.c{c}.rps"] = s.get("rps_completed")
    if "buckets" in data:
        b = data["buckets"]
        if b:
            row["soak.minutes"] = len(b)
            row["soak.vram_peak_mib"] = data.get("vram_peak_mib_overall")
            row["soak.p95_drift_ms"] = data.get("p95_drift")
    return row


def main(args: list[str]) -> int:
    if not args:
        print("usage: aggregate <root> [<root2> ...]", file=sys.stderr)
        return 2
    rows: list[dict] = []
    for arg in args:
        for p in _find(Path(arg)):
            rows.append(_row(p))
    print(json.dumps({"rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
