"""Long-context evaluator (RULER-lite).

A minimal in-house subset that mirrors the spirit of NVIDIA RULER:

  - niah_single_simple        single needle in random haystack
  - niah_multi_key            multiple needles, query asks for one
  - niah_multi_value          one key, multiple values, ask for full set
  - vt_2hop                   variable tracking (2 hops)
  - common_words_extract      retrieve all marker words

Generates self-contained prompts at the requested target token lengths and
reports per-task accuracy at each length. This is intentionally lightweight
and reproducible without external datasets; for stronger upstream parity,
replace this module with a thin wrapper around the real RULER repo.

Env:
    BENCH_BASE_URL, BENCH_API_KEY, BENCH_MODEL, BENCH_MODEL_KEY
    BENCH_RULER_LENGTHS    comma-separated targets (default "32768,65536,131072")
    BENCH_RULER_SAMPLES    samples per (task,length) (default 5)
    BENCH_RULER_TASKS      comma-separated subset of tasks
    OUT                    results dir
"""

from __future__ import annotations

import json
import os
import random
import string
import time
from pathlib import Path

from .. import client, metrics


def _approx_tokens(text: str) -> int:
    # Empirically, the Qwen3.6 tokenizer compresses our log-style ledger text
    # at ~2 chars/token (very different from the BPE-typical 4). The /2
    # divisor gets close to the real tokenizer count; pair with the haystack
    # cap below to keep prompts under max_model_len.
    return max(1, len(text) // 2)


def _haystack(target_tokens: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    components = ["forgejo", "planka", "litellm", "vllm", "backups", "agents", "router", "soak"]
    severities = ["info", "warn", "error", "debug"]
    routes = ["fast", "strong", "code", "long"]
    lines: list[str] = []
    # Empirically, the Qwen3.6 tokenizer compresses our log-style text at
    # ~2.82 chars/token (denser than the /3 approx assumes). Combined with
    # chat-template wrapping, system prompt, and completion budget, the
    # safest cap is 85% of the target. RULER signal at 110K-128K is still
    # meaningful for "does the long-context route work?" without tipping
    # over the hard max_model_len limit.
    cap = max(int(target_tokens * 0.85), target_tokens // 2)
    while sum(_approx_tokens(line) for line in lines) < cap:
        i = len(lines)
        text = (
            f"2026-05-{rng.randint(1, 28):02d}T{i % 24:02d}:{i % 60:02d}:00Z "
            f"{rng.choice(severities)} component={rng.choice(components)} "
            f"event={i} latency_ms={rng.randint(40, 700)} "
            f"trace=req-{i:06d} route={rng.choice(routes)}"
        )
        lines.append(text)
    return lines


def _rand_token(rng: random.Random, n: int = 8) -> str:
    return "".join(rng.choices(string.ascii_uppercase + string.digits, k=n))


def _niah_single_simple(target: int, seed: int) -> dict:
    rng = random.Random(seed)
    haystack = _haystack(target, seed)
    secret = _rand_token(rng)
    pos = rng.randint(int(len(haystack) * 0.3), int(len(haystack) * 0.7))
    haystack.insert(pos, f"NEEDLE_KEY={secret} <-- the magic token for today's run")
    prompt = (
        "You are a precise long-context retriever. Read the LEDGER and return the "
        "exact value associated with NEEDLE_KEY. Return only the value, no extra text.\n\n"
        "LEDGER:\n" + "\n".join(haystack)
    )
    return {"prompt": prompt, "expected": secret, "task": "niah_single_simple"}


def _niah_multi_key(target: int, seed: int) -> dict:
    rng = random.Random(seed)
    haystack = _haystack(target, seed)
    keys = [f"KEY_{i}" for i in range(4)]
    values = [_rand_token(rng) for _ in keys]
    for k, v in zip(keys, values):
        pos = rng.randint(0, len(haystack) - 1)
        haystack.insert(pos, f"NEEDLE {k}={v}")
    target_idx = rng.randint(0, len(keys) - 1)
    prompt = (
        "Read the LEDGER. The ledger contains several NEEDLE lines. Find the value "
        f"of {keys[target_idx]} and return ONLY that value.\n\nLEDGER:\n"
        + "\n".join(haystack)
    )
    return {"prompt": prompt, "expected": values[target_idx], "task": "niah_multi_key"}


def _niah_multi_value(target: int, seed: int) -> dict:
    rng = random.Random(seed)
    haystack = _haystack(target, seed)
    values = [_rand_token(rng) for _ in range(3)]
    for v in values:
        pos = rng.randint(0, len(haystack) - 1)
        haystack.insert(pos, f"NEEDLE GROUP=alpha VALUE={v}")
    prompt = (
        "Read the LEDGER. There are multiple NEEDLE lines with GROUP=alpha. Return the "
        "VALUE entries as a comma-separated list, sorted alphabetically, with no spaces.\n\n"
        "LEDGER:\n" + "\n".join(haystack)
    )
    expected = ",".join(sorted(values))
    return {"prompt": prompt, "expected": expected, "task": "niah_multi_value"}


def _vt_2hop(target: int, seed: int) -> dict:
    rng = random.Random(seed)
    haystack = _haystack(target, seed)
    a = _rand_token(rng, 6)
    b = _rand_token(rng, 6)
    final_value = _rand_token(rng, 6)
    haystack.insert(rng.randint(0, len(haystack) - 1), f"VAR {a}={b}")
    haystack.insert(rng.randint(0, len(haystack) - 1), f"VAR {b}={final_value}")
    prompt = (
        f"Read the LEDGER. Resolve variable {a} by following VAR assignments in the form "
        "'VAR X=Y'. Return only the final non-VAR value.\n\nLEDGER:\n" + "\n".join(haystack)
    )
    return {"prompt": prompt, "expected": final_value, "task": "vt_2hop"}


def _common_words(target: int, seed: int) -> dict:
    rng = random.Random(seed)
    haystack = _haystack(target, seed)
    markers = sorted({_rand_token(rng, 5) for _ in range(5)})
    for m in markers:
        haystack.insert(rng.randint(0, len(haystack) - 1), f"MARKER {m} repeated")
    prompt = (
        "Read the LEDGER. Find every line that begins with 'MARKER '. Return the marker "
        "tokens as a comma-separated list, sorted alphabetically, with no spaces.\n\n"
        "LEDGER:\n" + "\n".join(haystack)
    )
    return {"prompt": prompt, "expected": ",".join(markers), "task": "common_words_extract"}


_BUILDERS = {
    "niah_single_simple": _niah_single_simple,
    "niah_multi_key": _niah_multi_key,
    "niah_multi_value": _niah_multi_value,
    "vt_2hop": _vt_2hop,
    "common_words_extract": _common_words,
}


def _score(expected: str, content: str) -> bool:
    return expected.strip() in (content or "").strip()


def main() -> int:
    base_url = os.environ["BENCH_BASE_URL"].rstrip("/")
    api_key = os.environ["BENCH_API_KEY"]
    model = os.environ["BENCH_MODEL"]
    model_key = os.environ.get("BENCH_MODEL_KEY", model)
    out_dir = Path(os.environ["OUT"])
    out_dir.mkdir(parents=True, exist_ok=True)
    lengths = [int(x) for x in os.environ.get("BENCH_RULER_LENGTHS", "32768,65536,131072").split(",")]
    samples = int(os.environ.get("BENCH_RULER_SAMPLES", "5"))
    task_filter = os.environ.get("BENCH_RULER_TASKS")
    tasks = list(_BUILDERS.keys())
    if task_filter:
        wanted = {x.strip() for x in task_filter.split(",")}
        tasks = [t for t in tasks if t in wanted]

    cfg = client.ClientConfig(base_url=base_url, api_key=api_key, model=model)
    if not client.health_check(cfg):
        metrics.write_json(out_dir / "summary.json", {"error": "health check failed"})
        return 2

    results_path = out_dir / "results.jsonl"
    results_path.write_text("")
    summary: dict = {
        "model_key": model_key,
        "model": model,
        "lengths": lengths,
        "samples": samples,
        "tasks": tasks,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "by_task_length": {},
    }

    for task in tasks:
        for L in lengths:
            hits = 0
            attempts = 0
            latencies: list[float] = []
            for s in range(samples):
                spec = _BUILDERS[task](L, seed=hash((task, L, s)) & 0xFFFFFFFF)
                res = client.post_chat(
                    cfg,
                    [
                        {"role": "system", "content": "Follow instructions exactly. Be terse."},
                        {"role": "user", "content": spec["prompt"]},
                    ],
                    max_tokens=120,
                    temperature=0.0,
                )
                ok_match = res.ok and _score(spec["expected"], res.content)
                rec = {
                    "task": task,
                    "target_tokens": L,
                    "sample": s,
                    "ok": res.ok,
                    "match": bool(ok_match),
                    "latency_ms": res.latency_ms,
                    "prompt_tokens": res.prompt_tokens,
                    "completion_tokens": res.completion_tokens,
                    "expected": spec["expected"],
                    "answer_excerpt": (res.content or "")[:200],
                    "error": res.error,
                }
                with results_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
                attempts += 1
                if ok_match:
                    hits += 1
                if res.ok:
                    latencies.append(res.latency_ms)
                print(json.dumps({"ruler": rec}), flush=True)
            summary["by_task_length"].setdefault(task, {})[str(L)] = {
                "match_rate": round(hits / max(attempts, 1), 4),
                "attempts": attempts,
                "latency_ms": metrics.summarize(latencies),
            }
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    metrics.write_json(out_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
