"""Persistent state for identity issuance.

The state file is a small JSON document that records what's been issued,
when, and how to verify it. It is intentionally human-readable and
git-ignorable (it lives under ``$XDG_STATE_HOME``, never in the repo).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class IdentityStateError(Exception):
    """Raised when state-file IO or schema is invalid."""


class ComponentStatus(str, Enum):
    """Lifecycle states for one identity component."""

    NOT_REQUIRED = "not_required"  # the manifest doesn't ask for this component
    PENDING = "pending"            # required, not yet started
    ISSUED = "issued"              # automated step completed
    OPERATOR_TODO = "operator_todo"  # waiting on a human-mediated step
    REVOKED = "revoked"            # explicitly revoked


# Canonical component names. Keep these stable: the state file uses them as keys.
class Component(str, Enum):
    SSH_KEY = "ssh_key"
    SANDBOX_IMAGE = "sandbox_image"
    FORGEJO_ACCOUNT = "forgejo_account"
    FORGEJO_PAT = "forgejo_pat"
    DISCORD_BOT = "discord_bot"
    INFISICAL_TOKEN = "infisical_token"


ALL_COMPONENTS: tuple[Component, ...] = tuple(Component)


@dataclass
class IdentityState:
    """In-memory representation of one agent's identity state."""

    principal: str
    components: dict[Component, dict[str, Any]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    schema_version: int = 1

    # ------------------------------------------------------------------
    # accessors / mutators
    # ------------------------------------------------------------------

    def status(self, component: Component) -> ComponentStatus:
        entry = self.components.get(component)
        if entry is None:
            return ComponentStatus.PENDING
        try:
            return ComponentStatus(entry.get("status", "pending"))
        except ValueError:
            return ComponentStatus.PENDING

    def set_component(
        self,
        component: Component,
        *,
        status: ComponentStatus,
        details: dict[str, Any] | None = None,
        next_steps: list[str] | None = None,
    ) -> None:
        entry = dict(self.components.get(component) or {})
        entry["status"] = status.value
        entry["updated_at"] = time.time()
        if details is not None:
            entry["details"] = details
        if next_steps is not None:
            entry["next_steps"] = next_steps
        self.components[component] = entry
        self.updated_at = time.time()

    def get_details(self, component: Component) -> dict[str, Any]:
        return dict((self.components.get(component) or {}).get("details") or {})

    def get_next_steps(self, component: Component) -> list[str]:
        return list((self.components.get(component) or {}).get("next_steps") or [])

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "principal": self.principal,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "components": {comp.value: entry for comp, entry in self.components.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdentityState:
        if not isinstance(data, dict):
            raise IdentityStateError("state file must be a JSON object")
        if data.get("schema_version") != 1:
            raise IdentityStateError(
                f"unsupported schema_version: {data.get('schema_version')!r}"
            )
        principal = data.get("principal")
        if not isinstance(principal, str):
            raise IdentityStateError("state file missing 'principal'")
        components_raw = data.get("components") or {}
        components: dict[Component, dict[str, Any]] = {}
        for key, value in components_raw.items():
            try:
                comp = Component(key)
            except ValueError:
                # Tolerate unknown components for forward-compat; ignore.
                continue
            if isinstance(value, dict):
                components[comp] = value
        return cls(
            principal=principal,
            components=components,
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


def default_state_dir() -> Path:
    override = os.environ.get("HOMELAB_IDENTITY_STATE")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "homelab-control" / "identity"


@dataclass
class StateStore:
    """Filesystem-backed store for ``IdentityState`` objects."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()

    def path_for(self, principal: str) -> Path:
        slug = _slugify_principal(principal)
        return self.root / f"{slug}.json"

    def load(self, principal: str) -> IdentityState:
        path = self.path_for(principal)
        if not path.is_file():
            return IdentityState(principal=principal)
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise IdentityStateError(f"{path}: invalid JSON: {exc}") from exc
        state = IdentityState.from_dict(data)
        if state.principal != principal:
            raise IdentityStateError(
                f"{path}: principal mismatch ({state.principal!r} vs {principal!r})"
            )
        return state

    def save(self, state: IdentityState) -> Path:
        path = self.path_for(state.principal)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
        return path

    def list_principals(self) -> list[str]:
        if not self.root.is_dir():
            return []
        principals: list[str] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_file() or entry.suffix != ".json":
                continue
            try:
                with entry.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                principal = data.get("principal")
                if isinstance(principal, str):
                    principals.append(principal)
            except (OSError, json.JSONDecodeError):
                continue
        return principals


def _slugify_principal(principal: str) -> str:
    return principal.replace(":", "-").replace("/", "-")
