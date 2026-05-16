# Sandbox base images

Per-agent images live here, one Containerfile per principal:

- `_base.Containerfile` — locked-down Debian-slim base with `git`, `python3`,
  a non-root `agent` user (UID/GID match host via build-args), no shell
  history, no SSH client by default.
- `agent-<name>.Containerfile` — per-agent overlay; `FROM` the base, then
  layer in the language runtimes / CLIs that agent needs.

Build all images:

```bash
python3 -m apps._shared.sandbox build --all
```

Build one:

```bash
python3 -m apps._shared.sandbox build --principal agent:homelab-maintainer
```

The image tag matches the manifest's `sandbox.base_image` field
(e.g. `agent-homelab-maintainer:latest`).

## Why per-agent images

A single shared image would either be too narrow (agents that need extra
tooling can't get it) or too broad (every agent has shells and clients it
doesn't need, blowing the default-deny posture). Per-agent images are cheap
under Podman's storage driver and make the trust surface explicit: if an
agent needs `kubectl`, it goes in that agent's Containerfile and shows up in
the registry.

## Pinning

The base image pins the upstream Debian digest. Update by editing the
`FROM` line and rebuilding `--all`. The pin is checked into git so rebuilds
on different hosts produce identical images.
