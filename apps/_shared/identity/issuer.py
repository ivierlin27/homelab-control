"""Identity issuance state machine.

The issuer is the one place that decides what each agent needs and how to
provision it. Some steps are fully automated (SSH keypair, sandbox image
build); others print operator checklists because no API exists or because
admin credentials should not be assumed in the calling environment.

Each ``issue_*`` function is idempotent: re-running detects existing
artifacts and updates the state file rather than re-creating them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from apps._shared.registry import AgentManifest, Registry, load_registry

from .state import (
    ALL_COMPONENTS,
    Component,
    ComponentStatus,
    IdentityState,
    StateStore,
    default_state_dir,
)

SSH_KEY_TYPE = "ed25519"


class IssuerError(Exception):
    """Raised when an automated issuance step fails."""


# ---------------------------------------------------------------------------
# Top-level: issue everything possible for one principal
# ---------------------------------------------------------------------------


@dataclass
class IssuanceReport:
    """Per-component result of one ``issue_principal`` call."""

    principal: str
    components: dict[Component, ComponentStatus]
    state_path: Path

    def needs_operator_action(self) -> bool:
        return any(
            status == ComponentStatus.OPERATOR_TODO for status in self.components.values()
        )


def issue_principal(
    principal: str,
    *,
    registry: Registry | None = None,
    state_store: StateStore | None = None,
    ssh_dir: Path | None = None,
) -> IssuanceReport:
    """Run every eligible step for ``principal`` and persist the state.

    Steps that require operator action (Forgejo bot, Discord bot, Infisical
    token) are recorded as ``OPERATOR_TODO`` with a checklist; the caller
    can re-run after completing those steps to advance the state machine.
    """

    registry = registry or load_registry()
    manifest = registry.get(principal)
    state_store = state_store or StateStore(default_state_dir())
    state = state_store.load(principal)

    # Mark components the manifest doesn't request as NOT_REQUIRED so the
    # status table is honest about scope.
    _mark_not_required(state, manifest)

    issue_ssh_key(state, manifest, ssh_dir=ssh_dir)
    register_sandbox_image(state, manifest)
    register_operator_todo(state, manifest, Component.FORGEJO_ACCOUNT, _forgejo_account_steps(manifest))
    register_operator_todo(state, manifest, Component.FORGEJO_PAT, _forgejo_pat_steps(manifest))
    register_operator_todo(state, manifest, Component.DISCORD_BOT, _discord_bot_steps(manifest))
    register_operator_todo(state, manifest, Component.INFISICAL_TOKEN, _infisical_steps(manifest))

    state_path = state_store.save(state)
    return IssuanceReport(
        principal=principal,
        components={comp: state.status(comp) for comp in ALL_COMPONENTS},
        state_path=state_path,
    )


# ---------------------------------------------------------------------------
# Per-component: SSH keypair (fully automated)
# ---------------------------------------------------------------------------


def issue_ssh_key(
    state: IdentityState,
    manifest: AgentManifest,
    *,
    ssh_dir: Path | None = None,
) -> ComponentStatus:
    """Generate an ed25519 keypair if the agent has a git_user and none exists."""

    git_user = manifest.get("identity", "git_user")
    if not git_user:
        state.set_component(Component.SSH_KEY, status=ComponentStatus.NOT_REQUIRED)
        return ComponentStatus.NOT_REQUIRED

    base = (ssh_dir or _default_ssh_dir()).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    private_key = base / git_user
    public_key = base / f"{git_user}.pub"

    if private_key.is_file() and public_key.is_file():
        details = {
            "private_key": str(private_key),
            "public_key": str(public_key),
            "fingerprint": _ssh_fingerprint(public_key),
            "key_type": SSH_KEY_TYPE,
        }
        state.set_component(
            Component.SSH_KEY,
            status=ComponentStatus.ISSUED,
            details=details,
            next_steps=[
                f"Public key already present; copy contents of {public_key} into Forgejo for {git_user}.",
            ],
        )
        return ComponentStatus.ISSUED

    if not shutil.which("ssh-keygen"):
        raise IssuerError("ssh-keygen not found on PATH")

    comment = f"{git_user}@{manifest.principal}"
    cmd = [
        "ssh-keygen",
        "-t",
        SSH_KEY_TYPE,
        "-N",
        "",                  # no passphrase
        "-C",
        comment,
        "-f",
        str(private_key),
    ]
    try:
        subprocess.run(  # noqa: S603 - controlled argv
            cmd, capture_output=True, text=True, check=True, timeout=30
        )
    except subprocess.CalledProcessError as exc:
        raise IssuerError(
            f"ssh-keygen failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    private_key.chmod(0o600)
    public_key.chmod(0o644)
    details = {
        "private_key": str(private_key),
        "public_key": str(public_key),
        "fingerprint": _ssh_fingerprint(public_key),
        "key_type": SSH_KEY_TYPE,
    }
    state.set_component(
        Component.SSH_KEY,
        status=ComponentStatus.ISSUED,
        details=details,
        next_steps=[
            f"Add the public key {public_key} to Forgejo user {git_user}'s SSH keys.",
            "Configure ssh client (~/.ssh/config) with a Host alias if the queue runner uses a custom HOST.",
        ],
    )
    return ComponentStatus.ISSUED


# ---------------------------------------------------------------------------
# Per-component: sandbox image (recorded, not built here to avoid podman dep)
# ---------------------------------------------------------------------------


def register_sandbox_image(state: IdentityState, manifest: AgentManifest) -> ComponentStatus:
    """Record the expected image tag and Containerfile path.

    The actual `podman build` is invoked separately via
    ``python3 -m apps._shared.sandbox build --principal <principal>``.
    Keeping the build out of the issuer keeps this module dependency-free
    and lets non-podman hosts (e.g. the operator's laptop) still run
    issuance and produce checklists.
    """

    image_name = manifest.get("sandbox", "base_image")
    if not image_name:
        state.set_component(Component.SANDBOX_IMAGE, status=ComponentStatus.NOT_REQUIRED)
        return ComponentStatus.NOT_REQUIRED

    repo_root = Path(__file__).resolve().parents[3]
    containerfile_rel = Path(
        "apps/_shared/sandbox/images"
    ) / f"{image_name}.Containerfile"
    containerfile_abs = repo_root / containerfile_rel

    if containerfile_abs.is_file():
        state.set_component(
            Component.SANDBOX_IMAGE,
            status=ComponentStatus.OPERATOR_TODO,
            details={
                "image_tag": f"{image_name}:latest",
                "containerfile": str(containerfile_rel),
            },
            next_steps=[
                f"Run: python3 -m apps._shared.sandbox build --principal {manifest.principal}",
                "Verify with: python3 -m apps._shared.sandbox build --principal "
                f"{manifest.principal} --print-only",
            ],
        )
        return ComponentStatus.OPERATOR_TODO

    state.set_component(
        Component.SANDBOX_IMAGE,
        status=ComponentStatus.OPERATOR_TODO,
        details={
            "image_tag": f"{image_name}:latest",
            "containerfile": str(containerfile_rel),
            "containerfile_present": False,
        },
        next_steps=[
            f"Create {containerfile_rel}; FROM agent-base:latest and add agent-specific deps.",
            f"Then: python3 -m apps._shared.sandbox build --principal {manifest.principal}",
        ],
    )
    return ComponentStatus.OPERATOR_TODO


# ---------------------------------------------------------------------------
# Per-component: operator-mediated checklists
# ---------------------------------------------------------------------------


def register_operator_todo(
    state: IdentityState,
    manifest: AgentManifest,
    component: Component,
    next_steps: list[str] | None,
) -> ComponentStatus:
    if next_steps is None:
        state.set_component(component, status=ComponentStatus.NOT_REQUIRED)
        return ComponentStatus.NOT_REQUIRED
    if state.status(component) == ComponentStatus.ISSUED:
        # Operator already marked it done; preserve.
        return ComponentStatus.ISSUED
    state.set_component(
        component,
        status=ComponentStatus.OPERATOR_TODO,
        next_steps=next_steps,
    )
    return ComponentStatus.OPERATOR_TODO


def _forgejo_account_steps(manifest: AgentManifest) -> list[str] | None:
    account = manifest.get("identity", "forgejo_account")
    git_email = manifest.get("identity", "git_email") or f"{account}@forgejo.dev-path.org"
    if not account:
        return None
    return [
        "As a Forgejo admin, sign in to https://forgejo.dev-path.org and create a user:",
        f"  username: {account}",
        f"  email:    {git_email}",
        f"  full name: {manifest.display_name}",
        "  role: User (NOT admin)",
        "  password: long random; rotate immediately and discard the value",
        "Disable web sign-in for the bot user once the PAT is created.",
        f"After creation, run: identity confirm --principal {manifest.principal} "
        f"--component forgejo_account",
    ]


def _forgejo_pat_steps(manifest: AgentManifest) -> list[str] | None:
    account = manifest.get("identity", "forgejo_account")
    if not account:
        return None
    forgejo_scope = manifest.get("tool_grants", "forgejo", "scope") or "read"
    return [
        f"Sign in to Forgejo as {account} (one-time) at https://forgejo.dev-path.org",
        "Generate a Personal Access Token with the minimum scopes for the agent's role:",
        f"  scope: {forgejo_scope}",
        "Copy the token, then immediately store it in Infisical under the agent's "
        "secrets_profile as FORGEJO_TOKEN.",
        "Sign out and disable web sign-in for the bot user.",
        f"After storing, run: identity confirm --principal {manifest.principal} "
        f"--component forgejo_pat",
    ]


def _discord_bot_steps(manifest: AgentManifest) -> list[str] | None:
    bot_app = manifest.get("identity", "discord_bot_app_name") or manifest.get(
        "discord", "bot_app_name"
    )
    if not bot_app:
        return None
    channels = manifest.get("discord", "channels", default=[]) or []
    channel_summary = ", ".join(
        ch.get("name") or str(ch.get("id"))
        for ch in channels
        if isinstance(ch, dict)
    ) or "(none in registry)"
    return [
        f"Visit https://discord.com/developers/applications and create an app named: {bot_app}",
        "Under 'Bot': add a bot, copy the token (this is the only time it's shown).",
        "Under 'Bot' > 'Privileged Gateway Intents': enable Message Content Intent only if needed.",
        "Generate an OAuth2 invite URL with these scopes/perms only:",
        "  scopes: bot, applications.commands",
        "  permissions: Send Messages, Read Message History, Create Public Threads, "
        "Send Messages in Threads, Add Reactions, Embed Links",
        "Invite the bot to Kevin's guild.",
        f"Add the bot to these channels: {channel_summary}",
        "Store the token in Infisical under the agent's secrets_profile as DISCORD_BOT_TOKEN.",
        f"After storing, run: identity confirm --principal {manifest.principal} "
        f"--component discord_bot",
    ]


def _infisical_steps(manifest: AgentManifest) -> list[str] | None:
    profile = manifest.get("identity", "secrets_profile")
    if not profile or profile == "none":
        return None
    return [
        f"Sign in to Infisical as admin at https://infisical.dev-path.org",
        f"Create a project / environment scope named: {profile}",
        "Grant the bot identity an access token scoped to that project, read-only by default.",
        f"Save the access token to ~/.config/homelab-control/agent-{profile}.env on the runner host as INFISICAL_TOKEN.",
        f"After storing, run: identity confirm --principal {manifest.principal} "
        f"--component infisical_token",
    ]


# ---------------------------------------------------------------------------
# Verification + revocation
# ---------------------------------------------------------------------------


def verify_principal(
    principal: str,
    *,
    registry: Registry | None = None,
    state_store: StateStore | None = None,
) -> dict[Component, tuple[ComponentStatus, str]]:
    """Spot-check what we can verify locally.

    Currently checks that the SSH keypair files exist and are 0600/0644 and
    that the recorded sandbox Containerfile exists. Forgejo/Discord/Infisical
    verification requires admin tokens and is left for a follow-up ticket.
    """

    registry = registry or load_registry()
    manifest = registry.get(principal)
    state_store = state_store or StateStore(default_state_dir())
    state = state_store.load(principal)

    out: dict[Component, tuple[ComponentStatus, str]] = {}

    # SSH keypair
    if state.status(Component.SSH_KEY) == ComponentStatus.ISSUED:
        details = state.get_details(Component.SSH_KEY)
        priv = Path(details.get("private_key") or "")
        pub = Path(details.get("public_key") or "")
        if not priv.is_file() or not pub.is_file():
            out[Component.SSH_KEY] = (ComponentStatus.PENDING, "key files missing on disk")
        elif (priv.stat().st_mode & 0o077) != 0:
            out[Component.SSH_KEY] = (
                ComponentStatus.ISSUED,
                f"warning: {priv} permissions too open ({oct(priv.stat().st_mode & 0o777)})",
            )
        else:
            out[Component.SSH_KEY] = (ComponentStatus.ISSUED, "ok")
    else:
        out[Component.SSH_KEY] = (state.status(Component.SSH_KEY), "")

    # Sandbox image: confirm Containerfile exists
    if manifest.get("sandbox", "base_image"):
        repo_root = Path(__file__).resolve().parents[3]
        cf = repo_root / "apps/_shared/sandbox/images" / (
            f"{manifest.get('sandbox', 'base_image')}.Containerfile"
        )
        if cf.is_file():
            out[Component.SANDBOX_IMAGE] = (state.status(Component.SANDBOX_IMAGE), "Containerfile present")
        else:
            out[Component.SANDBOX_IMAGE] = (
                ComponentStatus.PENDING,
                f"Containerfile missing: {cf}",
            )
    else:
        out[Component.SANDBOX_IMAGE] = (ComponentStatus.NOT_REQUIRED, "")

    for comp in (
        Component.FORGEJO_ACCOUNT,
        Component.FORGEJO_PAT,
        Component.DISCORD_BOT,
        Component.INFISICAL_TOKEN,
    ):
        out[comp] = (state.status(comp), "verification requires admin tokens; not implemented")

    return out


def revoke_component(
    principal: str,
    component: Component,
    *,
    state_store: StateStore | None = None,
    delete_local_artifacts: bool = False,
) -> ComponentStatus:
    """Mark a component as revoked. For SSH keys, optionally delete the files."""

    state_store = state_store or StateStore(default_state_dir())
    state = state_store.load(principal)

    if delete_local_artifacts and component == Component.SSH_KEY:
        details = state.get_details(component)
        for key in ("private_key", "public_key"):
            path_str = details.get(key)
            if path_str:
                path = Path(path_str)
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    state.set_component(
        component,
        status=ComponentStatus.REVOKED,
        next_steps=[
            "Rotate any downstream credentials that depended on this component.",
            "Re-run `identity issue` to reissue when ready.",
        ],
    )
    state_store.save(state)
    return ComponentStatus.REVOKED


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mark_not_required(state: IdentityState, manifest: AgentManifest) -> None:
    if not manifest.get("identity", "git_user") and state.status(Component.SSH_KEY) != ComponentStatus.ISSUED:
        state.set_component(Component.SSH_KEY, status=ComponentStatus.NOT_REQUIRED)
    if not manifest.get("sandbox", "base_image"):
        state.set_component(Component.SANDBOX_IMAGE, status=ComponentStatus.NOT_REQUIRED)
    if not manifest.get("identity", "forgejo_account"):
        for comp in (Component.FORGEJO_ACCOUNT, Component.FORGEJO_PAT):
            state.set_component(comp, status=ComponentStatus.NOT_REQUIRED)
    if not (manifest.get("identity", "discord_bot_app_name") or manifest.get("discord", "bot_app_name")):
        state.set_component(Component.DISCORD_BOT, status=ComponentStatus.NOT_REQUIRED)
    profile = manifest.get("identity", "secrets_profile")
    if not profile or profile == "none":
        state.set_component(Component.INFISICAL_TOKEN, status=ComponentStatus.NOT_REQUIRED)


# ---------------------------------------------------------------------------
# Plan-only path: return what `issue` would do without any side effects.
#
# Use cases:
#   - Producing a per-principal runbook for an operator before any identity
#     is actually issued (e.g. for `agent:finance` before Phase 1 starts).
#   - Reviewing what identity work is outstanding without writing state files,
#     running ssh-keygen, or building sandbox images.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentPlan:
    """What ``issue_principal`` would do for one component, without doing it."""

    component: Component
    would_status: ComponentStatus  # what status this component would end up at
    summary: str                   # one-line human-readable summary
    next_steps: list[str]          # operator checklist; empty for NOT_REQUIRED


@dataclass(frozen=True)
class PrincipalPlan:
    """Aggregate plan for one principal."""

    principal: str
    manifest_path: Path
    components: list[ComponentPlan]

    def needs_operator_action(self) -> bool:
        return any(c.would_status == ComponentStatus.OPERATOR_TODO for c in self.components)

    def to_dict(self) -> dict:
        return {
            "principal": self.principal,
            "manifest_path": str(self.manifest_path),
            "components": [
                {
                    "component": c.component.value,
                    "would_status": c.would_status.value,
                    "summary": c.summary,
                    "next_steps": list(c.next_steps),
                }
                for c in self.components
            ],
        }


def plan_principal(
    principal: str,
    *,
    registry: Registry | None = None,
    ssh_dir: Path | None = None,
    state_store: StateStore | None = None,
    ignore_state: bool = False,
) -> PrincipalPlan:
    """Compute the issuance plan for ``principal`` without any side effects.

    No state file writes, no ssh-keygen invocation, no podman build, no API
    calls. The only IO is read-only: loading the registry/manifest YAML, a
    couple of ``shutil.which`` / ``Path.is_file`` checks, and a read of the
    issuer's state store so already-confirmed operator-mediated components
    aren't re-reported as work.

    Pass ``ignore_state=True`` to produce a fresh-provisioning view (every
    component reported as if no prior issuance had happened). Useful for
    publishing a generic onboarding runbook.
    """
    registry = registry or load_registry()
    manifest = registry.get(principal)
    ssh_base = (ssh_dir or _default_ssh_dir()).expanduser()

    # State-store read is best-effort: a missing or unreadable state file
    # collapses to "nothing issued yet," matching the issuer's own assumption.
    if ignore_state:
        state = IdentityState(principal=principal)
    else:
        try:
            store = state_store or StateStore(default_state_dir())
            state = store.load(principal)
        except Exception:  # noqa: BLE001
            state = IdentityState(principal=principal)

    components: list[ComponentPlan] = []

    # --- SSH key ----------------------------------------------------------
    git_user = manifest.get("identity", "git_user")
    if not git_user:
        components.append(ComponentPlan(
            Component.SSH_KEY, ComponentStatus.NOT_REQUIRED,
            "manifest has no identity.git_user; no SSH key needed",
            [],
        ))
    else:
        private_key = ssh_base / git_user
        public_key = ssh_base / f"{git_user}.pub"
        keygen_available = shutil.which("ssh-keygen") is not None
        if private_key.is_file() and public_key.is_file():
            components.append(ComponentPlan(
                Component.SSH_KEY, ComponentStatus.ISSUED,
                f"SSH key already present at {private_key} (would skip)",
                [
                    f"Public key contents: `cat {public_key}` — paste into Forgejo "
                    f"user {git_user}'s SSH keys panel if not already there.",
                ],
            ))
        else:
            comment = f"{git_user}@{manifest.principal}"
            cmd = (
                f"ssh-keygen -t {SSH_KEY_TYPE} -N '' -C '{comment}' "
                f"-f {private_key}"
            )
            steps = [
                f"Would generate ed25519 keypair at: {private_key} (and .pub)",
                f"Equivalent command (the issuer runs this for real on `identity issue`): `{cmd}`",
                f"After generation, add the public key to Forgejo user {git_user} (Settings → SSH Keys).",
            ]
            if not keygen_available:
                steps.insert(0, "WARN: ssh-keygen not found on this host's PATH; "
                                "`identity issue` will fail here. Run from a host that has openssh-client.")
            components.append(ComponentPlan(
                Component.SSH_KEY, ComponentStatus.PENDING,
                f"would generate {SSH_KEY_TYPE} keypair for {git_user}",
                steps,
            ))

    # --- Sandbox image ----------------------------------------------------
    image_name = manifest.get("sandbox", "base_image")
    if not image_name:
        components.append(ComponentPlan(
            Component.SANDBOX_IMAGE, ComponentStatus.NOT_REQUIRED,
            "manifest has no sandbox.base_image; no image needed",
            [],
        ))
    elif state.status(Component.SANDBOX_IMAGE) == ComponentStatus.ISSUED:
        components.append(ComponentPlan(
            Component.SANDBOX_IMAGE, ComponentStatus.ISSUED,
            f"image tag {image_name}:latest — already built and confirmed (would skip)",
            [
                f"Rebuild on this host: `python3 -m apps._shared.sandbox build "
                f"--principal {principal}`",
                f"Verify: `podman images | grep ^localhost/{image_name}`",
            ],
        ))
    else:
        repo_root = Path(__file__).resolve().parents[3]
        containerfile_rel = Path(
            "apps/_shared/sandbox/images"
        ) / f"{image_name}.Containerfile"
        containerfile_abs = repo_root / containerfile_rel
        present = containerfile_abs.is_file()
        steps: list[str] = []
        if not present:
            steps.append(
                f"Create {containerfile_rel}; FROM agent-base:latest and add agent-specific deps."
            )
        steps.append(
            f"On every runner host, build the image: "
            f"`python3 -m apps._shared.sandbox build --principal {principal}`"
        )
        steps.append(
            f"Verify: `podman images | grep ^localhost/{image_name}` and "
            f"`python3 -m apps._shared.sandbox build --principal {principal} --print-only`"
        )
        components.append(ComponentPlan(
            Component.SANDBOX_IMAGE, ComponentStatus.OPERATOR_TODO,
            f"image tag {image_name}:latest; Containerfile "
            + ("present" if present else "MISSING — create it first"),
            steps,
        ))

    # --- Forgejo account + PAT + Discord bot + Infisical token ------------
    # These four are entirely operator-mediated; the existing step builders
    # already produce the right checklists, so we reuse them verbatim. For
    # each, the issuer's state file is the source of truth for whether the
    # operator has already run `identity confirm` — if so, we report ISSUED
    # (with a one-line "already confirmed" note) instead of re-emitting the
    # full checklist.
    for component, builder, summary_when_required in (
        (Component.FORGEJO_ACCOUNT, _forgejo_account_steps,
         "Create the bot Forgejo user"),
        (Component.FORGEJO_PAT, _forgejo_pat_steps,
         "Generate scoped Personal Access Token for the bot"),
        (Component.DISCORD_BOT, _discord_bot_steps,
         "Create Discord application + bot, invite, store token"),
        (Component.INFISICAL_TOKEN, _infisical_steps,
         "Provision Infisical project + scoped access token"),
    ):
        steps = builder(manifest)
        if steps is None:
            components.append(ComponentPlan(
                component, ComponentStatus.NOT_REQUIRED,
                "not required by manifest",
                [],
            ))
            continue
        recorded = state.status(component)
        if recorded == ComponentStatus.ISSUED:
            components.append(ComponentPlan(
                component, ComponentStatus.ISSUED,
                "already confirmed via `identity confirm` (would skip)",
                [
                    f"To re-issue: `python -m apps._shared.identity revoke "
                    f"--principal {principal} --component {component.value}` "
                    f"then re-run `identity issue`.",
                ],
            ))
        elif recorded == ComponentStatus.REVOKED:
            components.append(ComponentPlan(
                component, ComponentStatus.REVOKED,
                "previously revoked; rotate per checklist before reissue",
                steps,
            ))
        else:
            components.append(ComponentPlan(
                component, ComponentStatus.OPERATOR_TODO,
                summary_when_required,
                steps,
            ))

    return PrincipalPlan(
        principal=principal,
        manifest_path=manifest.path,
        components=components,
    )


def render_plan_markdown(plan: PrincipalPlan) -> str:
    """Render a :class:`PrincipalPlan` as a runbook-style markdown document.

    Pure function — useful for tests and for piping plan output to disk.
    """
    glyph = {
        ComponentStatus.NOT_REQUIRED: "—",
        ComponentStatus.PENDING: "·",
        ComponentStatus.ISSUED: "✓",
        ComponentStatus.OPERATOR_TODO: "!",
        ComponentStatus.REVOKED: "✗",
    }
    lines: list[str] = []
    lines.append(f"# Identity issuance runbook — `{plan.principal}`")
    lines.append("")
    lines.append(f"Generated by `python -m apps._shared.identity plan --principal {plan.principal}`.")
    lines.append("")
    lines.append(f"- Manifest: `{plan.manifest_path}`")
    needs = "yes" if plan.needs_operator_action() else "no"
    lines.append(f"- Needs operator action: **{needs}**")
    lines.append("")
    lines.append("## Component summary")
    lines.append("")
    lines.append("| Component | Would-status | Summary |")
    lines.append("|---|---|---|")
    for c in plan.components:
        lines.append(
            f"| `{c.component.value}` | {glyph[c.would_status]} `{c.would_status.value}` "
            f"| {c.summary} |"
        )
    lines.append("")
    lines.append("## Operator checklists")
    lines.append("")
    for c in plan.components:
        if not c.next_steps:
            continue
        lines.append(f"### `{c.component.value}`  ({c.would_status.value})")
        lines.append("")
        for step in c.next_steps:
            lines.append(f"- {step}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "This runbook is generated from the agent manifest and the local host's available tooling. "
        "Re-run the command to refresh after any change to "
        f"`{plan.manifest_path.name}` or after ssh-keygen / podman become available."
    )
    lines.append("")
    return "\n".join(lines)


def _default_ssh_dir() -> Path:
    override = os.environ.get("HOMELAB_IDENTITY_SSH_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ssh" / "homelab-agents"


def _ssh_fingerprint(public_key_path: Path) -> str:
    try:
        proc = subprocess.run(  # noqa: S603
            ["ssh-keygen", "-l", "-E", "sha256", "-f", str(public_key_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""
