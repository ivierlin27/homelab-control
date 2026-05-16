"""Load and validate the homelab capability registry.

The loader is intentionally dependency-light: PyYAML + the standard library.
It implements the cross-file integrity rules documented in
`config/agents/registry.schema.yaml` so manifests stay tightly coupled to the
code that interprets them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

AGENT_PRINCIPAL_RE = re.compile(r"^agent:[a-z][a-z0-9-]*$")
SCHEMA_VERSION = 1
ALLOWED_AUTONOMY_MODES = {"propose_only", "low_risk_auto", "domain_auto"}
ALLOWED_DISCORD_MODES = {"read", "write", "silent"}
ALLOWED_FORGEJO_SCOPES = {"read", "review", "author"}

# Path resolution: this module lives at apps/_shared/registry/loader.py;
# the repo root is three parents up.
_THIS_FILE = Path(__file__).resolve()
REPO_ROOT = _THIS_FILE.parents[3]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "config" / "agents" / "registry.yaml"


class RegistryError(Exception):
    """Raised when the registry or any manifest violates the schema."""


@dataclass(frozen=True)
class AgentManifest:
    """In-memory view of one agent manifest plus its source path."""

    principal: str
    path: Path
    data: dict[str, Any]

    @property
    def display_name(self) -> str:
        return str(self.data.get("display_name", self.principal))

    @property
    def domain(self) -> str:
        return str(self.data.get("domain", ""))

    def get(self, *keys: str, default: Any = None) -> Any:
        """Nested-get helper. ``manifest.get('identity', 'git_user')``."""
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur


@dataclass(frozen=True)
class Registry:
    """All loaded manifests, keyed by principal."""

    schema_version: int
    agents: dict[str, AgentManifest] = field(default_factory=dict)
    source_path: Path | None = None

    def get(self, principal: str) -> AgentManifest:
        if principal not in self.agents:
            raise RegistryError(f"unknown principal: {principal}")
        return self.agents[principal]

    def list_principals(self) -> list[str]:
        return sorted(self.agents.keys())


def load_registry(
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    *,
    repo_root: Path | None = None,
) -> Registry:
    """Load, parse, and validate the registry index and every manifest.

    Raises ``RegistryError`` on the first integrity violation found, with a
    message that names the offending file and field.
    """

    registry_path = Path(registry_path).resolve()
    if not registry_path.is_file():
        raise RegistryError(f"registry not found: {registry_path}")

    root = (repo_root or _infer_repo_root(registry_path)).resolve()

    index = _load_yaml(registry_path)
    _validate_index_shape(index, registry_path)

    agents: dict[str, AgentManifest] = {}
    for entry in index["agents"]:
        principal = entry["principal"]
        manifest_rel = entry["manifest"]
        manifest_path = (root / manifest_rel).resolve()
        if not manifest_path.is_file():
            raise RegistryError(
                f"{registry_path}: manifest not found for {principal}: {manifest_rel}"
            )
        manifest_data = _load_yaml(manifest_path)
        _validate_manifest_shape(manifest_data, manifest_path)
        if manifest_data["principal"] != principal:
            raise RegistryError(
                f"{manifest_path}: principal {manifest_data['principal']!r} "
                f"does not match registry entry {principal!r}"
            )
        if principal in agents:
            raise RegistryError(
                f"{registry_path}: duplicate principal {principal!r} in index"
            )
        agents[principal] = AgentManifest(principal, manifest_path, manifest_data)

    _validate_cross_file(agents, root)
    return Registry(
        schema_version=int(index["schema_version"]),
        agents=agents,
        source_path=registry_path,
    )


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _infer_repo_root(registry_path: Path) -> Path:
    # registry_path is .../config/agents/registry.yaml -> repo root is 2 up
    return registry_path.parents[2]


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from exc


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise RegistryError(msg)


def _validate_index_shape(data: Any, path: Path) -> None:
    _require(isinstance(data, dict), f"{path}: top-level must be a mapping")
    _require("schema_version" in data, f"{path}: missing 'schema_version'")
    _require(
        data["schema_version"] == SCHEMA_VERSION,
        f"{path}: schema_version must be {SCHEMA_VERSION}, got {data['schema_version']!r}",
    )
    _require(
        isinstance(data.get("agents"), list) and data["agents"],
        f"{path}: 'agents' must be a non-empty list",
    )
    seen_paths: set[str] = set()
    for i, entry in enumerate(data["agents"]):
        loc = f"{path}: agents[{i}]"
        _require(isinstance(entry, dict), f"{loc} must be a mapping")
        principal = entry.get("principal")
        manifest = entry.get("manifest")
        _require(
            isinstance(principal, str) and AGENT_PRINCIPAL_RE.match(principal),
            f"{loc}: principal must match {AGENT_PRINCIPAL_RE.pattern}",
        )
        _require(
            isinstance(manifest, str) and manifest,
            f"{loc}: manifest must be a non-empty string",
        )
        _require(manifest not in seen_paths, f"{loc}: duplicate manifest path {manifest!r}")
        seen_paths.add(manifest)


def _validate_manifest_shape(data: Any, path: Path) -> None:
    _require(isinstance(data, dict), f"{path}: top-level must be a mapping")
    for required in ("principal", "display_name", "domain", "queue_dir"):
        _require(required in data, f"{path}: missing required key {required!r}")
    principal = data["principal"]
    _require(
        isinstance(principal, str) and AGENT_PRINCIPAL_RE.match(principal),
        f"{path}: principal must match {AGENT_PRINCIPAL_RE.pattern}",
    )
    queue_dir = data["queue_dir"]
    _require(
        isinstance(queue_dir, str)
        and (queue_dir.startswith("/") or queue_dir.startswith("~")),
        f"{path}: queue_dir must be an absolute or ~-prefixed path",
    )
    trust = data.get("trust") or {}
    if "autonomy_mode" in trust:
        _require(
            trust["autonomy_mode"] in ALLOWED_AUTONOMY_MODES,
            f"{path}: trust.autonomy_mode must be one of {sorted(ALLOWED_AUTONOMY_MODES)}",
        )
    discord = data.get("discord") or {}
    for i, ch in enumerate(discord.get("channels") or []):
        _require(isinstance(ch, dict), f"{path}: discord.channels[{i}] must be a mapping")
        mode = ch.get("mode")
        _require(
            mode in ALLOWED_DISCORD_MODES,
            f"{path}: discord.channels[{i}].mode must be one of {sorted(ALLOWED_DISCORD_MODES)}",
        )
        _require(
            "id" in ch or "name" in ch,
            f"{path}: discord.channels[{i}] must have 'id' or 'name'",
        )
    forgejo = (data.get("tool_grants") or {}).get("forgejo") or {}
    if "scope" in forgejo:
        _require(
            forgejo["scope"] in ALLOWED_FORGEJO_SCOPES,
            f"{path}: tool_grants.forgejo.scope must be one of {sorted(ALLOWED_FORGEJO_SCOPES)}",
        )


def _validate_cross_file(agents: dict[str, AgentManifest], repo_root: Path) -> None:
    seen_git_user: dict[str, str] = {}
    seen_forgejo: dict[str, str] = {}
    seen_discord: dict[str, str] = {}
    seen_secrets: dict[str, str] = {}

    for principal, manifest in agents.items():
        identity = manifest.get("identity", default={}) or {}
        for key, bucket, label in (
            ("git_user", seen_git_user, "identity.git_user"),
            ("forgejo_account", seen_forgejo, "identity.forgejo_account"),
            ("discord_bot_app_name", seen_discord, "identity.discord_bot_app_name"),
        ):
            value = identity.get(key)
            if value:
                if value in bucket:
                    raise RegistryError(
                        f"{manifest.path}: {label}={value!r} also used by "
                        f"{bucket[value]!r}"
                    )
                bucket[value] = principal
        secrets_profile = identity.get("secrets_profile")
        if secrets_profile and secrets_profile != "none":
            if secrets_profile in seen_secrets:
                raise RegistryError(
                    f"{manifest.path}: identity.secrets_profile={secrets_profile!r} "
                    f"also used by {seen_secrets[secrets_profile]!r}"
                )
            seen_secrets[secrets_profile] = principal

        # references.policy / review_policy must exist on disk if set
        refs = manifest.get("references", default={}) or {}
        for ref_key in ("policy", "review_policy"):
            ref_path = refs.get(ref_key)
            if ref_path:
                full = (repo_root / ref_path).resolve()
                if not full.is_file():
                    raise RegistryError(
                        f"{manifest.path}: references.{ref_key} not found: {ref_path}"
                    )

        # references.memory_principal must contain an entry with id == principal
        mem_ref = refs.get("memory_principal")
        if mem_ref:
            full = (repo_root / mem_ref).resolve()
            if not full.is_file():
                raise RegistryError(
                    f"{manifest.path}: references.memory_principal not found: {mem_ref}"
                )
            mem = _load_yaml(full)
            entries = (mem or {}).get("principals") or []
            ids = {e.get("id") for e in entries if isinstance(e, dict)}
            if principal not in ids:
                raise RegistryError(
                    f"{manifest.path}: principal {principal!r} not found in {mem_ref}"
                )

        # a2a.allowed_callees must all exist in the registry
        callees = manifest.get("a2a", "allowed_callees", default=[]) or []
        for callee in callees:
            if callee not in agents:
                raise RegistryError(
                    f"{manifest.path}: a2a.allowed_callees references unknown "
                    f"principal {callee!r}"
                )
            if callee == principal:
                raise RegistryError(
                    f"{manifest.path}: a2a.allowed_callees may not include self"
                )


def _expand(path_str: str) -> str:
    return os.path.expanduser(path_str)
