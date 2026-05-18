"""Tests for the probe (semver/image-ref parsing + record shape)."""

from __future__ import annotations

import pytest

from .probe import ContainerRecord, _split_image_ref, default_targets


@pytest.mark.parametrize("ref,image,tag", [
    ("ghcr.io/khoj-ai/khoj:latest", "ghcr.io/khoj-ai/khoj", "latest"),
    ("postgres:16-alpine", "postgres", "16-alpine"),
    ("pgvector/pgvector:pg16", "pgvector/pgvector", "pg16"),
    ("vaultwarden/server:1.35.4", "vaultwarden/server", "1.35.4"),
    # bare ref → :latest
    ("redis", "redis", "latest"),
    # digest-pinned → treat as :latest for upgrade-eligibility
    ("foo/bar@sha256:abc123", "foo/bar", "latest"),
    # registry with port
    ("registry.local:5000/foo:1.2", "registry.local:5000/foo", "1.2"),
])
def test_split_image_ref(ref, image, tag):
    assert _split_image_ref(ref) == (image, tag)


def test_default_targets_includes_alienware_and_lxcs(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_SCAN_LXC_IDS", "200,201")
    monkeypatch.setenv("MAINTENANCE_SCAN_PROXMOX_SSH", "root@p")
    # default_targets reads env at call time
    from . import probe as probe_mod
    targets = probe_mod.default_targets()
    labels = [t.host_label for t in targets]
    assert labels == ["alienware", "pve-lxc-200", "pve-lxc-201"]
    # ssh prefix wired up
    assert targets[1].argv_prefix[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert "pct" in targets[1].argv_prefix
    assert "200" in targets[1].argv_prefix


def test_container_record_serializes():
    r = ContainerRecord(host="h", container="c", image="ghcr.io/foo/bar",
                        tag="1.2", container_id="abc", image_id="sha", status="running")
    assert r.as_dict()["image"] == "ghcr.io/foo/bar"
    assert r.as_dict()["tag"] == "1.2"
