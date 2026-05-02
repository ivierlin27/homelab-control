"""Constrained sandbox for RLM handles.

Handles store the raw body. The root model only sees metadata returned from
:meth:`Sandbox.metadata`. Probes operate on the body via the small, audited
vocabulary defined in docs/RLM_HARNESS.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


PREFIX_CHARS = 200
PREFIX_RECORDS = 5


@dataclass
class Handle:
    """In-sandbox container for a piece of source material.

    The body is intentionally not exposed to the harness's audit serialization.
    Only the projection returned by :meth:`Sandbox.metadata` is safe to put
    anywhere near the root model's context.
    """

    id: str
    kind: str
    body: Any
    schema: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    def length(self) -> int:
        if self.kind in {"text", "transcript"}:
            return len(self.body) if isinstance(self.body, str) else 0
        if self.kind == "lines":
            return len(self.body) if isinstance(self.body, list) else 0
        if self.kind == "records":
            return len(self.body) if isinstance(self.body, list) else 0
        if self.kind == "json":
            return len(json.dumps(self.body))
        if self.kind == "pdf":
            return len(self.body) if isinstance(self.body, list) else 0
        return 0

    def prefix(self) -> str:
        if self.kind in {"text", "transcript"} and isinstance(self.body, str):
            return self.body[:PREFIX_CHARS]
        if self.kind in {"lines", "pdf"} and isinstance(self.body, list):
            return "\n".join(str(item) for item in self.body[:PREFIX_RECORDS])
        if self.kind == "records" and isinstance(self.body, list):
            return json.dumps(self.body[:PREFIX_RECORDS])[:PREFIX_CHARS]
        if self.kind == "json":
            try:
                return json.dumps(self.body)[:PREFIX_CHARS]
            except (TypeError, ValueError):
                return ""
        return ""

    def accessor_set(self) -> list[str]:
        if self.kind in {"text", "transcript"}:
            return ["head", "tail", "slice", "grep", "count", "summarize_via_subcall"]
        if self.kind in {"lines", "pdf"}:
            return ["head", "tail", "slice", "grep", "count", "summarize_via_subcall"]
        if self.kind == "records":
            return ["head", "tail", "slice", "grep", "count", "index_by", "summarize_via_subcall"]
        if self.kind == "json":
            return ["index_by", "summarize_via_subcall", "describe"]
        return ["describe"]


class Sandbox:
    """Minimal handle store with constrained probe execution."""

    def __init__(self) -> None:
        self._handles: dict[str, Handle] = {}

    def has(self, handle_id: str) -> bool:
        return handle_id in self._handles

    def get(self, handle_id: str) -> Handle:
        if handle_id not in self._handles:
            raise KeyError(f"unknown handle: {handle_id}")
        return self._handles[handle_id]

    def add(self, handle: Handle) -> Handle:
        if handle.id in self._handles:
            raise ValueError(f"duplicate handle id: {handle.id}")
        self._handles[handle.id] = handle
        return handle

    def add_text(self, handle_id: str, body: str, *, schema: str = "", provenance: dict[str, Any] | None = None) -> Handle:
        return self.add(Handle(id=handle_id, kind="text", body=body, schema=schema, provenance=provenance or {}))

    def add_lines(self, handle_id: str, lines: Iterable[str], *, schema: str = "", provenance: dict[str, Any] | None = None) -> Handle:
        return self.add(Handle(id=handle_id, kind="lines", body=list(lines), schema=schema, provenance=provenance or {}))

    def add_records(self, handle_id: str, records: Iterable[dict[str, Any]], *, schema: str = "", provenance: dict[str, Any] | None = None) -> Handle:
        return self.add(Handle(id=handle_id, kind="records", body=list(records), schema=schema, provenance=provenance or {}))

    def add_from_path(self, handle_id: str, path: Path, *, kind: str = "lines", provenance: dict[str, Any] | None = None) -> Handle:
        text = path.read_text()
        provenance = {**(provenance or {}), "source_path": str(path)}
        if kind == "text":
            return self.add_text(handle_id, text, schema="text from disk", provenance=provenance)
        if kind == "lines":
            return self.add_lines(handle_id, text.splitlines(), schema="lines from disk", provenance=provenance)
        if kind == "records":
            return self.add_records(handle_id, [json.loads(line) for line in text.splitlines() if line.strip()], schema="jsonl from disk", provenance=provenance)
        raise ValueError(f"unsupported kind for path ingestion: {kind}")

    def metadata(self, handle_id: str) -> dict[str, Any]:
        handle = self.get(handle_id)
        return {
            "id": handle.id,
            "kind": handle.kind,
            "length": handle.length(),
            "schema": handle.schema or f"{handle.kind} body",
            "prefix": handle.prefix(),
            "accessor_set": handle.accessor_set(),
            "provenance": handle.provenance,
        }

    def metadata_all(self) -> list[dict[str, Any]]:
        return [self.metadata(handle_id) for handle_id in self._handles]

    def head(self, handle_id: str, n: int) -> list[str] | str | list[Any]:
        handle = self.get(handle_id)
        n = max(0, int(n))
        if handle.kind in {"text", "transcript"} and isinstance(handle.body, str):
            return handle.body[: max(n, 1) * 80]
        if handle.kind in {"lines", "pdf"} and isinstance(handle.body, list):
            return handle.body[:n]
        if handle.kind == "records" and isinstance(handle.body, list):
            return handle.body[:n]
        raise TypeError(f"head not supported for kind={handle.kind}")

    def tail(self, handle_id: str, n: int) -> list[str] | str | list[Any]:
        handle = self.get(handle_id)
        n = max(0, int(n))
        if handle.kind in {"text", "transcript"} and isinstance(handle.body, str):
            return handle.body[-(max(n, 1) * 80):]
        if handle.kind in {"lines", "pdf"} and isinstance(handle.body, list):
            return handle.body[-n:] if n else []
        if handle.kind == "records" and isinstance(handle.body, list):
            return handle.body[-n:] if n else []
        raise TypeError(f"tail not supported for kind={handle.kind}")

    def slice(self, handle_id: str, start: int, end: int) -> list[Any] | str:
        handle = self.get(handle_id)
        start = max(0, int(start))
        end = max(start, int(end))
        if handle.kind in {"text", "transcript"} and isinstance(handle.body, str):
            return handle.body[start:end]
        if handle.kind in {"lines", "pdf"} and isinstance(handle.body, list):
            return handle.body[start:end]
        if handle.kind == "records" and isinstance(handle.body, list):
            return handle.body[start:end]
        raise TypeError(f"slice not supported for kind={handle.kind}")

    def grep(self, handle_id: str, pattern: str, *, max_matches: int = 25) -> list[dict[str, Any]]:
        handle = self.get(handle_id)
        compiled = re.compile(pattern, re.IGNORECASE)
        results: list[dict[str, Any]] = []
        if handle.kind in {"lines", "pdf"} and isinstance(handle.body, list):
            for index, line in enumerate(handle.body):
                if compiled.search(str(line)):
                    results.append({"line": index, "snippet": str(line)[:200]})
                    if len(results) >= max_matches:
                        break
            return results
        if handle.kind in {"text", "transcript"} and isinstance(handle.body, str):
            for match in compiled.finditer(handle.body):
                start = max(0, match.start() - 40)
                end = min(len(handle.body), match.end() + 40)
                results.append({"offset": match.start(), "snippet": handle.body[start:end]})
                if len(results) >= max_matches:
                    break
            return results
        if handle.kind == "records" and isinstance(handle.body, list):
            for index, record in enumerate(handle.body):
                if compiled.search(json.dumps(record, default=str)):
                    results.append({"record": index, "snippet": json.dumps(record, default=str)[:200]})
                    if len(results) >= max_matches:
                        break
            return results
        raise TypeError(f"grep not supported for kind={handle.kind}")

    def count(self, handle_id: str, pattern: str) -> int:
        handle = self.get(handle_id)
        compiled = re.compile(pattern, re.IGNORECASE)
        if handle.kind in {"lines", "pdf"} and isinstance(handle.body, list):
            return sum(1 for line in handle.body if compiled.search(str(line)))
        if handle.kind in {"text", "transcript"} and isinstance(handle.body, str):
            return len(compiled.findall(handle.body))
        if handle.kind == "records" and isinstance(handle.body, list):
            return sum(1 for record in handle.body if compiled.search(json.dumps(record, default=str)))
        raise TypeError(f"count not supported for kind={handle.kind}")

    def index_by(self, handle_id: str, key: str) -> dict[str, list[int]]:
        handle = self.get(handle_id)
        if handle.kind != "records" or not isinstance(handle.body, list):
            raise TypeError(f"index_by not supported for kind={handle.kind}")
        index: dict[str, list[int]] = {}
        for position, record in enumerate(handle.body):
            value = str(record.get(key, "")) if isinstance(record, dict) else ""
            index.setdefault(value, []).append(position)
        return index

    def derive(self, source_id: str, transform: str, new_id: str, *, schema: str = "") -> Handle:
        handle = self.get(source_id)
        if handle.kind == "lines" and isinstance(handle.body, list):
            if transform.startswith("filter "):
                pattern = re.compile(transform.removeprefix("filter ").strip(), re.IGNORECASE)
                filtered = [line for line in handle.body if pattern.search(str(line))]
                return self.add_lines(
                    new_id,
                    filtered,
                    schema=schema or f"derived(filter): {transform}",
                    provenance={**handle.provenance, "derived_from": source_id, "transform": transform},
                )
        if handle.kind == "records" and isinstance(handle.body, list):
            if transform.startswith("where "):
                expr = transform.removeprefix("where ").strip()
                key, _, value = expr.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                filtered = [record for record in handle.body if isinstance(record, dict) and str(record.get(key, "")) == value]
                return self.add_records(
                    new_id,
                    filtered,
                    schema=schema or f"derived(where): {transform}",
                    provenance={**handle.provenance, "derived_from": source_id, "transform": transform},
                )
        raise ValueError(f"unsupported derive transform for kind={handle.kind}: {transform}")
