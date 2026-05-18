# Author Agent Sandbox (`AUTHOR_AGENT_SANDBOX_CHECKS`)

The author agent (`apps/author_agent`, running as `agent:homelab`) accepts
jobs that include a `checks` list — arbitrary shell strings such as
`pytest tests/`, `mypy .`, or `npm run lint` — that must succeed before
the agent commits and pushes a change. Historically these strings ran
with `shell=True` directly on the host, inheriting the host's environment,
filesystem, and network.

When `AUTHOR_AGENT_SANDBOX_CHECKS=1` is set, each check command is
routed through `apps._shared.sandbox.SandboxRunner` and executed inside
the per-principal container image built from
`apps/_shared/sandbox/images/agent-homelab.Containerfile`. The shell-string
contract is preserved (each command is wrapped as `sh -c "<command>"`).

Default is **off** so the operator opts in per-host.

## Setup

Per-host one-time setup:

```bash
# 1. Build the base + agent-homelab sandbox images (cached after first run).
python -m apps._shared.sandbox.build --principal agent:homelab

# 2. Flip the flag in the author agent's systemd unit, e.g.:
mkdir -p ~/.config/systemd/user/alienware-author-agent.service.d
cat > ~/.config/systemd/user/alienware-author-agent.service.d/sandbox.conf <<'EOF'
[Service]
Environment=AUTHOR_AGENT_SANDBOX_CHECKS=1
EOF
systemctl --user daemon-reload
systemctl --user restart alienware-author-agent.service
```

## Security model

Every sandboxed check runs with **all** of these podman flags:

| Flag | What it prevents |
|---|---|
| `--read-only` | writes to the container's root filesystem |
| `--cap-drop=ALL` | every Linux capability (no `CAP_NET_RAW`, `CAP_SYS_ADMIN`, etc.) |
| `--security-opt=no-new-privileges` | setuid escalation inside the container |
| `--security-opt=label=disable` | (see "Future work" below — SELinux is currently relaxed for the container only) |
| `--userns=keep-id` | container UID 1000 = host UID, so the bind mount is readable without host chown |
| `--pids-limit=512` | fork bombs |
| `--memory=2g` | OOM the host |
| `--network=none` (default) | egress; flips to `slirp4netns` only if `sandbox.network.allowed_hosts` is set in the agent's manifest |
| `--workdir=/work` + the only bind mount | the agent can only write to the bind-mounted worktree |

Mounts: only the job's worktree at `/work` (read-write). Container env
includes `SANDBOX_PRINCIPAL` and `SANDBOX_CORRELATION_ID` for in-sandbox
logging. Every run appends a `sandbox_check` row to the agent's
hash-chained `trust-ledger.jsonl` with the correlation id, image, network
mode, egress allowlist, exit code, and duration.

## Symptoms → likely causes

| Symptom | Likely cause | Where to look first |
|---|---|---|
| `SandboxedCheckError: sandbox launch failed: podman binary not found on PATH` | `podman` not installed on host | `which podman`; install with `dnf install podman` |
| `SandboxedCheckError: ...manifest.sandbox.base_image is required` | manifest `sandbox.base_image` missing | `config/agents/agent-homelab.yaml` |
| `Error: short-name resolution: ... agent-homelab:latest` | image not built on this host | `podman images \| grep agent-homelab`; if missing run `python -m apps._shared.sandbox.build --principal agent:homelab` |
| `stderr: ls: cannot open directory '/work': Permission denied` | rootless UID mismatch — image built with a different `AGENT_UID` than the runtime host user | rebuild image: `python -m apps._shared.sandbox.build --principal agent:homelab` |
| Check works on host, fails in sandbox: command not found | base image (debian:bookworm-slim) lacks the tool | add it to `apps/_shared/sandbox/images/agent-homelab.Containerfile` and rebuild |
| `network=blocked` for a check that needs HTTP | manifest only allows `forgejo.dev-path.org`; sandbox defaults to `--network=none` | update `sandbox.network.allowed_hosts` in `config/agents/agent-homelab.yaml` |

## Investigation steps

