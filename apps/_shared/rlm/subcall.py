"""Sub-call invoker for the RLM harness.

Sub-calls speak a strict JSON schema and run against the existing LiteLLM
gateway via the symbolic-intent shim. Internal CoT is discarded; only the
structured object survives. A null transport is provided so the harness can
run in tests and on hosts without gateway access.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import request


SUBCALL_SCHEMA_KEYS = ("summary", "citations", "confidence", "open_questions")


class SubCallSchemaError(ValueError):
    """Raised when a sub-call response does not match the expected schema."""


@dataclass
class SubCallResult:
    summary: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"
    open_questions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    route: str = ""
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "citations": self.citations,
            "confidence": self.confidence,
            "open_questions": self.open_questions,
        }


Transport = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def _approx_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _parse_schema(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SubCallSchemaError(f"sub-call response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SubCallSchemaError("sub-call response is not a JSON object")
    summary = payload.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        raise SubCallSchemaError("sub-call response missing non-empty 'summary'")
    citations = payload.get("citations", [])
    if not isinstance(citations, list):
        raise SubCallSchemaError("sub-call response 'citations' must be a list")
    confidence = str(payload.get("confidence", "low")).lower()
    if confidence not in {"low", "medium", "high"}:
        raise SubCallSchemaError(f"sub-call response has invalid confidence: {confidence}")
    open_questions = payload.get("open_questions", [])
    if not isinstance(open_questions, list):
        raise SubCallSchemaError("sub-call response 'open_questions' must be a list")
    return {
        "summary": summary.strip(),
        "citations": citations,
        "confidence": confidence,
        "open_questions": [str(item) for item in open_questions],
    }


class SubCallInvoker:
    """Dispatches sub-calls through the LiteLLM gateway with a JSON contract."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
        intent_to_model: dict[str, str] | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("MODEL_GATEWAY_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("MODEL_GATEWAY_API_KEY", "")
        self.transport = transport
        self.intent_to_model = intent_to_model or {
            "summarize": os.environ.get("RLM_FAST_MODEL", "homelab-fast"),
            "classify": os.environ.get("RLM_FAST_MODEL", "homelab-fast"),
            "code": os.environ.get("RLM_STRONG_MODEL", "homelab-strong"),
            "plan": os.environ.get("RLM_STRONG_MODEL", "homelab-strong"),
        }

    def model_for_intent(self, intent: str) -> str:
        return self.intent_to_model.get(intent, os.environ.get("RLM_FAST_MODEL", "homelab-fast"))

    def call(
        self,
        *,
        intent: str,
        sub_prompt: str,
        context: dict[str, Any],
    ) -> SubCallResult:
        model = self.model_for_intent(intent)
        payload = self._build_payload(model=model, sub_prompt=sub_prompt, context=context)
        start = time.monotonic()
        if self.transport is not None:
            response = self.transport(intent, model, payload)
        else:
            response = self._http_post(payload)
        latency_ms = int((time.monotonic() - start) * 1000)
        text = self._extract_text(response)
        parsed = _parse_schema(text)
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        return SubCallResult(
            **parsed,
            raw=response if isinstance(response, dict) else {},
            tokens_in=int(usage.get("prompt_tokens", _approx_token_count(json.dumps(payload, default=str)))),
            tokens_out=int(usage.get("completion_tokens", _approx_token_count(text))),
            latency_ms=latency_ms,
            route=intent,
            model=model,
        )

    def _build_payload(self, *, model: str, sub_prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        system = (
            "You are a sub-RLM worker. Respond with a single JSON object matching keys: "
            "summary (string), citations (array of {handle, range}), confidence (low|medium|high), "
            "open_questions (array of strings). Do not include any prose outside the JSON."
        )
        user = json.dumps({"sub_prompt": sub_prompt, "context": context}, default=str)
        return {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("MODEL_GATEWAY_BASE_URL is not configured for sub-calls")
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # Phase 0.6: surface the calling agent so the gateway's cost/latency
        # callback can attribute the call. Empty/unset → callback records
        # "unknown" rather than guessing.
        principal = os.environ.get("AGENT_PRINCIPAL", "").strip()
        if principal:
            headers["x-agent-principal"] = principal
        req = request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=int(os.environ.get("RLM_SUBCALL_TIMEOUT", "120"))) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    @staticmethod
    def _extract_text(response: Any) -> str:
        if not isinstance(response, dict):
            return str(response)
        choices = response.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content
        return json.dumps(response, default=str)
