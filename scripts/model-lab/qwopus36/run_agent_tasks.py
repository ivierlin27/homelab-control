#!/usr/bin/env python3
"""Run real-world agent task prompts against a lab endpoint."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROMPTS = json.loads((ROOT / "agent_tasks" / "prompts.json").read_text(encoding="utf-8"))


@dataclass
class TaskRun:
    task_id: str
    model_key: str
    wall_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    response: str = ""
    rubric: dict[str, bool] = field(default_factory=dict)
    error: str = ""


def chat(base: str, api_key: str, model: str, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": kw.get("max_tokens", 2048),
    }
    if os.environ.get("AGENT_ENABLE_THINKING_FALSE", "1") == "1":
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if "tools" in kw:
        payload["tools"] = kw["tools"]
        payload["tool_choice"] = "auto"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode("utf-8"))


def score_task(task_id: str, text: str) -> dict[str, bool]:
    t = text.lower()
    if task_id == "pr_review":
        return {
            "mentions_risk": "risk" in t or "sandbox" in t,
            "actionable_items": bool(re.search(r"\d+\.|[-*]", text)),
            "format_ok": len(text) > 200,
        }
    if task_id == "finance_categorize":
        return {
            "bean_syntax": "Expenses:" in text and "Assets:" in text,
            "no_uncategorized_left": "Uncategorized" not in text or text.count("Uncategorized") <= 1,
            "reasonable_categories": any(
                x in text for x in ("Food", "Transport", "Entertainment", "Income", "Groceries")
            ),
        }
    if task_id == "incident_triage":
        return {
            "identifies_service": "vllm" in t or "strong-long" in t or "8002" in t,
            "actionable_next_steps": "systemctl" in t or "check" in t,
            "no_hallucinated_hosts": "192.168.1.45" in text or "alienware" in t,
        }
    if task_id == "structured_json":
        try:
            # strip markdown fences
            raw = text.strip()
            if "```" in raw:
                raw = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
                raw = raw.group(1) if raw else text
            obj = json.loads(raw.strip())
            return {
                "valid_json": True,
                "required_keys": all(
                    k in obj for k in ("service", "risk", "rationale", "rollback_steps")
                ),
                "risk_enum": obj.get("risk") in ("low", "med", "high"),
            }
        except json.JSONDecodeError:
            return {"valid_json": False, "required_keys": False, "risk_enum": False}
    if task_id == "tool_chain":
        return {
            "three_tool_steps": len(re.findall(r"systemctl|journal|propose|fix|active", t)) >= 2,
            "coherent_chain": len(text) > 100,
        }
    return {}


def run_one(
    base: str,
    api_key: str,
    model: str,
    model_key: str,
    task_id: str,
    spec: dict[str, Any],
) -> TaskRun:
    user = spec.get("user", "")
    if "user_file" in spec:
        user = (ROOT / "agent_tasks" / spec["user_file"]).read_text(encoding="utf-8")
    messages = [
        {"role": "system", "content": spec["system"]},
        {"role": "user", "content": user},
    ]
    t0 = time.perf_counter()
    try:
        out = chat(base, api_key, model, messages)
        ch = (out.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        text = (msg.get("content") or "") or ""
        if msg.get("tool_calls"):
            text += "\n[tool_calls]\n" + json.dumps(msg["tool_calls"], indent=2)
        usage = out.get("usage") or {}
        wall = time.perf_counter() - t0
        rubric = score_task(task_id, text)
        return TaskRun(
            task_id=task_id,
            model_key=model_key,
            wall_s=wall,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            response=text,
            rubric=rubric,
        )
    except Exception as e:  # noqa: BLE001
        return TaskRun(task_id=task_id, model_key=model_key, error=str(e))


def main() -> int:
    base = os.environ["AGENT_BASE_URL"]
    api_key = os.environ["AGENT_API_KEY"]
    model = os.environ["AGENT_MODEL"]
    model_key = os.environ["AGENT_MODEL_KEY"]
    out_dir = Path(os.environ["AGENT_OUT_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)

    task_filter = os.environ.get("AGENT_TASK_FILTER", "").strip()
    allowed = {t.strip() for t in task_filter.split(",") if t.strip()} if task_filter else None

    runs: list[TaskRun] = []
    for task_id, spec in PROMPTS.items():
        if allowed is not None and task_id not in allowed:
            continue
        print(f"== {model_key} :: {task_id}")
        runs.append(run_one(base, api_key, model, model_key, task_id, spec))

    out_path = out_dir / f"{model_key}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    summary = {
        "model_key": model_key,
        "tasks": {
            r.task_id: {
                "wall_s": round(r.wall_s, 2),
                "completion_tokens": r.completion_tokens,
                "rubric_pass": sum(r.rubric.values()),
                "rubric_total": len(r.rubric),
                "error": r.error,
            }
            for r in runs
        },
    }
    (out_dir / f"{model_key}-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
