"""Property helpers and optional vLLM summarization."""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from typing import Any

VALID_STATUSES = {
    "discovered",
    "reviewing",
    "visit_scheduled",
    "visited",
    "shortlisted",
    "backup",
    "rejected",
    "offer",
}

STATUS_RANK = {
    "discovered": 1,
    "backup": 2,
    "reviewing": 3,
    "visit_scheduled": 4,
    "visited": 5,
    "shortlisted": 6,
    "offer": 7,
    "rejected": 0,
}


def _vllm_config() -> tuple[str, str, dict[str, str]]:
    base_url = os.environ.get("MOVE_VLLM_BASE_URL", "http://192.168.1.45:8002/v1").rstrip("/")
    model = os.environ.get("MOVE_VLLM_MODEL", "homelab-strong-long-vllm")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.environ.get("MOVE_VLLM_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return base_url, model, headers


def normalize_address(address: str) -> str:
    return " ".join(address.strip().split())


def address_match_key(address: str) -> str:
    """Leading number + street token, e.g. '1400 Coast Meridian' → '1400 coast'."""
    parts = normalize_address(address).lower().split()
    if len(parts) >= 2 and parts[0].isdigit():
        return f"{parts[0]} {parts[1]}"
    return " ".join(parts[:2]) if len(parts) >= 2 else parts[0] if parts else ""


def find_property(conn: sqlite3.Connection, *, address: str | None = None, forum_url: str | None = None) -> sqlite3.Row | None:
    if forum_url:
        row = conn.execute(
            "SELECT * FROM properties WHERE discord_forum_url = ?",
            (forum_url,),
        ).fetchone()
        if row:
            return row
    if address:
        normalized = normalize_address(address)
        row = conn.execute(
            "SELECT * FROM properties WHERE lower(address_normalized) = lower(?)",
            (normalized,),
        ).fetchone()
        if row:
            return row
        key = address_match_key(address)
        if key:
            for candidate in conn.execute("SELECT * FROM properties").fetchall():
                if address_match_key(candidate["address_normalized"]) == key:
                    return candidate
    return None


def _property_score(prop: dict[str, Any]) -> int:
    score = STATUS_RANK.get(prop.get("status") or "discovered", 0) * 10
    if prop.get("listing_url"):
        score += 100
    if prop.get("price"):
        score += 50
    if prop.get("sheet_row_url"):
        score += 25
    if prop.get("ai_summary"):
        score += 15
    return score


def dedupe_properties(conn: sqlite3.Connection) -> dict[str, Any]:
    """Remove duplicate board entries; keep richest record per address."""
    from collections import defaultdict

    rows = conn.execute("SELECT * FROM properties").fetchall()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        prop = dict(row)
        key = address_match_key(prop["address_normalized"]) or prop["address_normalized"].lower()
        groups[key].append(prop)

    deleted: list[dict[str, str]] = []
    merged = 0
    for _key, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=_property_score, reverse=True)
        keeper = items[0]
        for dup in items[1:]:
            conn.execute("DELETE FROM properties WHERE id = ?", (dup["id"],))
            deleted.append({"id": dup["id"], "address": dup["address_normalized"]})
            merged += 1
    return {"removed": merged, "deleted": deleted}


def upsert_property(
    conn: sqlite3.Connection,
    *,
    address_normalized: str,
    listing_url: str | None = None,
    sheet_row_url: str | None = None,
    discord_forum_url: str | None = None,
    status: str = "discovered",
    price: int | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    address_normalized = normalize_address(address_normalized)
    now = datetime.utcnow().isoformat()
    existing = find_property(conn, address=address_normalized, forum_url=discord_forum_url)
    if existing:
        merged_status = existing["status"]
        if STATUS_RANK.get(status, 0) > STATUS_RANK.get(existing["status"], 0):
            merged_status = status
        conn.execute(
            """UPDATE properties SET address_normalized = ?, listing_url = COALESCE(?, listing_url),
               sheet_row_url = COALESCE(?, sheet_row_url),
               discord_forum_url = COALESCE(?, discord_forum_url),
               status = ?, price = COALESCE(?, price), updated_at = ?
               WHERE id = ?""",
            (
                address_normalized,
                listing_url,
                sheet_row_url,
                discord_forum_url,
                merged_status,
                price,
                now,
                existing["id"],
            ),
        )
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (existing["id"],)).fetchone()
    else:
        prop_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO properties (
                id, address_normalized, listing_url, sheet_row_url, discord_forum_url,
                status, price, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                prop_id,
                address_normalized,
                listing_url,
                sheet_row_url,
                discord_forum_url,
                status,
                price,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (prop_id,)).fetchone()
    return dict(row)


