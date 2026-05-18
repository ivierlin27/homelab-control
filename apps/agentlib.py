#!/usr/bin/env python3
"""Shared helpers for homelab queue workers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

# Make ``apps._shared.*`` resolvable from inside the running agents.
# The agents append ``<repo>/apps`` to sys.path so ``from agentlib import ...``
# works; for the new registry/skills/audit primitives we also need the repo
# root on sys.path so the ``apps._shared.<pkg>`` import path resolves.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def slugify(value: str, *, default: str = "task") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or default


def repo_name_from_path(path: Path) -> str:
    return path.resolve().name


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2) + "\n")


def parse_pr_url(pr_url: str) -> dict[str, Any]:
    parsed = parse.urlparse(pr_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[2] != "pulls":
        raise ValueError(f"unsupported PR URL: {pr_url}")
    owner, repo, _, number = parts[:4]
    return {
        "base_url": f"{parsed.scheme}://{parsed.netloc}",
        "owner": owner,
        "repo": repo,
        "number": int(number),
    }


def _api_url(base_url: str, api_path: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1/{api_path.lstrip('/')}"


def forgejo_request(
    base_url: str,
    api_path: str,
    *,
    token: str = "",
    method: str = "GET",
    payload: dict[str, Any] | list[Any] | None = None,
    timeout: int = 30,
) -> Any:
    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if token:
        headers["Authorization"] = f"token {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        _api_url(base_url, api_path),
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"forgejo api {method} {api_path} failed: http {exc.code}: {response_body}") from exc


def extract_links(text: str) -> list[str]:
    return re.findall(r"https?://\S+", text)


# ---------------------------------------------------------------------------
# Registry-aware agent boot (Phase 0.5 / 0.8 integration).
#
# `boot_principal()` is the single entry point every agent's ``main()`` calls
# at startup. It loads the capability registry, fetches this agent's manifest,
# filters the configured skills, opens the agent's audit ledger, and writes a
# tamper-evident ``boot`` event with the manifest hash, the skill ids, and the
# tool grants snapshot. The returned ``BootContext`` is the runtime view of
# "what this agent is allowed to do right now".
#
# A boot failure (missing principal, malformed manifest, ungranted skill,
# etc.) is fatal under ``AGENT_REGISTRY_ENFORCE=1`` and a soft-fail (stderr
# warning + return ``None``) otherwise. The default is soft-fail so we can
# roll out the wiring without breaking live agents mid-deploy; flip
# ``AGENT_REGISTRY_ENFORCE=1`` in each agent's systemd unit once the boot
# event shows up cleanly in its trust-ledger.
# ---------------------------------------------------------------------------


class BootError(RuntimeError):
    """Raised when ``boot_principal`` cannot safely return a BootContext."""


@dataclass(frozen=True)
class BootContext:
    """Snapshot of what the agent loaded from the registry at startup."""

    principal: str
    manifest: Any            # apps._shared.registry.AgentManifest
    skills: tuple            # tuple[apps._shared.skills.Skill, ...]
    audit: Any               # apps._shared.audit.AuditLog
    state_dir: Path
    manifest_sha256: str
    enforced: bool

    def skill_ids(self) -> list[str]:
        return [s.id for s in self.skills]

    def has_tool(self, tool: str) -> bool:
        return tool in (self.manifest.get("tools", default=[]) or [])


def _boot_failure(msg: str, *, enforce: bool) -> None:
    if enforce:
        raise BootError(msg)
    print(f"warning: agent boot soft-fail: {msg}", file=sys.stderr)
    return None


def boot_principal(
    default_principal: str | None = None,
    *,
    principal: str | None = None,
    registry_path: Path | str | None = None,
) -> "BootContext | None":
    """Validate this agent against the capability registry and audit the boot.

    Argument resolution order for the principal:
      1. ``principal=...`` keyword argument (tests pass this in)
      2. ``AGENT_PRINCIPAL`` environment variable (production)
      3. ``default_principal`` positional (the agent's hardcoded constant)

    Returns a frozen :class:`BootContext` on success. Under
    ``AGENT_REGISTRY_ENFORCE=1`` raises :class:`BootError` on any problem;
    otherwise prints a warning and returns ``None`` so the agent can keep
    running with its legacy hardcoded behaviour during the migration.
    """
    enforce_raw = os.environ.get("AGENT_REGISTRY_ENFORCE", "0").strip().lower()
    enforce = enforce_raw in {"1", "true", "yes", "on"}

    name = (
        principal
        or os.environ.get("AGENT_PRINCIPAL")
        or default_principal
    )
    if not name:
        return _boot_failure(
            "no principal: pass principal= or set AGENT_PRINCIPAL env",
            enforce=enforce,
        )
    if not name.startswith("agent:"):
        return _boot_failure(
            f"principal must start with 'agent:', got {name!r}",
            enforce=enforce,
        )

    # Imports are inside the function so that agents which never call
    # `boot_principal()` do not pay the cost of importing yaml + the four
    # primitive modules. Also keeps unit tests of unrelated helpers light.
    try:
        from apps._shared.audit import AuditLog
        from apps._shared.registry import RegistryError, load_registry
        from apps._shared.skills import SkillError, skills_for_agent
    except ImportError as exc:
        return _boot_failure(
            f"boot dependencies missing (registry/skills/audit): {exc}",
            enforce=enforce,
        )

    try:
        registry = (
            load_registry(registry_path) if registry_path else load_registry()
        )
    except RegistryError as exc:
        return _boot_failure(f"registry load failed: {exc}", enforce=enforce)

    try:
        manifest = registry.get(name)
    except RegistryError:
        return _boot_failure(
            f"principal {name!r} not in registry; "
            f"add an entry in config/agents/registry.yaml",
            enforce=enforce,
        )

    try:
        skills_list = skills_for_agent(manifest)
    except SkillError as exc:
        return _boot_failure(
            f"skill resolution failed for {name}: {exc}",
            enforce=enforce,
        )

    queue_dir_raw = manifest.get("queue_dir")
    if not queue_dir_raw:
        return _boot_failure(
            f"{name}: manifest.queue_dir is required",
            enforce=enforce,
        )

    state_dir = Path(os.path.expanduser(str(queue_dir_raw))).resolve()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _boot_failure(
            f"cannot create state dir {state_dir}: {exc}",
            enforce=enforce,
        )

    audit_path = state_dir / "trust-ledger.jsonl"
    audit = AuditLog(str(audit_path))

    try:
        manifest_sha256 = hashlib.sha256(manifest.path.read_bytes()).hexdigest()
    except OSError as exc:
        return _boot_failure(
            f"cannot read manifest at {manifest.path}: {exc}",
            enforce=enforce,
        )

    audit.append({
        "principal": name,
        "event": "boot",
        "manifest_path": str(manifest.path),
        "manifest_sha256": manifest_sha256,
        "skills_loaded": [s.id for s in skills_list],
        "tools_granted": list(manifest.get("tools", default=[]) or []),
        "autonomy_mode": manifest.get("trust", "autonomy_mode", default="propose_only"),
        "registry_path": str(registry.source_path) if registry.source_path else "",
        "enforced": enforce,
    })

    return BootContext(
        principal=name,
        manifest=manifest,
        skills=tuple(skills_list),
        audit=audit,
        state_dir=state_dir,
        manifest_sha256=manifest_sha256,
        enforced=enforce,
    )
