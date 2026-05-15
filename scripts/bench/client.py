"""Thin OpenAI-compatible client wrapper used across runners."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request


@dataclass
class ClientConfig:
    base_url: str
    api_key: str
    model: str
    request_timeout_s: float = 600.0
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ChatResult:
    ok: bool
    latency_ms: int
    status: int = 0
    finish_reason: str | None = None
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str = ""
    error_body: str = ""

    def decode_tok_s(self) -> float:
        if not self.completion_tokens or self.latency_ms <= 0:
            return 0.0
        return round(self.completion_tokens / max(self.latency_ms / 1000.0, 0.001), 2)


def post_chat(
    cfg: ClientConfig,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.0,
    response_format: dict | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    chat_template_kwargs: dict | None = None,
    extra_body: dict | None = None,
) -> ChatResult:
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if chat_template_kwargs:
        payload["chat_template_kwargs"] = chat_template_kwargs
    if extra_body:
        payload.update(extra_body)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
    }
    headers.update(cfg.extra_headers)

    url = cfg.base_url.rstrip("/") + "/chat/completions"
    req = request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers
    )
    start = time.monotonic()
    try:
        with request.urlopen(req, timeout=cfg.request_timeout_s) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
        latency_ms = int((time.monotonic() - start) * 1000)
        parsed = json.loads(raw)
        choice = (parsed.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = parsed.get("usage") or {}
        return ChatResult(
            ok=True,
            latency_ms=latency_ms,
            status=status,
            finish_reason=choice.get("finish_reason"),
            content=msg.get("content") or "",
            reasoning=msg.get("reasoning") or "",
            tool_calls=list(msg.get("tool_calls") or []),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
        )
    except error.HTTPError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:1500]
        except Exception:
            pass
        return ChatResult(
            ok=False,
            latency_ms=latency_ms,
            status=exc.code,
            error=f"HTTPError {exc.code}",
            error_body=body,
        )
    except Exception as exc:  # noqa: BLE001 - benchmark records failures as data.
        latency_ms = int((time.monotonic() - start) * 1000)
        return ChatResult(ok=False, latency_ms=latency_ms, error=repr(exc))


def health_check(cfg: ClientConfig) -> bool:
    """Lightweight 1-token probe to confirm the endpoint responds."""
    r = post_chat(
        cfg,
        [{"role": "user", "content": "ping"}],
        max_tokens=1,
        temperature=0.0,
    )
    return r.ok
