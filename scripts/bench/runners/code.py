"""Code-correctness sanity (HumanEval / MBPP-Plus / CRUXEval).

Thin wrapper around lm-evaluation-harness's ``local-completions`` model
backend. We delegate execution rather than reimplement the harness so the
scores are comparable to community reports.

Requirements (one-time on the box):
    pip install --user lm-eval[api]

Env:
    BENCH_BASE_URL, BENCH_API_KEY, BENCH_MODEL, BENCH_MODEL_KEY
    BENCH_CODE_TASKS    comma-separated lm-eval task names
                        default: "humaneval,mbpp_plus"
    BENCH_CODE_LIMIT    optional --limit (e.g. "20" for fast smoke)
    OUT                 results dir
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def main() -> int:
    base_url = os.environ["BENCH_BASE_URL"].rstrip("/")
    api_key = os.environ["BENCH_API_KEY"]
    model = os.environ["BENCH_MODEL"]
    model_key = os.environ.get("BENCH_MODEL_KEY", model)
    out_dir = Path(os.environ["OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)

    if not shutil.which("lm-eval") and not shutil.which("lm_eval"):
        msg = (
            "lm-eval CLI not found. Install with: pip install --user 'lm-eval[api]'"
            " or run inside a venv."
        )
        (out_dir / "summary.json").write_text(json.dumps({"error": msg}, indent=2))
        return 2

    tasks = os.environ.get("BENCH_CODE_TASKS", "humaneval,mbpp_plus")
    limit = os.environ.get("BENCH_CODE_LIMIT")

    cmd = [
        shutil.which("lm-eval") or shutil.which("lm_eval"),
        "--model",
        "local-completions",
        "--model_args",
        f"model={model},base_url={base_url}/completions,api_key={api_key},num_concurrent=4",
        "--tasks",
        tasks,
        "--output_path",
        str(out_dir),
        "--apply_chat_template",
    ]
    if limit:
        cmd += ["--limit", limit]

    started = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        (out_dir / "summary.json").write_text(json.dumps({"error": repr(exc)}, indent=2))
        return 2
    elapsed = time.time() - started

    (out_dir / "lm-eval.stdout.log").write_text(r.stdout)
    (out_dir / "lm-eval.stderr.log").write_text(r.stderr)

    summary = {
        "model_key": model_key,
        "model": model,
        "tasks": tasks,
        "limit": limit,
        "elapsed_s": round(elapsed, 1),
        "exit_code": r.returncode,
        "cmd": cmd,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