def pipeline_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM properties GROUP BY status"
    ).fetchall()
    by_status = {row["status"]: row["count"] for row in rows}
    total = sum(by_status.values())
    return {"total": total, "by_status": by_status}


def summarize_with_llm(*, criteria: dict[str, Any], address: str, thread_text: str, sheet_url: str | None) -> tuple[str, str]:
    base_url, model, headers = _vllm_config()
    must_haves = criteria.get("must_haves") or []
    red_flags = criteria.get("red_flags") or []
    prompt = (
        "You are helping a family evaluate a home purchase in Coquitlam/Port Moody BC.\n\n"
        f"Property: {address}\n"
        f"Must-haves: {', '.join(must_haves)}\n"
        f"Red flags: {', '.join(red_flags)}\n"
    )
    if sheet_url:
        prompt += f"Google Sheet row: {sheet_url} (scores not fetched — use Discord context only)\n"
    prompt += (
        "\nDiscord discussion:\n"
        f"{thread_text[:12000]}\n\n"
        "Write a concise summary: layout/fit notes, family sentiment, red flags vs criteria, "
        "and a recommendation tier (strong / maybe / pass). Keep under 400 words."
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 700,
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return content.strip(), model
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
        fallback = (
            f"**{address}** — LLM unavailable ({exc}).\n\n"
            f"Discussion excerpt ({len(thread_text)} chars):\n{thread_text[:1500]}"
        )
        return fallback, "fallback"


def store_summary(conn: sqlite3.Connection, prop_id: str, summary: str, model: str) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE properties SET ai_summary = ?, updated_at = ? WHERE id = ?",
        (summary, now, prop_id),
    )
    row = conn.execute("SELECT * FROM properties WHERE id = ?", (prop_id,)).fetchone()
    return dict(row)


def resolve_properties_for_compare(
    conn: sqlite3.Connection,
    *,
    property_ids: list[str] | None = None,
    addresses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve 2–4 properties by id or address substring."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pid in property_ids or []:
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (pid,)).fetchone()
        if row and row["id"] not in seen:
            seen.add(row["id"])
            found.append(dict(row))
    for addr in addresses or []:
        needle = addr.strip().lower()
        if not needle:
            continue
        rows = conn.execute("SELECT * FROM properties").fetchall()
        for row in rows:
            if row["id"] in seen:
                continue
            if needle in row["address_normalized"].lower():
                seen.add(row["id"])
                found.append(dict(row))
                break
    if len(found) < 2:
        raise ValueError("need at least 2 properties (by id or address match)")
    if len(found) > 4:
        found = found[:4]
    return found


def compare_with_llm(*, criteria: dict[str, Any], properties: list[dict[str, Any]]) -> tuple[str, str]:
    base_url, model, headers = _vllm_config()
    must_haves = criteria.get("must_haves") or []
    red_flags = criteria.get("red_flags") or []
    lines = []
    for p in properties:
        price = p.get("price")
        price_str = f"${price:,}" if price else "unknown"
        lines.append(
            f"- {p['address_normalized']} | {price_str} | status={p.get('status')} | "
            f"summary={ (p.get('ai_summary') or 'none')[:300] }"
        )
    prompt = (
        "Compare these homes for a family buying in Coquitlam/Port Moody BC.\n\n"
        f"Must-haves: {', '.join(must_haves)}\n"
        f"Red flags: {', '.join(red_flags)}\n\n"
        "Properties:\n"
        + "\n".join(lines)
        + "\n\n"
        "For each property: brief fit vs must-haves, main red flags, tier (strong/maybe/pass). "
        "End with a ranked recommendation (1 best). Under 500 words. Markdown bullets OK."
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 900,
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip(), model
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
        fallback = "**Compare unavailable** — LLM error: {}\n\n".format(exc) + "\n".join(lines)
        return fallback, "fallback"
