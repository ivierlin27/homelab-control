"""Move command center — homelab MVP API + LAN dashboard."""

from __future__ import annotations

import os
import sqlite3
import urllib.error
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from discover_forum import discover_forum
from digest import build_digest, format_digest_text
from house_intake import intake_from_listing
from listing_parse import parse_listing_url
from properties import (
    VALID_STATUSES,
    compare_with_llm,
    dedupe_properties,
    find_property,
    normalize_address,
    pipeline_counts,
    resolve_properties_for_compare,
    store_summary,
    summarize_with_llm,
    upsert_property,
)
from sheet_intake import post_status_update
from sheet_links import fetch_sheet_index, sheet_url_for_property
from temp_housing import (
    VALID_TEMP_STATUSES,
    compare_temp_with_llm,
    evaluate_temp_with_llm,
    intake_from_url_with_llm,
    resolve_candidates,
    row_to_dict as temp_row_to_dict,
)

DATA_DIR = Path(os.environ.get("MOVE_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "move.db"
SEED_FILE = Path(os.environ.get("MOVE_SEED_FILE", "/app/seed.yaml"))

app = FastAPI(title="Move Command Center", version="0.4.6")

CRITERIA_CACHE: dict[str, Any] = {}
TEMP_HOUSING_REQS: dict[str, Any] = {}
MOVE_CHECKLIST: list[dict[str, Any]] = []
DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


@contextmanager
def db_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS milestones (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                date TEXT NOT NULL,
                workstream TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workstream TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'todo',
                due_date TEXT,
                priority TEXT NOT NULL DEFAULT 'normal',
                blocked_by TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                workstream TEXT NOT NULL,
                mitigation TEXT
            );
            CREATE TABLE IF NOT EXISTS properties (
                id TEXT PRIMARY KEY,
                address_normalized TEXT NOT NULL,
                listing_url TEXT,
                sheet_row_url TEXT,
                discord_forum_url TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                price INTEGER,
                ai_summary TEXT,
                red_flags TEXT,
                decision TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS temp_housing (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                listing_url TEXT,
                monthly_cost INTEGER,
                parking INTEGER NOT NULL DEFAULT 0,
                dog_ok INTEGER NOT NULL DEFAULT 0,
                transit_to_sfu_notes TEXT,
                available_from TEXT,
                available_to TEXT,
                status TEXT NOT NULL DEFAULT 'researching',
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS external_links (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS checklist_done (
                item_key TEXT PRIMARY KEY,
                done_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                question TEXT NOT NULL,
                outcome TEXT NOT NULL,
                rationale TEXT,
                workstream TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def seed_if_empty() -> None:
    with db_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]:
            return
    if not SEED_FILE.exists():
        return
    data = yaml.safe_load(SEED_FILE.read_text()) or {}
    now = datetime.utcnow().isoformat()
    with db_conn() as conn:
        for m in data.get("milestones", []):
            conn.execute(
                "INSERT INTO milestones (id, label, date, workstream, notes) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), m["label"], m["date"], m["workstream"], m.get("notes")),
            )
        for r in data.get("risks", []):
            conn.execute(
                "INSERT INTO risks (id, title, severity, status, workstream, mitigation) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    r["title"],
                    r["severity"],
                    r.get("status", "open"),
                    r["workstream"],
                    r.get("mitigation"),
                ),
            )
        for t in data.get("tasks", []):
            conn.execute(
                """INSERT INTO tasks (id, title, workstream, status, due_date, priority, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    t["title"],
                    t["workstream"],
                    t.get("status", "todo"),
                    t.get("due_date"),
                    t.get("priority", "normal"),
                    now,
                    now,
                ),
            )
        for key, value in (data.get("external_links") or {}).items():
            if value:
                conn.execute(
                    "INSERT OR REPLACE INTO external_links (key, value) VALUES (?, ?)",
                    (key, value),
                )


@app.on_event("startup")
def startup() -> None:
    init_db()
    seed_if_empty()
    _load_criteria_cache()


def _load_criteria_cache() -> None:
    global CRITERIA_CACHE, MOVE_CHECKLIST, TEMP_HOUSING_REQS
    if SEED_FILE.exists():
        data = yaml.safe_load(SEED_FILE.read_text()) or {}
        CRITERIA_CACHE = data.get("criteria_cache") or {}
        MOVE_CHECKLIST = data.get("move_checklist") or []
        TEMP_HOUSING_REQS = data.get("temp_housing_requirements") or {}
    with db_conn() as conn:
        links = {
            "discord_channel": "https://discord.com/channels/539859908691099672/1405377958570627145",
            "discord_forum": "https://discord.com/channels/539859908691099672/1516955162974097430",
        }
        for key, value in links.items():
            conn.execute(
                "INSERT OR REPLACE INTO external_links (key, value) VALUES (?, ?)",
                (key, value),
            )


class TaskIn(BaseModel):
    title: str
    workstream: str = "move"
    status: str = "todo"
    due_date: str | None = None
    priority: str = "normal"
    blocked_by: str | None = None
    notes: str | None = None


class TaskPatch(BaseModel):
    status: str | None = None
    due_date: str | None = None
    priority: str | None = None
    blocked_by: str | None = None
    notes: str | None = None


class PropertyIn(BaseModel):
    address: str
    listing_url: str | None = None
    sheet_url: str | None = None
    forum_url: str | None = None
    status: str = "discovered"
    price: int | None = None


class PropertyPatch(BaseModel):
    status: str | None = None
    listing_url: str | None = None
    sheet_url: str | None = None
    forum_url: str | None = None
    price: int | None = None
    decision: str | None = None


class SummarizeIn(BaseModel):
    thread_text: str
    sheet_notes: str | None = None


class IntakeIn(BaseModel):
    listing_url: str
    mls: str | None = None
    neighborhood: str | None = None
    notes: str | None = None
    skip_sheet: bool = False
    create_forum: bool = True


class ForumStatusIn(BaseModel):
    forum_url: str
    status: str


class TempHousingIn(BaseModel):
    name: str
    listing_url: str | None = None
    monthly_cost: int | None = None
    parking: bool = False
    dog_ok: bool = False
    transit_to_sfu_notes: str | None = None
    available_from: str | None = None
    available_to: str | None = None
    status: str = "researching"
    notes: str | None = None


class TempHousingPatch(BaseModel):
    name: str | None = None
    listing_url: str | None = None
    monthly_cost: int | None = None
    parking: bool | None = None
    dog_ok: bool | None = None
    transit_to_sfu_notes: str | None = None
    available_from: str | None = None
    available_to: str | None = None
    status: str | None = None
    notes: str | None = None


class TempHousingCompareIn(BaseModel):
    item_ids: list[str] = Field(..., min_length=2, max_length=4)


class TempHousingEvaluateIn(BaseModel):
    notes: str | None = None


class TempHousingIntakeUrlIn(BaseModel):
    listing_url: str
    save: bool = False


class DecisionIn(BaseModel):
    question: str
    outcome: str
    rationale: str | None = None
    workstream: str | None = None
    date: str | None = None


class CompareIn(BaseModel):
    property_ids: list[str] | None = None
    addresses: list[str] | None = None


def _row_from_sheet_url(sheet_row_url: str | None) -> int | None:
    if not sheet_row_url:
        return None
    import re

    m = re.search(r"range=(\d+)", sheet_row_url)
    return int(m.group(1)) if m else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def days_until(d: str) -> int:
    return (date.fromisoformat(d) - date.today()).days


def _workstream_health(conn: sqlite3.Connection) -> dict[str, Any]:
    health: dict[str, Any] = {}
    for ws in ("sell", "move", "temp_housing", "buy"):
        open_count = conn.execute(
            """SELECT COUNT(*) FROM tasks
               WHERE workstream = ? AND status NOT IN ('done', 'cancelled')""",
            (ws,),
        ).fetchone()[0]
        waiting = conn.execute(
            """SELECT title FROM tasks
               WHERE workstream = ? AND status = 'waiting'
               ORDER BY updated_at DESC LIMIT 1""",
            (ws,),
        ).fetchone()
        health[ws] = {
            "open_tasks": open_count,
            "top_blocker": waiting[0] if waiting else None,
        }
    health["buy"]["properties"] = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    return health


@app.get("/api/v1/status")
def status() -> dict[str, Any]:
    with db_conn() as conn:
        milestones = rows_to_dicts(conn.execute("SELECT * FROM milestones ORDER BY date").fetchall())
        tasks = rows_to_dicts(
            conn.execute(
                """SELECT * FROM tasks WHERE status NOT IN ('done', 'cancelled')
                   ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                   due_date IS NULL, due_date LIMIT 5"""
            ).fetchall()
        )
        waiting = rows_to_dicts(
            conn.execute("SELECT * FROM tasks WHERE status = 'waiting' ORDER BY updated_at DESC").fetchall()
        )
        risks = rows_to_dicts(
            conn.execute(
                "SELECT * FROM risks WHERE status = 'open' AND severity IN ('critical', 'high') ORDER BY severity"
            ).fetchall()
        )
        links = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM external_links").fetchall()}
        recent_properties = rows_to_dicts(
            conn.execute("SELECT * FROM properties ORDER BY updated_at DESC LIMIT 5").fetchall()
        )
        workstream_health = _workstream_health(conn)
    for m in milestones:
        m["days_until"] = days_until(m["date"])
    return {
        "milestones": milestones,
        "next_tasks": tasks,
        "waiting": waiting,
        "risks": risks,
        "external_links": links,
        "workstream_health": workstream_health,
        "recent_properties": recent_properties,
    }


@app.get("/api/v1/tasks")
def list_tasks(workstream: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM tasks WHERE 1=1"
    params: list[Any] = []
    if workstream:
        q += " AND workstream = ?"
        params.append(workstream)
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY due_date IS NULL, due_date, title"
    with db_conn() as conn:
        return rows_to_dicts(conn.execute(q, params).fetchall())


@app.post("/api/v1/tasks")
def create_task(body: TaskIn) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    task_id = str(uuid.uuid4())
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO tasks (id, title, workstream, status, due_date, priority, blocked_by, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                body.title,
                body.workstream,
                body.status,
                body.due_date,
                body.priority,
                body.blocked_by,
                body.notes,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row)


@app.patch("/api/v1/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "no fields to update")
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with db_conn() as conn:
        cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", [*fields.values(), task_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "task not found")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row)


@app.get("/api/v1/today")
def today() -> dict[str, Any]:
    today_str = date.today().isoformat()
    with db_conn() as conn:
        overdue = rows_to_dicts(
            conn.execute(
                """SELECT * FROM tasks WHERE status NOT IN ('done', 'cancelled')
                   AND due_date IS NOT NULL AND due_date < ?
                   ORDER BY due_date, priority""",
                (today_str,),
            ).fetchall()
        )
        due_today = rows_to_dicts(
            conn.execute(
                """SELECT * FROM tasks WHERE status NOT IN ('done', 'cancelled')
                   AND due_date = ?
                   ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END""",
                (today_str,),
            ).fetchall()
        )
        critical_open = rows_to_dicts(
            conn.execute(
                """SELECT * FROM tasks WHERE status NOT IN ('done', 'cancelled')
                   AND priority = 'critical'
                   ORDER BY due_date IS NULL, due_date LIMIT 5"""
            ).fetchall()
        )
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in overdue + due_today + critical_open:
        if task["id"] not in seen:
            seen.add(task["id"])
            combined.append(task)
        if len(combined) >= 5:
            break
    return {"date": today_str, "tasks": combined, "overdue_count": len(overdue)}


@app.get("/api/v1/milestones")
def list_milestones() -> list[dict[str, Any]]:
    with db_conn() as conn:
        items = rows_to_dicts(conn.execute("SELECT * FROM milestones ORDER BY date").fetchall())
    for m in items:
        m["days_until"] = days_until(m["date"])
    return items


@app.get("/api/v1/risks")
def list_risks() -> list[dict[str, Any]]:
    with db_conn() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM risks ORDER BY severity, title").fetchall())


@app.get("/api/v1/properties")
def list_properties(status: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM properties WHERE 1=1"
    params: list[Any] = []
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY updated_at DESC"
    with db_conn() as conn:
        return rows_to_dicts(conn.execute(q, params).fetchall())


@app.get("/api/v1/properties/{property_id}")
def get_property(property_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not row:
        raise HTTPException(404, "property not found")
    return dict(row)


@app.post("/api/v1/properties")
def create_property(body: PropertyIn) -> dict[str, Any]:
    with db_conn() as conn:
        try:
            sheet_url = body.sheet_url
            if not sheet_url and os.environ.get("MOVE_SHEET_INDEX_URL"):
                try:
                    index = fetch_sheet_index()
                    base_row = conn.execute(
                        "SELECT value FROM external_links WHERE key = 'google_sheet'"
                    ).fetchone()
                    base = base_row["value"] if base_row else None
                    sheet_url = sheet_url_for_property(
                        address=body.address,
                        index=index,
                        base_sheet_url=base,
                    )
                except RuntimeError:
                    pass
            return upsert_property(
                conn,
                address_normalized=body.address,
                listing_url=body.listing_url,
                sheet_row_url=sheet_url,
                discord_forum_url=body.forum_url,
                status=body.status,
                price=body.price,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.patch("/api/v1/properties/{property_id}")
def patch_property(property_id: str, body: PropertyPatch) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        raise HTTPException(400, "invalid status")
    rename = {"sheet_url": "sheet_row_url", "forum_url": "discord_forum_url"}
    mapped = {rename.get(k, k): v for k, v in fields.items()}
    mapped["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in mapped)
    with db_conn() as conn:
        cur = conn.execute(
            f"UPDATE properties SET {set_clause} WHERE id = ?",
            [*mapped.values(), property_id],
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "property not found")
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    result = dict(row)
    sheet_row = _row_from_sheet_url(result.get("sheet_row_url"))
    if sheet_row and (mapped.get("discord_forum_url") or mapped.get("status")):
        post_status_update(
            row=sheet_row,
            status=mapped.get("status") or result.get("status") or "discovered",
            discord_forum_url=mapped.get("discord_forum_url") or result.get("discord_forum_url"),
        )
    return result


@app.post("/api/v1/house/dedupe")
def house_dedupe() -> dict[str, Any]:
    """Remove duplicate property board entries (same address / street key)."""
    with db_conn() as conn:
        return dedupe_properties(conn)


@app.post("/api/v1/house/intake")
def house_intake(body: IntakeIn) -> dict[str, Any]:
    """Parse listing URL, append Properties row (Apps Script), upsert homelab property."""
    with db_conn() as conn:
        try:
            return intake_from_listing(
                conn,
                listing_url=body.listing_url,
                mls=body.mls,
                neighborhood=body.neighborhood,
                notes=body.notes,
                skip_sheet=body.skip_sheet,
                create_forum=body.create_forum,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc


@app.post("/api/v1/house/status-by-forum")
def house_status_by_forum(body: ForumStatusIn) -> dict[str, Any]:
    """Update property pipeline status from Discord forum URL (emoji reactions)."""
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"invalid status: {body.status}")
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM properties WHERE discord_forum_url = ?",
            (body.forum_url,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "property not found for forum URL")
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE properties SET status = ?, updated_at = ? WHERE id = ?",
            (body.status, now, row["id"]),
        )
        updated = conn.execute("SELECT * FROM properties WHERE id = ?", (row["id"],)).fetchone()
        sheet_row = _row_from_sheet_url(updated["sheet_row_url"])
        sheet_sync = {}
        if sheet_row:
            sheet_sync = post_status_update(
                row=sheet_row,
                status=body.status,
                discord_forum_url=body.forum_url,
            )
    return {"property": dict(updated), "sheet_sync": sheet_sync}


@app.post("/api/v1/house/discover-forum")
def house_discover_forum() -> dict[str, Any]:
    """Backfill active forum threads into the property index (requires MOVE_DISCORD_BOT_TOKEN)."""
    token = os.environ.get("MOVE_DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            503,
            "MOVE_DISCORD_BOT_TOKEN not configured on this host — run discover script from Alienware",
        )
    with db_conn() as conn:
        try:
            return discover_forum(conn, token, link_sheet=True)
        except urllib.error.HTTPError as exc:
            raise HTTPException(502, f"Discord API error: {exc}") from exc


@app.post("/api/v1/house/link-sheet")
def house_link_sheet() -> dict[str, Any]:
    """Match properties to Google Sheet rows via MOVE_SHEET_INDEX_URL (Apps Script web app)."""
    try:
        index = fetch_sheet_index()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not index:
        raise HTTPException(
            503,
            "MOVE_SHEET_INDEX_URL not set or returned no rows — deploy apps-script/house-sheet-index.gs",
        )
    with db_conn() as conn:
        base_sheet_row = conn.execute(
            "SELECT value FROM external_links WHERE key = 'google_sheet'"
        ).fetchone()
        base_sheet = base_sheet_row["value"] if base_sheet_row else None
        props = conn.execute("SELECT * FROM properties").fetchall()
        linked = 0
        results: list[dict[str, Any]] = []
        now = datetime.utcnow().isoformat()
        for prop in props:
            url = sheet_url_for_property(
                address=prop["address_normalized"],
                index=index,
                base_sheet_url=base_sheet,
            )
            if url and url != prop["sheet_row_url"]:
                conn.execute(
                    "UPDATE properties SET sheet_row_url = ?, updated_at = ? WHERE id = ?",
                    (url, now, prop["id"]),
                )
                linked += 1
            results.append(
                {
                    "address": prop["address_normalized"],
                    "sheet_row_url": url or prop["sheet_row_url"],
                }
            )
    return {"linked": linked, "total": len(results), "properties": results}


@app.get("/api/v1/house/status")
def house_status() -> dict[str, Any]:
    with db_conn() as conn:
        pipeline = pipeline_counts(conn)
        recent = rows_to_dicts(
            conn.execute("SELECT * FROM properties ORDER BY updated_at DESC LIMIT 5").fetchall()
        )
        links = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM external_links").fetchall()}
    return {"pipeline": pipeline, "recent": recent, "external_links": links}


@app.post("/api/v1/properties/{property_id}/summarize")
def summarize_property(property_id: str, body: SummarizeIn) -> dict[str, Any]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not row:
            raise HTTPException(404, "property not found")
        text = body.thread_text
        if body.sheet_notes:
            text += f"\n\nSheet notes:\n{body.sheet_notes}"
        summary, model = summarize_with_llm(
            criteria=CRITERIA_CACHE,
            address=row["address_normalized"],
            thread_text=text,
            sheet_url=row["sheet_row_url"],
        )
        return store_summary(conn, property_id, summary, model)


@app.post("/api/v1/house/compare")
def house_compare(body: CompareIn) -> dict[str, Any]:
    """Compare 2–4 properties via local vLLM against search criteria."""
    if not body.property_ids and not body.addresses:
        raise HTTPException(400, "property_ids or addresses required")
    with db_conn() as conn:
        try:
            props = resolve_properties_for_compare(
                conn,
                property_ids=body.property_ids,
                addresses=body.addresses,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        comparison, model = compare_with_llm(criteria=CRITERIA_CACHE, properties=props)
    return {
        "properties": [{"id": p["id"], "address_normalized": p["address_normalized"], "price": p.get("price"), "status": p.get("status")} for p in props],
        "comparison": comparison,
        "model": model,
    }


@app.get("/api/v1/decisions")
def list_decisions() -> list[dict[str, Any]]:
    with db_conn() as conn:
        return rows_to_dicts(
            conn.execute("SELECT * FROM decisions ORDER BY date DESC, created_at DESC").fetchall()
        )


@app.post("/api/v1/decisions")
def create_decision(body: DecisionIn) -> dict[str, Any]:
    decision_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    decision_date = body.date or date.today().isoformat()
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO decisions (id, date, question, outcome, rationale, workstream, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                decision_date,
                body.question,
                body.outcome,
                body.rationale,
                body.workstream,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return dict(row)


@app.get("/api/v1/timeline")
def timeline_events(months: int = Query(3, ge=1, le=6)) -> dict[str, Any]:
    """Milestones + dated tasks for calendar views."""
    today = date.today()
    end = today.replace(day=1)
    for _ in range(months):
        if end.month == 12:
            end = end.replace(year=end.year + 1, month=1)
        else:
            end = end.replace(month=end.month + 1)
    end_str = end.isoformat()
    start_str = today.replace(day=1).isoformat()
    with db_conn() as conn:
        milestones = rows_to_dicts(conn.execute("SELECT * FROM milestones ORDER BY date").fetchall())
        tasks = rows_to_dicts(
            conn.execute(
                """SELECT id, title, workstream, status, due_date FROM tasks
                   WHERE due_date IS NOT NULL AND due_date >= ? AND due_date < ?
                   ORDER BY due_date""",
                (start_str, end_str),
            ).fetchall()
        )
    events: list[dict[str, Any]] = []
    for m in milestones:
        events.append({"date": m["date"], "label": m["label"], "kind": "milestone", "workstream": m.get("workstream")})
    for t in tasks:
        events.append({"date": t["due_date"], "label": t["title"], "kind": "task", "workstream": t.get("workstream"), "status": t.get("status")})
    events.sort(key=lambda e: e["date"])
    return {"start": start_str, "end": end_str, "events": events}


@app.get("/api/v1/temp-housing")
def list_temp_housing() -> list[dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM temp_housing ORDER BY monthly_cost").fetchall()
        return [temp_row_to_dict(r) for r in rows]


@app.get("/api/v1/temp-housing/requirements")
def get_temp_housing_requirements() -> dict[str, Any]:
    return TEMP_HOUSING_REQS


@app.post("/api/v1/temp-housing/intake-url")
def temp_housing_intake_url(body: TempHousingIntakeUrlIn) -> dict[str, Any]:
    """Fetch any rental URL and LLM-extract fields; optionally save as candidate."""
    parsed, model, fetch_status = intake_from_url_with_llm(
        listing_url=body.listing_url,
        requirements=TEMP_HOUSING_REQS,
    )
    result: dict[str, Any] = {
        "parsed": parsed,
        "model": model,
        "fetch_status": fetch_status,
    }
    if body.save:
        create_body = TempHousingIn(
            name=parsed["name"],
            listing_url=parsed.get("listing_url"),
            monthly_cost=parsed.get("monthly_cost"),
            parking=bool(parsed.get("parking")),
            dog_ok=bool(parsed.get("dog_ok")),
            transit_to_sfu_notes=parsed.get("transit_to_sfu_notes"),
            available_from=parsed.get("available_from"),
            available_to=parsed.get("available_to"),
            status=parsed.get("status", "researching"),
            notes=parsed.get("notes"),
        )
        result["candidate"] = create_temp_housing(create_body)
    return result


@app.get("/api/v1/temp-housing/{item_id}")
def get_temp_housing_item(item_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM temp_housing WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "candidate not found")
        return temp_row_to_dict(row)


@app.post("/api/v1/temp-housing")
def create_temp_housing(body: TempHousingIn) -> dict[str, Any]:
    if body.status not in VALID_TEMP_STATUSES:
        raise HTTPException(400, f"invalid status: {body.status}")
    item_id = str(uuid.uuid4())
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO temp_housing
               (id, name, listing_url, monthly_cost, parking, dog_ok, transit_to_sfu_notes,
                available_from, available_to, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                body.name,
                body.listing_url,
                body.monthly_cost,
                int(body.parking),
                int(body.dog_ok),
                body.transit_to_sfu_notes,
                body.available_from,
                body.available_to,
                body.status,
                body.notes,
            ),
        )
        row = conn.execute("SELECT * FROM temp_housing WHERE id = ?", (item_id,)).fetchone()
    return temp_row_to_dict(row)


@app.patch("/api/v1/temp-housing/{item_id}")
def patch_temp_housing(item_id: str, body: TempHousingPatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "no fields to update")
    if "status" in fields and fields["status"] not in VALID_TEMP_STATUSES:
        raise HTTPException(400, f"invalid status: {fields['status']}")
    for key in ("parking", "dog_ok"):
        if key in fields:
            fields[key] = int(fields[key])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with db_conn() as conn:
        cur = conn.execute(f"UPDATE temp_housing SET {set_clause} WHERE id = ?", [*fields.values(), item_id])
        if cur.rowcount == 0:
            raise HTTPException(404, "candidate not found")
        row = conn.execute("SELECT * FROM temp_housing WHERE id = ?", (item_id,)).fetchone()
    return temp_row_to_dict(row)


@app.delete("/api/v1/temp-housing/{item_id}")
def delete_temp_housing(item_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        cur = conn.execute("DELETE FROM temp_housing WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "candidate not found")
    return {"ok": True, "id": item_id}


@app.post("/api/v1/temp-housing/compare")
def compare_temp_housing(body: TempHousingCompareIn) -> dict[str, Any]:
    with db_conn() as conn:
        try:
            candidates = resolve_candidates(conn, item_ids=body.item_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        comparison, model = compare_temp_with_llm(requirements=TEMP_HOUSING_REQS, candidates=candidates)
    return {
        "candidates": [{"id": c["id"], "name": c["name"], "monthly_cost": c.get("monthly_cost"), "status": c.get("status")} for c in candidates],
        "comparison": comparison,
        "model": model,
    }


@app.post("/api/v1/temp-housing/{item_id}/evaluate")
def evaluate_temp_housing(item_id: str, body: TempHousingEvaluateIn | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM temp_housing WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "candidate not found")
        candidate = temp_row_to_dict(row)
        extra = body.notes if body else None
        evaluation, model = evaluate_temp_with_llm(
            requirements=TEMP_HOUSING_REQS,
            candidate=candidate,
            extra_notes=extra,
        )
    return {"candidate": {"id": candidate["id"], "name": candidate["name"]}, "evaluation": evaluation, "model": model}


@app.get("/api/v1/criteria")
def get_criteria() -> dict[str, Any]:
    return CRITERIA_CACHE


@app.get("/api/v1/checklist/move")
def get_move_checklist() -> dict[str, Any]:
    with db_conn() as conn:
        done = {
            r["item_key"]
            for r in conn.execute("SELECT item_key FROM checklist_done").fetchall()
        }
    groups: list[dict[str, Any]] = []
    for group in MOVE_CHECKLIST:
        items = []
        for idx, label in enumerate(group.get("items") or []):
            key = f"{group['group']}:{idx}"
            items.append({"key": key, "label": label, "done": key in done})
        groups.append({"group": group["group"], "items": items})
    return {"groups": groups}


@app.patch("/api/v1/checklist/move/{item_key:path}")
def toggle_checklist_item(item_key: str, done: bool = Query(...)) -> dict[str, Any]:
    with db_conn() as conn:
        if done:
            conn.execute(
                "INSERT OR REPLACE INTO checklist_done (item_key, done_at) VALUES (?, ?)",
                (item_key, datetime.utcnow().isoformat()),
            )
        else:
            conn.execute("DELETE FROM checklist_done WHERE item_key = ?", (item_key,))
    return {"item_key": item_key, "done": done}


@app.get("/api/v1/export/vault")
def export_vault() -> dict[str, Any]:
    generated = datetime.utcnow().isoformat()
    with db_conn() as conn:
        milestones = rows_to_dicts(conn.execute("SELECT * FROM milestones ORDER BY date").fetchall())
        tasks = rows_to_dicts(
            conn.execute(
                """SELECT * FROM tasks WHERE status NOT IN ('done', 'cancelled')
                   ORDER BY workstream, due_date IS NULL, due_date"""
            ).fetchall()
        )
        risks = rows_to_dicts(conn.execute("SELECT * FROM risks WHERE status = 'open' ORDER BY severity").fetchall())
        properties = rows_to_dicts(conn.execute("SELECT * FROM properties ORDER BY updated_at DESC").fetchall())
        temp_housing = rows_to_dicts(conn.execute("SELECT * FROM temp_housing ORDER BY monthly_cost").fetchall())
        decisions = rows_to_dicts(conn.execute("SELECT * FROM decisions ORDER BY date DESC").fetchall())
    lines = [
        "---",
        "title: Move command center snapshot",
        f"exported: {generated[:10]}",
        "type: note",
        "tags: [project, personal, housing, export]",
        "---",
        "",
        f"# Move snapshot ({generated[:10]})",
        "",
        "## Critical dates",
        "",
    ]
    for m in milestones:
        lines.append(f"- **{m['date']}** — {m['label']} ({m['workstream']})")
    lines.extend(["", "## Open tasks", ""])
    for t in tasks:
        due = f", due {t['due_date']}" if t.get("due_date") else ""
        lines.append(f"- [{t['workstream']}] {t['title']} ({t['status']}{due})")
    lines.extend(["", "## Property pipeline", ""])
    for p in properties:
        lines.append(f"- **{p['address_normalized']}** — {p['status']}")
        if p.get("listing_url"):
            lines.append(f"  - Listing: {p['listing_url']}")
        if p.get("sheet_row_url"):
            lines.append(f"  - Sheet: {p['sheet_row_url']}")
    lines.extend(["", "## Open risks", ""])
    for r in risks:
        lines.append(f"- [{r['severity']}] {r['title']}: {r.get('mitigation') or ''}")
    if temp_housing:
        lines.extend(["", "## Temp housing candidates", ""])
        for c in temp_housing:
            cost = f"${c['monthly_cost']}/mo" if c.get("monthly_cost") else "cost TBD"
            lines.append(f"- {c['name']} — {cost} ({c['status']})")
    if decisions:
        lines.extend(["", "## Decision log", ""])
        for d in decisions:
            lines.append(f"- **{d['date']}** — {d['question']}: {d['outcome']}")
            if d.get("rationale"):
                lines.append(f"  - {d['rationale']}")
    markdown = "\n".join(lines) + "\n"
    return {"markdown": markdown, "generated_at": generated}


@app.get("/api/v1/digest")
def daily_digest() -> dict[str, Any]:
    """Daily summary for Discord digest (milestones, overdue, risks, pipeline)."""
    dashboard_url = os.environ.get("MOVE_DASHBOARD_URL", "https://move.dev-path.org")
    with db_conn() as conn:
        data = build_digest(conn)
    return {
        **data,
        "text": format_digest_text(data, dashboard_url=dashboard_url),
    }


def _load_dashboard_html() -> str:
    if DASHBOARD_PATH.exists():
        return DASHBOARD_PATH.read_text()
    return "<html><body><p>dashboard.html missing</p></body></html>"


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(_load_dashboard_html())


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True})