1. `echo $AUTHOR_AGENT_SANDBOX_CHECKS` (inside the service unit's environment) — is the flag actually on? `systemctl --user show alienware-author-agent.service -p Environment`
2. `podman images | grep agent-homelab` — image built?
3. `tail -20 ~/.local/state/homelab-control/agent-homelab/trust-ledger.jsonl | jq -c 'select(.event=="sandbox_check") | {seq:.audit_seq, ec:.exit_code, cmd:.command, dur:.duration_seconds}'` — recent check outcomes.
4. `python -m apps._shared.audit verify ~/.local/state/homelab-control/agent-homelab/trust-ledger.jsonl` — chain still intact?
5. One-shot manual check (mirrors what the worker does):
   ```python
   AUTHOR_AGENT_SANDBOX_CHECKS=1 python -c "
   from pathlib import Path
   from apps.author_agent.sandboxed import run_command_sandboxed
   from apps._shared.audit import AuditLog
   wt = Path('/tmp/sb-smoke'); wt.mkdir(exist_ok=True)
   r = run_command_sandboxed('echo hello; id; pwd', worktree=wt,
                              audit=AuditLog(str(wt/'ledger.jsonl')))
   print(r['returncode'], r['stdout'])
   "
   ```

## Recovery

- **Image missing on a fresh host:** `python -m apps._shared.sandbox.build --principal agent:homelab`.
- **Permission denied on /work after a host UID change:** rebuild the image (build.py passes `--build-arg AGENT_UID=$(id -u)`).
- **All checks suddenly failing:** flip the flag back off (`systemctl --user edit alienware-author-agent.service`, comment out `AUTHOR_AGENT_SANDBOX_CHECKS=1`, restart) so the agent reverts to host execution while you investigate.

## Past incidents

### 2026-05-18 — first live smoke on Alienware

- **Symptom:** sandbox launched, ran the right argv, and audit-logged correctly, but every command failed with `ls: cannot open directory '/work': Permission denied`.
- **Root cause (two layered issues):**
  1. Rootless podman maps container UID 1000 to a host subuid in `/etc/subuid`, not to the host's `kenns` user. The bind-mounted worktree (owned by host UID 1000) appeared unreadable to the in-container `agent` user.
  2. Even after fixing UID mapping, SELinux MCS labels on Fedora blocked the container from reading host-owned directories.
- **Fix:** added `--userns=keep-id` and `--security-opt=label=disable` to `SandboxRunner` argv. Both are the documented rootless-podman patterns. Defence-in-depth still includes `--read-only`, `--cap-drop=ALL`, `--no-new-privileges`, `--network=none` by default, mem/pid limits.
- **Followup:** runbook created; commits `2af23b3`, `f71dd71`. SELinux re-tightening is tracked under "Future work" below.

## Configuration

| Var | Default | Purpose |
|---|---|---|
| `AUTHOR_AGENT_SANDBOX_CHECKS` | `0` (off) | `1`/`true`/`yes`/`on` routes `execute_task`'s `checks` list through SandboxRunner |
| `AGENT_PRINCIPAL` | `agent:homelab` | which manifest the sandbox reads (image, allowed_hosts) |
| `sandbox.base_image` (in manifest) | `agent-homelab` | tag prefix; runner appends `:latest` |
| `sandbox.network.allowed_hosts` (in manifest) | `[forgejo.dev-path.org]` | when non-empty, runner flips from `--network=none` to `--network=slirp4netns` |

## Future work / known limitations

- **SELinux**: currently disabled per-container via `--security-opt=label=disable` because the default rootless container labels can't read host-owned bind mounts. Proper fix is a tuned `container_t` policy or per-worktree `:Z` relabel on each task.
- **slirp4netns vs. strict DNS allowlist**: when `allowed_hosts` is set, the runner currently uses `slirp4netns` (full slirp networking) rather than a strict per-host allowlist. The host firewall is the de-facto gate. Tracked as the `TODO(0.1)` comment in `apps/_shared/sandbox/runner.py::_network_args`.
- **Other shell-outs not yet wrapped**: `git worktree add`, `git add/commit/push` in `execute_task`, and `git_lines()` still run on the host. They need the agent's SSH key for `push`, so wrapping them requires plumbing SSH credentials into the sandbox — a separate sprint.
- **Image freshness**: there is no automatic rebuild on `apt` or pip-dep updates. Add to the weekly maintenance scan or a Forgejo Action.
