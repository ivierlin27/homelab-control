"""Per-agent identity issuance and tracking.

Each agent in the registry needs several pieces of identity wired up before it
can run:

- an SSH keypair for git pushes (where applicable)
- a Forgejo bot account + scoped PAT
- a Discord bot application + token
- an Infisical service token scoped to the agent's `secrets_profile`
- a per-agent sandbox container image

Some of these are fully automatable (SSH keypair, sandbox image build); some
require human action in a web console (Discord, sometimes Forgejo). The
issuer does what it can and emits explicit operator checklists for the rest,
recording all progress in a per-agent JSON state file.

State lives at ``$HOMELAB_IDENTITY_STATE/<principal-slug>.json``.
The default ``$HOMELAB_IDENTITY_STATE`` is
``~/.local/state/homelab-control/identity``.

See `docs/plans/phase-0-platform.md` section 0.2.
"""

from .state import (
    Component,
    ComponentStatus,
    IdentityState,
    IdentityStateError,
    StateStore,
    default_state_dir,
)
from .issuer import (
    IssuerError,
    SSH_KEY_TYPE,
    issue_principal,
    issue_ssh_key,
    revoke_component,
    verify_principal,
)

__all__ = [
    "Component",
    "ComponentStatus",
    "IdentityState",
    "IdentityStateError",
    "IssuerError",
    "SSH_KEY_TYPE",
    "StateStore",
    "default_state_dir",
    "issue_principal",
    "issue_ssh_key",
    "revoke_component",
    "verify_principal",
]
