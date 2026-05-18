"""Registry client: list tags for an image and pick the recommended upgrade.

Goal is "what semver-compatible upgrade is available *now*" — not full CVE
scoring (Trivy can join in v2). Three registry kinds are handled:

- **Docker Hub** (``docker.io/<namespace>/<repo>``) — public JSON API at
  ``https://hub.docker.com/v2/repositories/<ns>/<repo>/tags?page_size=100``
  with built-in ``last_updated`` we can use for tie-breaks.
- **GHCR / Forgejo OCI** (``ghcr.io/...`` / ``code.forgejo.org/...``) —
  standard OCI Distribution Spec ``/v2/<name>/tags/list`` after a
  short anonymous-pull bearer dance.
- **Local images** (no registry prefix or unknown registry) — yield no
  recommendation; reported as "unmanaged" so the operator at least sees
  them.

Semver pick rule: among tags that look numeric (``\\d+(\\.\\d+){0,3}``)
and that match the **major version** of the current tag, pick the
highest one strictly greater than current. Tags like ``latest``, ``main``,
``edge``, ``pg16`` etc. are reported as **floating** — the operator
can't infer an upgrade from a name alone, so we flag them for visibility
without recommending a pin.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger("maintenance-scan.registry")

# Only match pure numeric tags (optionally with build-metadata `+...`).
# Reject hyphen suffixes (`16-alpine`, `1.0.0-rc1`) — those are flavor variants
# or pre-releases where cross-tag upgrade recommendations would be wrong.
NUMERIC_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+){0,3})(\+.*)?$")
# Tags treated as "floating" — they move, we can't recommend a numeric upgrade.
FLOATING_TAGS = frozenset({
    "latest", "main", "edge", "stable", "nightly", "dev", "local",
    "main-latest", "rc",
})


@dataclass
class UpgradeFinding:
    image: str
    current_tag: str
    newest_tag: Optional[str]
    severity: str  # "ok" | "upgrade" | "major-upgrade" | "floating" | "unmanaged" | "error"
    notes: str = ""
    available_tags: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "image": self.image,
            "current_tag": self.current_tag,
            "newest_tag": self.newest_tag,
            "severity": self.severity,
            "notes": self.notes,
        }


# ----- semver helpers -----------------------------------------------------

def _parse_numeric(tag: str) -> Optional[tuple[int, ...]]:
    m = NUMERIC_TAG_RE.match(tag.strip())
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _tag_major(tag: str) -> Optional[int]:
    p = _parse_numeric(tag)
    return p[0] if p else None


def _tag_to_str(parts: tuple[int, ...]) -> str:
    return ".".join(str(x) for x in parts)


def _classify_drift(current: str, newest_in_major: Optional[tuple[int, ...]],
                    newest_overall: Optional[tuple[int, ...]]) -> tuple[str, str, Optional[str]]:
    cur = _parse_numeric(current)
    if newest_in_major and (cur is None or newest_in_major > cur):
        # padded compare to handle (1,2) vs (1,2,3)
        return "upgrade", f"upgrade available in same major", _tag_to_str(newest_in_major)
    if newest_overall and cur and newest_overall[0] > cur[0]:
        return "major-upgrade", f"new major available (current {current})", _tag_to_str(newest_overall)
    return "ok", "current is newest in major", None


# ----- registry fetchers --------------------------------------------------

def _split_registry_image(image: str) -> tuple[str, str]:
    """Split a fully-qualified image ref into (registry_host, path).

    Implicit registry is docker.io for refs without a host component.
    """
    # registry hosts contain '.' or ':' in the first segment
    head, _, rest = image.partition("/")
    if head and ("." in head or ":" in head or head == "localhost"):
        return head, rest or head
    return "docker.io", image  # implicit: docker.io/library/<image> handled below


async def _docker_hub_tags(client: httpx.AsyncClient, path: str) -> list[str]:
    # docker.io defaults: "ubuntu" → "library/ubuntu"
    if "/" not in path:
        path = f"library/{path}"
    tags: list[str] = []
    url = f"https://hub.docker.com/v2/repositories/{path}/tags?page_size=100&ordering=last_updated"
    for _ in range(3):  # follow at most 3 next pages
        try:
            r = await client.get(url, timeout=10.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("hub.docker.com %s: %s", path, exc)
            return tags
        data = r.json()
        for it in data.get("results", []) or []:
            name = it.get("name")
            if isinstance(name, str):
                tags.append(name)
        next_url = data.get("next")
        if not next_url:
            break
        url = next_url
    return tags


async def _oci_v2_tags(client: httpx.AsyncClient, registry: str, path: str) -> list[str]:
    # Bearer challenge → fetch tags. Works for ghcr.io and code.forgejo.org.
    base = f"https://{registry}/v2/{path}"
    try:
        r = await client.get(f"{base}/tags/list", timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("oci %s/%s: %s", registry, path, exc)
        return []
    if r.status_code == 401:
        # Parse Www-Authenticate: Bearer realm="…",service="…",scope="…"
        auth = r.headers.get("Www-Authenticate", "")
        params = dict(re.findall(r'(\w+)="([^"]+)"', auth))
        realm, service, scope = params.get("realm"), params.get("service"), params.get("scope")
        if not realm:
            return []
        if not scope:
            scope = f"repository:{path}:pull"
        try:
            tr = await client.get(realm, params={"service": service, "scope": scope}, timeout=10.0)
            tr.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("oci %s/%s bearer: %s", registry, path, exc)
            return []
        token = tr.json().get("token") or tr.json().get("access_token")
        if not token:
            return []
        try:
            r = await client.get(f"{base}/tags/list", headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("oci %s/%s tags retry: %s", registry, path, exc)
            return []
    elif r.status_code != 200:
        log.warning("oci %s/%s tags HTTP %s", registry, path, r.status_code)
        return []
    return list(r.json().get("tags") or [])


async def list_tags(client: httpx.AsyncClient, image: str) -> tuple[str, list[str]]:
    """Return (registry, tags) for an image reference like ``ghcr.io/foo/bar``."""
    registry, path = _split_registry_image(image)
    if registry == "docker.io":
        return registry, await _docker_hub_tags(client, path)
    return registry, await _oci_v2_tags(client, registry, path)


async def assess_image(client: httpx.AsyncClient, image: str, current_tag: str) -> UpgradeFinding:
    if current_tag in FLOATING_TAGS:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="floating",
                              notes=f"image is pinned to a moving tag ({current_tag}); re-pull to refresh")
    registry, _ = _split_registry_image(image)
    # Local-only images (no registry, no docker.io official) → unmanaged.
    # `_split_registry_image` returns "docker.io" for refs like "redis" too,
    # so a true unmanaged image is one whose tag we can't fetch.
    _, tags = await list_tags(client, image)
    if not tags:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="error", notes=f"could not fetch tags from {registry}")
    cur = _parse_numeric(current_tag)
    if cur is None:
        # Tag like "pg16", "16-alpine" — treat as floating-but-named.
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="floating",
                              notes=f"current tag {current_tag!r} is not numeric; can't infer upgrade")
    numeric = [(t, _parse_numeric(t)) for t in tags]
    numeric = [(t, p) for t, p in numeric if p is not None]
    if not numeric:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="floating", notes="no numeric tags published")
    in_major = [(t, p) for t, p in numeric if p[0] == cur[0]]
    newest_in_major = max((p for _, p in in_major), default=None)
    newest_overall = max((p for _, p in numeric), default=None)
    severity, notes, newest_str = _classify_drift(current_tag, newest_in_major, newest_overall)
    return UpgradeFinding(
        image=image, current_tag=current_tag, newest_tag=newest_str,
        severity=severity, notes=notes,
        available_tags=sorted({t for t, _ in numeric})[-20:],
    )


def assess_image_sync(client: httpx.Client, image: str, current_tag: str) -> UpgradeFinding:
    """Synchronous twin of :func:`assess_image` for the verifier callback."""
    if current_tag in FLOATING_TAGS:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="floating", notes=f"pinned to moving tag {current_tag}")
    registry, path = _split_registry_image(image)
    tags: list[str] = []
    try:
        if registry == "docker.io":
            url_path = path if "/" in path else f"library/{path}"
            url = f"https://hub.docker.com/v2/repositories/{url_path}/tags?page_size=100"
            for _ in range(3):
                r = client.get(url)
                if r.status_code != 200:
                    break
                data = r.json()
                tags.extend(t.get("name") for t in (data.get("results") or []) if t.get("name"))
                if not data.get("next"):
                    break
                url = data["next"]
        else:
            base = f"https://{registry}/v2/{path}"
            r = client.get(f"{base}/tags/list")
            if r.status_code == 401:
                params = dict(re.findall(r'(\w+)="([^"]+)"', r.headers.get("Www-Authenticate", "")))
                if params.get("realm"):
                    tr = client.get(params["realm"], params={
                        "service": params.get("service"),
                        "scope": params.get("scope") or f"repository:{path}:pull",
                    })
                    token = tr.json().get("token") or tr.json().get("access_token") if tr.status_code == 200 else None
                    if token:
                        r = client.get(f"{base}/tags/list", headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                tags = list(r.json().get("tags") or [])
    except httpx.HTTPError as exc:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="error", notes=str(exc)[:120])
    if not tags:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="error", notes=f"no tags fetched from {registry}")
    cur = _parse_numeric(current_tag)
    if cur is None:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="floating", notes=f"current tag {current_tag!r} is not numeric")
    numeric = [(t, _parse_numeric(t)) for t in tags]
    numeric = [(t, p) for t, p in numeric if p is not None]
    if not numeric:
        return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=None,
                              severity="floating", notes="no numeric tags published")
    newest_in_major = max((p for _, p in numeric if p[0] == cur[0]), default=None)
    newest_overall = max((p for _, p in numeric), default=None)
    severity, notes, newest_str = _classify_drift(current_tag, newest_in_major, newest_overall)
    return UpgradeFinding(image=image, current_tag=current_tag, newest_tag=newest_str,
                          severity=severity, notes=notes)


async def assess_many(images: list[tuple[str, str]]) -> list[UpgradeFinding]:
    """Assess a list of ``(image, current_tag)`` pairs concurrently."""
    async with httpx.AsyncClient(headers={"User-Agent": "homelab-control-maintenance/1.0"}) as client:
        sem = asyncio.Semaphore(8)

        async def _one(pair):
            async with sem:
                try:
                    return await assess_image(client, *pair)
                except Exception as exc:  # noqa: BLE001
                    log.warning("assess %s:%s: %s", pair[0], pair[1], exc)
                    return UpgradeFinding(image=pair[0], current_tag=pair[1], newest_tag=None,
                                          severity="error", notes=str(exc)[:120])

        return await asyncio.gather(*(_one(p) for p in images))
