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
| `relabel=shared` on every bind mount (added 2026-05-18) | SELinux is **fully enforcing**; Podman applies the container's MCS categories to the bind-mounted host path on each run via `chcon` |
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
- **Initial workaround:** added `--userns=keep-id` and `--security-opt=label=disable` to `SandboxRunner` argv. Both are the documented rootless-podman patterns. The `label=disable` part was a known compromise — it turns off SELinux confinement for the container entirely — tracked for re-tightening below.
- **Followup:** runbook created; commits `2af23b3`, `f71dd71`.

### 2026-05-18 (later same day) — SELinux re-tightening

- **Symptom:** with `--security-opt=label=disable` removed for repro, the same command fails again with `ls: cannot open directory '/work': Permission denied` and `cat: /work/marker.txt: Permission denied`.
- **Investigation:** live tail of `journalctl -t setroubleshoot` while reproducing showed a denial on `cat` opening `/work/marker.txt`. The raw AVC (from `sudo ausearch -m AVC -ts recent`) tells the story setroubleshoot's first plugin missed:

  ```text
  type=AVC msg=audit(...): avc: denied { read open } for pid=NNNN comm="cat"
    name="marker.txt"
    scontext=unconfined_u:system_r:container_t:s0:c597,c723
    tcontext=unconfined_u:object_r:user_tmp_t:s0
    tclass=file permissive=0
  ```
- **Root cause:** SELinux MCS category mismatch.
  - The container's **source** label has two MCS categories Podman assigned per-run (`c597,c723`).
  - The host bind path under `/tmp` has **target** label `user_tmp_t:s0` with no categories.
  - MCS requires the target's category set to be a subset of the source's — empty is technically a subset of anything, BUT the type transition between `container_t` and `user_tmp_t` is itself denied because `container_t` isn't allowed to read `user_tmp_t` at all.
  - setroubleshoot's `restorecon` plugin (99.5% confidence) suggested relabeling the file to `default_t`. **That would NOT have helped** — `container_t` also can't read `default_t`. The plugin sees "label mismatch" and reflexively suggests `restorecon`; the *right* answer is to make Podman use a path it's allowed to relabel.
- **Fix:**
  - Drop `--security-opt=label=disable` from `SandboxRunner` argv.
  - Add `relabel=shared` to every bind-mount spec. Podman then `chcon`s the host path to `container_file_t` with the running container's MCS categories on each run. This is the `--mount` equivalent of `--volume :z`.
  - Refuse `worktree_path` values under `/tmp` at `__post_init__`, because Fedora policy refuses to let containers relabel `/tmp` — that block is the original incident's cause. `HOMELAB_SANDBOX_ALLOW_TMP=1` overrides for tests.
  - New helper `apps._shared.sandbox.scratch.make_scratch_dir()` provisions scratch under `/var/lib/homelab-control/sandbox/` (typed `container_file_t` via `semanage fcontext`) so the relabel always succeeds.
- **One-time host setup** (per Alienware-like deploy host):
  ```bash
  sudo mkdir -p /var/lib/homelab-control/sandbox
  sudo chown $USER:$USER /var/lib/homelab-control/sandbox
  sudo semanage fcontext -a -t container_file_t \
    '/var/lib/homelab-control/sandbox(/.*)?'
  sudo restorecon -Rv /var/lib/homelab-control/sandbox
  ```
- **Verification (live):** with the fix in place and watcher running, the probe via `make_scratch_dir()` ran cleanly — container saw `..` labeled `container_file_t:s0:c597,c723` and `marker.txt` readable. The runner's `__post_init__` guard refused a deliberately-passed `/tmp/...` worktree with the expected `SandboxError`. Zero AVCs in `journalctl -t setroubleshoot` during the run. Commit: `dc9b647`.
- **Followup:** `label=disable` is now blocked from reintroduction by `test_source_file_does_not_reintroduce_label_disable` in `apps/_shared/sandbox/test_runner.py`.

## Configuration

| Var | Default | Purpose |
|---|---|---|
| `AUTHOR_AGENT_SANDBOX_CHECKS` | `0` (off) | `1`/`true`/`yes`/`on` routes `execute_task`'s `checks` list through SandboxRunner |
| `AGENT_PRINCIPAL` | `agent:homelab` | which manifest the sandbox reads (image, allowed_hosts) |
| `sandbox.base_image` (in manifest) | `agent-homelab` | tag prefix; runner appends `:latest` |
| `sandbox.network.allowed_hosts` (in manifest) | `[forgejo.dev-path.org]` | when non-empty, runner flips from `--network=none` to `--network=slirp4netns` |

## Future work / known limitations

- **SELinux**: **re-tightened on 2026-05-18.** Container runs with full SELinux confinement (`container_t`); bind mount uses `relabel=shared` so Podman applies the per-run MCS label to the host path. See Past incidents above for the AVC + diagnosis.
- **slirp4netns vs. strict DNS allowlist**: when `allowed_hosts` is set, the runner currently uses `slirp4netns` (full slirp networking) rather than a strict per-host allowlist. The host firewall is the de-facto gate. Tracked as the `TODO(0.1)` comment in `apps/_shared/sandbox/runner.py::_network_args`.
- **Other shell-outs not yet wrapped**: `git worktree add`, `git add/commit/push` in `execute_task`, and `git_lines()` still run on the host. They need the agent's SSH key for `push`, so wrapping them requires plumbing SSH credentials into the sandbox — a separate sprint.
- **Image freshness**: there is no automatic rebuild on `apt` or pip-dep updates. Add to the weekly maintenance scan or a Forgejo Action.
