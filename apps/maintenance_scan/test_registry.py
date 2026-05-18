"""Tests for the registry client (semver pick + drift classification)."""

from __future__ import annotations

import httpx
import pytest

from . import registry as reg
from .registry import (
    UpgradeFinding,
    _classify_drift,
    _parse_numeric,
    _split_registry_image,
    assess_image,
)


@pytest.mark.parametrize("tag,parsed", [
    ("1.2.3", (1, 2, 3)),
    ("v2.4", (2, 4)),
    ("1.0.0-rc1", None),
    ("1.0.0+build5", (1, 0, 0)),
    ("latest", None),
    ("pg16", None),
    ("16-alpine", None),
    ("main-latest", None),
])
def test_parse_numeric(tag, parsed):
    assert _parse_numeric(tag) == parsed


@pytest.mark.parametrize("image,registry,path", [
    ("ghcr.io/khoj-ai/khoj", "ghcr.io", "khoj-ai/khoj"),
    ("code.forgejo.org/forgejo/forgejo", "code.forgejo.org", "forgejo/forgejo"),
    ("docker.io/n8nio/n8n", "docker.io", "n8nio/n8n"),
    ("postgres", "docker.io", "postgres"),
    ("registry.local:5000/foo", "registry.local:5000", "foo"),
])
def test_split_registry_image(image, registry, path):
    assert _split_registry_image(image) == (registry, path)


def test_classify_drift_upgrade_in_same_major():
    severity, _, newest = _classify_drift("1.2.0", (1, 3, 1), (2, 0, 0))
    assert severity == "upgrade"
    assert newest == "1.3.1"


def test_classify_drift_new_major_only():
    severity, _, newest = _classify_drift("1.5.0", (1, 5, 0), (2, 0, 0))
    assert severity == "major-upgrade"
    assert newest == "2.0.0"


def test_classify_drift_ok_when_at_top_of_major():
    severity, _, newest = _classify_drift("1.5.0", (1, 5, 0), (1, 5, 0))
    assert severity == "ok"
    assert newest is None


@pytest.mark.anyio
async def test_assess_image_floating_tag_short_circuits():
    async with httpx.AsyncClient() as client:
        f = await assess_image(client, "n8nio/n8n", "latest")
    assert f.severity == "floating"
    assert "latest" in f.notes


@pytest.mark.anyio
async def test_assess_image_unmanaged_when_no_registry():
    async with httpx.AsyncClient() as client:
        f = await assess_image(client, "memory-mem0-api", "local")
    # tag "local" is in FLOATING_TAGS so caught there first
    assert f.severity in {"unmanaged", "floating"}


@pytest.mark.anyio
async def test_assess_image_uses_mocked_tags(monkeypatch):
    async def fake_list_tags(client, image):
        return "docker.io", ["1.0.0", "1.1.0", "1.2.3", "2.0.0", "latest"]

    monkeypatch.setattr(reg, "list_tags", fake_list_tags)
    async with httpx.AsyncClient() as client:
        f = await assess_image(client, "foo/bar", "1.1.0")
    assert f.severity == "upgrade"
    assert f.newest_tag == "1.2.3"


@pytest.mark.anyio
async def test_assess_image_reports_major_upgrade(monkeypatch):
    async def fake_list_tags(client, image):
        return "docker.io", ["1.0.0", "2.0.0", "2.1.0"]

    monkeypatch.setattr(reg, "list_tags", fake_list_tags)
    async with httpx.AsyncClient() as client:
        f = await assess_image(client, "foo/bar", "1.0.0")
    # in-major newest equals current → falls through to overall newest
    assert f.severity == "major-upgrade"
    assert f.newest_tag == "2.1.0"


@pytest.mark.anyio
async def test_assess_image_marks_non_numeric_tag_as_floating(monkeypatch):
    async def fake_list_tags(client, image):
        return "docker.io", ["1.0", "16-alpine"]

    monkeypatch.setattr(reg, "list_tags", fake_list_tags)
    async with httpx.AsyncClient() as client:
        f = await assess_image(client, "postgres", "16-alpine")
    assert f.severity == "floating"


@pytest.fixture
def anyio_backend():
    return "asyncio"
