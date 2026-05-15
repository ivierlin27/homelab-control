"""Repeated micro-benchmark with warmup + percentile reporting.

Replaces ``scripts/bench_single_vllm_once.py`` with N-run statistics, optional
schema validation, optional tool-call validation, and richer per-run records.

Output layout (under ``out_dir``):

    results.jsonl       - one record per individual run
    summary.json        - aggregate (per-test percentiles + overall)
    runs/<test_id>.jsonl - per-test stream (mirror of results.jsonl filtered)

Configurable via env vars:

    BENCH_BASE_URL          OpenAI-compatible base URL (e.g. http://host:port/v1)
    BENCH_API_KEY           bearer token
    BENCH_MODEL             model name to send in the request
    BENCH_MODEL_KEY         human-friendly tag for the result file (default: BENCH_MODEL)
    BENCH_REPEATS           total repeats per test (default: 10)
    BENCH_WARMUP            warmup runs to drop (default: 2)
    BENCH_ENABLE_THINKING_FALSE=1   sets chat_template_kwargs.enable_thinking=False
    BENCH_TESTS             comma-separated subset of test ids (default: all)
    OUT                     output directory (will be created)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .. import client, fixtures, metrics


def _load_config() -> tuple[client.ClientConfig, str, int, int, dict, set[str] | None, Path]:
    base_url = os.environ["BENCH_BASE_URL"].rstrip("/")
    api_key = os.environ["BENCH_API_KEY"]
    model = os.environ["BENCH_MODEL"]
    model_key = os.environ.get("BENCH_MODEL_KEY", model)
    repeats = int(os.environ.get("BENCH_REPEATS", "10"))
    warmup = int(os.environ.get("BENCH_WARMUP", "2"))
    template_kwargs: dict = {}
    if os.environ.get("BENCH_ENABLE_THINKING_FALSE") == "1":
        template_kwargs["enable_thinking"] = False
    subset_env = os.environ.get("BENCH_TESTS")
    subset = {s.strip() for s in subset_env.split(",")} if subset_env else None
    out_dir = Path(os.environ["OUT"])
    cfg = client.ClientConfig(base_url=base_url, api_key=api_key, model=model)
    return cfg, model_key, repeats, warmup, template_kwargs, subset, out_dir


def _validate_schema(content: str, schema: dict) -> dict[str, bool | str]:
    """Best-effort jsonschema validation without a hard dependency.

    Returns ``{validity, schema_pass, error}``.
    """
    result: dict[str, bool | str] = {"validity": False, "schema_pass": False, "error": ""}
    try:
        parsed = json.loads(content)
        result["validity"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"json: {exc}"
        return result
    try:
        import jsonschema  # type: ignore

        jsonschema.validate(parsed, schema)
        result["schema_pass"] = True
    except ImportError:
        # Fall back to a minimal check: required keys present.
        required = schema.get("required") or []
        if isinstance(parsed, dict) and all(k in parsed for k in required):
            result["schema_pass"] = True
        else:
            result["error"] = "missing-required-keys (no jsonschema lib)"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"schema: {exc}"
    return result


def _run_one(cfg: client.ClientConfig, test: dict, template_kwargs: dict) -> dict:
    res = client.post_chat(
        cfg,
        test["messages"],
        max_tokens=test["max_tokens"],
        temperature=test["temperature"],
        response_format=test.get("response_format"),
        tools=test.get("tools"),
        tool_choice=test.get("tool_choice"),
        chat_template_kwargs=template_kwargs or None,
    )
    record: dict = {
        "test_id": test["id"],
        "ok": res.ok,
        "status": res.status,
        "latency_ms": res.latency_ms,
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "total_tokens": res.total_tokens,
        "decode_tok_s": res.decode_tok_s(),
        "finish_reason": res.finish_reason,
        "content_excerpt": res.content[:900],
        "tool_call_count": len(res.tool_calls),
        "reasoning_excerpt": res.reasoning[:300],
    }
    if res.error:
        record["error"] = res.error
        record["error_body"] = res.error_body[:600]
    if "needles" in test:
        record["needle_hits"] = {n: n in res.content for n in test["needles"]}
    if "schema" in test:
        record["schema_check"] = _validate_schema(res.content, test["schema"])
    if "tool_call_expected" in test:
        names = [
            (tc.get("function") or {}).get("name") for tc in res.tool_calls
        ]
        record["tool_call_match"] = test["tool_call_expected"] in names
        record["tool_call_names"] = names
    return record


def main() -> int:
    cfg, model_key, repeats, warmup, template_kwargs, subset, out_dir = _load_config()
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    results_path = out_dir / "results.jsonl"
    summary_path = out_dir / "summary.json"

    tests = fixtures.micro_tests()
    if subset:
        tests = [t for t in tests if t["id"] in subset]

    meta = {
        "kind": "meta",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model_key": model_key,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "repeats": repeats,
        "warmup": warmup,
        "test_ids": [t["id"] for t in tests],
        "gpu_before": [g for g in metrics.gpu_snapshot().to_dict()],
        "vllm_metrics_before": metrics.fetch_vllm_metrics(cfg.base_url),
    }
    with results_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, sort_keys=True) + "\n")

    if not client.health_check(cfg):
        meta_err = {"kind": "fatal", "error": "endpoint health check failed"}
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(meta_err) + "\n")
        metrics.write_json(summary_path, {"meta": meta, "fatal": meta_err})
        return 2

    per_test: dict[str, list[dict]] = {}
    for test in tests:
        per_test[test["id"]] = []
        per_test_path = runs_dir / f"{test['id']}.jsonl"
        with per_test_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "test_meta", "test_id": test["id"]}) + "\n")
        for i in range(repeats):
            rec = _run_one(cfg, test, template_kwargs)
            rec["repeat"] = i
            rec["is_warmup"] = i < warmup
            per_test[test["id"]].append(rec)
            line = json.dumps(rec, sort_keys=True)
            print(line, flush=True)
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            with per_test_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    summary: dict = {
        "meta": meta,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpu_after": metrics.gpu_snapshot().to_dict(),
        "vllm_metrics_after": metrics.fetch_vllm_metrics(cfg.base_url),
        "per_test": {},
    }

    for test_id, records in per_test.items():
        bench = [r for r in records if not r["is_warmup"] and r.get("ok")]
        latencies = [r["latency_ms"] for r in bench]
        decodes = [r["decode_tok_s"] for r in bench if r.get("decode_tok_s")]
        comp = [r["completion_tokens"] for r in bench]
        prompts = [r["prompt_tokens"] for r in bench]
        ok_count = len(bench)
        attempt = len([r for r in records if not r["is_warmup"]])
        per: dict = {
            "ok_count": ok_count,
            "attempts": attempt,
            "ok_rate": round(ok_count / attempt, 4) if attempt else 0.0,
            "latency_ms": metrics.summarize(latencies),
            "decode_tok_s": metrics.summarize(decodes),
            "completion_tokens": metrics.summarize([float(x) for x in comp]),
            "prompt_tokens": metrics.summarize([float(x) for x in prompts]),
        }
        if any("schema_check" in r for r in records):
            valid = [1.0 if r.get("schema_check", {}).get("validity") else 0.0 for r in bench]
            sp = [1.0 if r.get("schema_check", {}).get("schema_pass") else 0.0 for r in bench]
            per["json_validity_rate"] = round(sum(valid) / max(len(valid), 1), 4)
            per["schema_pass_rate"] = round(sum(sp) / max(len(sp), 1), 4)
        if any("tool_call_match" in r for r in records):
            tcm = [1.0 if r.get("tool_call_match") else 0.0 for r in bench]
            per["tool_call_match_rate"] = round(sum(tcm) / max(len(tcm), 1), 4)
        if any("needle_hits" in r for r in records):
            needle_keys = sorted({k for r in bench for k in r.get("needle_hits", {})})
            for nk in needle_keys:
                hits = [1.0 if r.get("needle_hits", {}).get(nk) else 0.0 for r in bench]
                per[f"needle_hit_rate.{nk}"] = round(sum(hits) / max(len(hits), 1), 4)
        summary["per_test"][test_id] = per

    metrics.write_json(summary_path, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
