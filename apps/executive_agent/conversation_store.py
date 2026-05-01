#!/usr/bin/env python3
"""SQLite conversation storage for executive assistant chat surfaces."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT 'homelab',
                    task_type TEXT NOT NULL DEFAULT 'research',
                    plan_ready INTEGER NOT NULL DEFAULT 0,
                    write_memory INTEGER NOT NULL DEFAULT 0,
                    search_memory INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                );

                CREATE INDEX IF NOT EXISTS idx_turns_conversation_created
                    ON turns(conversation_id, created_at);
                """
            )

    def upsert_conversation(
        self,
        *,
        conversation_id: str,
        title: str,
        source: str,
        source_ref: str,
        owner: str = "",
        domain: str = "homelab",
        task_type: str = "research",
        plan_ready: bool = False,
        write_memory: bool = False,
        search_memory: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self.connect() as conn:
            existing = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE conversations
                    SET title = ?, source = ?, source_ref = ?, owner = ?, domain = ?,
                        task_type = ?, plan_ready = ?, write_memory = ?, search_memory = ?,
                        metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        source,
                        source_ref,
                        owner,
                        domain,
                        task_type,
                        int(plan_ready),
                        int(write_memory),
                        int(search_memory),
                        metadata_json,
                        now,
                        conversation_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO conversations (
                        id, title, source, source_ref, owner, domain, task_type,
                        plan_ready, write_memory, search_memory, metadata_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        title,
                        source,
                        source_ref,
                        owner,
                        domain,
                        task_type,
                        int(plan_ready),
                        int(write_memory),
                        int(search_memory),
                        metadata_json,
                        now,
                        now,
                    ),
                )
        return self.get_conversation(conversation_id)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise KeyError(f"conversation not found: {conversation_id}")
        return self._conversation_from_row(row)

    def conversation_for_source(
        self,
        *,
        source: str,
        source_ref: str,
        default_title: str,
        owner: str = "",
        domain: str = "homelab",
        task_type: str = "research",
        plan_ready: bool = False,
        write_memory: bool = False,
        search_memory: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE source = ? AND source_ref = ?",
                (source, source_ref),
            ).fetchone()
        if row:
            return self._conversation_from_row(row)
        conversation_id = f"{source}:{source_ref}".replace("/", "-")
        return self.upsert_conversation(
            conversation_id=conversation_id,
            title=default_title,
            source=source,
            source_ref=source_ref,
            owner=owner,
            domain=domain,
            task_type=task_type,
            plan_ready=plan_ready,
            write_memory=write_memory,
            search_memory=search_memory,
            metadata=metadata,
        )

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def add_turn(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO turns(conversation_id, role, content, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, json.dumps(result or {}, sort_keys=True), now),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            turn_id = cursor.lastrowid
        return {
            "id": turn_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "result": result or {},
            "created_at": now,
        }

    def list_turns(self, conversation_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM turns
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [self._turn_from_row(row) for row in reversed(rows)]

    def _conversation_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "source": row["source"],
            "source_ref": row["source_ref"],
            "owner": row["owner"],
            "domain": row["domain"],
            "task_type": row["task_type"],
            "plan_ready": bool(row["plan_ready"]),
            "write_memory": bool(row["write_memory"]),
            "search_memory": bool(row["search_memory"]),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _turn_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "content": row["content"],
            "result": json.loads(row["result_json"] or "{}"),
            "created_at": row["created_at"],
        }
