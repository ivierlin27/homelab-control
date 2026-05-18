"""Inventory probe: list running containers across every known host.

Designed to run from Alienware. SSH into Proxmox (already authorized for
the backup pipeline) and shell into each LXC via ``pct exec`` to ask
Docker what is running. Output is a flat list of
``{host, container, image, tag, container_id, image_id}`` records — one
per running container.

No dependency on ``inventory/services.yaml``: catches images we forgot
to declare, and survives services.yaml drift.

Hosts probed today:

- ``alienware``           — local docker (where the gateway + vLLM + agents run)
- ``proxmox-lxc-200..204``— memory-engine, forgejo, vaultwarden, infisical,
                            homelab-operator (each runs its own docker stack)

The list is centralised in :data:`DEFAULT_TARGETS` so new LXCs are a
one-line addition.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

log = logging.getLogger("maintenance-scan.probe")

CANDIDATE_RUNTIMES = ("docker", "podman")


@dataclass(frozen=True)
class ProbeTarget:
    """How to reach one docker host."""

    host_label: str
    # Argv prefix that wraps a docker command so it runs on the right host.
    # e.g. local: []
    #      ssh:   ["ssh", "-o", "BatchMode=yes", "root@proxmox.dev-path.org"]
    #      lxc:   ["ssh", ..., "pct", "exec", "200", "--"]
    argv_prefix: list[str] = field(default_factory=list)

    def cmd(self, *args: str) -> list[str]:
        return [*self.argv_prefix, *args]


def default_targets() -> list[ProbeTarget]:
    proxmox_ssh = os.environ.get(
        "MAINTENANCE_SCAN_PROXMOX_SSH", "root@proxmox.dev-path.org"
    )
    lxc_ids = [
        s.strip()
        for s in os.environ.get("MAINTENANCE_SCAN_LXC_IDS", "200,201,202,203,204").split(",")
        if s.strip()
    ]
    targets: list[ProbeTarget] = [ProbeTarget(host_label="alienware")]
    for lxc_id in lxc_ids:
        targets.append(
            ProbeTarget(
                host_label=f"pve-lxc-{lxc_id}",
                argv_prefix=[
                    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                    proxmox_ssh,
                    "pct", "exec", lxc_id, "--",
                ],
            )
        )
    return targets


DEFAULT_TARGETS = default_targets()


@dataclass
class ContainerRecord:
    host: str
    container: str
    image: str           # e.g. "ghcr.io/khoj-ai/khoj"
    tag: str             # e.g. "latest" or "1.42.0"
    container_id: str
    image_id: str        # sha256:... (resolved separately for tag-drift detection)
    status: str

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "container": self.container,
            "image": self.image,
            "tag": self.tag,
            "container_id": self.container_id,
            "image_id": self.image_id,
            "status": self.status,
        }


def _split_image_ref(ref: str) -> tuple[str, str]:
    """Split an image reference into (image, tag).

    Handles digests (``@sha256:...``) and missing tags (defaults to ``latest``).
    Registry hosts and namespaces are preserved as part of ``image``.
    """
    if "@" in ref:
        image, _digest = ref.rsplit("@", 1)
        return image, "latest"  # digest-pinned → treat as latest for upgrade-eligibility
    # Image refs CAN contain a port (e.g. registry.local:5000/foo:tag). The
    # last ":" after the final "/" is the tag separator.
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        return ref[:last_colon], ref[last_colon + 1 :]
    return ref, "latest"


async def _run(cmd: list[str], *, timeout: float = 15.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return -1, "", f"timeout after {timeout}s"
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def _detect_runtime(target: ProbeTarget) -> str | None:
    """Return 'docker' or 'podman' (whichever exists on the target), else None."""
    for rt in CANDIDATE_RUNTIMES:
        rc, out, _ = await _run(target.cmd("sh", "-c", f"command -v {rt} >/dev/null && echo {rt}"), timeout=5.0)
        if rc == 0 and out.strip() == rt:
            return rt
    return None


async def probe_target(target: ProbeTarget) -> list[ContainerRecord]:
    runtime = await _detect_runtime(target)
    if runtime is None:
        log.info("probe %s: no docker/podman, skipping", target.host_label)
        return []
    # Step 1: get IDs (no template syntax → safe to pass through ssh)
    rc, out, err = await _run(target.cmd(runtime, "ps", "-q", "--no-trunc"))
    if rc != 0:
        log.warning("probe %s: %s ps failed rc=%s err=%s", target.host_label, runtime, rc, err.strip()[:160])
        return []
    ids = [line.strip() for line in out.splitlines() if line.strip()]
    if not ids:
        return []
    # Step 2: inspect all in one call → JSON array
    rc2, out2, err2 = await _run(
        target.cmd(runtime, "inspect", *ids), timeout=20.0,
    )
    if rc2 != 0:
        log.warning("probe %s: %s inspect failed: %s", target.host_label, runtime, err2.strip()[:160])
        return []
    try:
        inspected = json.loads(out2)
    except json.JSONDecodeError as exc:
        log.warning("probe %s: parse %s inspect json: %s", target.host_label, runtime, exc)
        return []
    rows: list[ContainerRecord] = []
    for entry in inspected:
        # docker keys (capitalized) vs podman keys (lowercase) — handle both
        cfg = entry.get("Config") or entry.get("config") or {}
        image_ref = (cfg.get("Image") or cfg.get("image") or "").strip()
        name = (entry.get("Name") or entry.get("name") or "").lstrip("/")
        cid = (entry.get("Id") or entry.get("id") or "")[:12]
        image_id = entry.get("Image") or entry.get("image") or ""
        state = entry.get("State") or entry.get("state") or {}
        status = state.get("Status") or state.get("status") or "?"
        if not image_ref:
            continue
        image, tag = _split_image_ref(image_ref)
        rows.append(ContainerRecord(
            host=target.host_label,
            container=name or cid,
            image=image,
            tag=tag,
            container_id=cid,
            image_id=image_id if isinstance(image_id, str) else str(image_id),
            status=status,
        ))
    log.info("probe %s (%s): %d container(s)", target.host_label, runtime, len(rows))
    return rows


async def probe_all(
    targets: Iterable[ProbeTarget] = DEFAULT_TARGETS,
) -> list[ContainerRecord]:
    results = await asyncio.gather(*(probe_target(t) for t in targets), return_exceptions=True)
    out: list[ContainerRecord] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("probe failed: %s", r)
            continue
        out.extend(r)
    return out


def write_snapshot(records: list[ContainerRecord], path: str) -> None:
    """Append a snapshot (one JSON object per scan) to ``path``."""
    import time
    from pathlib import Path

    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "records": [r.as_dict() for r in records],
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")
