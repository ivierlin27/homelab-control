#!/usr/bin/env python3
"""Authenticated local-network chat UI for the executive assistant."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "executive_agent"))

import main as executive  # noqa: E402
from chat_core import handle_chat_turn  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402


DEFAULT_STATE_DIR = Path.home() / ".local/state/homelab-control/agent-executive"


class ChatHandler(BaseHTTPRequestHandler):
    store: ConversationStore
    state_dir: Path
    policy_path: Path
    token: str

    def _authorized(self, form: dict[str, str] | None = None) -> bool:
        if not self.token:
            return True
        query = parse.parse_qs(parse.urlparse(self.path).query)
        provided = self.headers.get("X-Executive-Chat-Token") or (form or {}).get("token") or (query.get("token") or [""])[-1]
        return provided == self.token

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return {key: values[-1] for key, values in parse.parse_qs(raw).items()}

    def _send(self, status: int, body: str, content_type: str = "text/html") -> None:
        rendered = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(403, "Forbidden\n", "text/plain")
            return
        parsed = parse.urlparse(self.path)
        if parsed.path == "/api/conversations":
            self._send(200, json.dumps({"conversations": self.store.list_conversations()}, indent=2) + "\n", "application/json")
            return
        if parsed.path.startswith("/api/conversations/"):
            conversation_id = parse.unquote(parsed.path.removeprefix("/api/conversations/"))
            payload = {
                "conversation": self.store.get_conversation(conversation_id),
                "turns": self.store.list_turns(conversation_id),
            }
            self._send(200, json.dumps(payload, indent=2) + "\n", "application/json")
            return
        token_value = (parse.parse_qs(parsed.query).get("token") or [""])[-1]
        if parsed.path.startswith("/conversation/"):
            conversation_id = parse.unquote(parsed.path.removeprefix("/conversation/"))
            self._send(200, self.render_conversation(conversation_id, token_value))
            return
        self._send(200, self.render_index(token_value))

    def do_POST(self) -> None:  # noqa: N802
        form = self._read_form()
        if not self._authorized(form):
            self._send(403, "Forbidden\n", "text/plain")
            return
        parsed = parse.urlparse(self.path)
        try:
            if parsed.path == "/conversations":
                conversation = self.store.upsert_conversation(
                    conversation_id=form.get("conversation_id") or f"local-web:{uuid.uuid4().hex}",
                    title=form.get("title") or "New conversation",
                    source="local-web",
                    source_ref=form.get("title") or "local-web",
                    owner=form.get("owner", "kevin"),
                    domain=form.get("domain", "homelab"),
                    task_type=form.get("task_type", "research"),
                    plan_ready=form.get("plan_ready") == "on",
                    write_memory=form.get("write_memory") == "on",
                    search_memory=form.get("search_memory", "on") == "on",
                    metadata={"created_by": "local-web"},
                )
                self.redirect(f"/conversation/{parse.quote(conversation['id'])}", form)
                return
            if parsed.path.startswith("/conversation/") and parsed.path.endswith("/message"):
                conversation_id = parse.unquote(parsed.path.removeprefix("/conversation/").removesuffix("/message"))
                conversation = self.store.get_conversation(conversation_id)
                handle_chat_turn(
                    store=self.store,
                    conversation=conversation,
                    message=form.get("message", ""),
                    source="local-web",
                    source_ref=conversation_id,
                    source_user=form.get("owner", "kevin"),
                    metadata={"user_id": form.get("owner", "kevin")},
                    state_dir=self.state_dir,
                    policy_path=self.policy_path,
                    dry_run=form.get("dry_run") == "on",
                )
                self.redirect(f"/conversation/{parse.quote(conversation_id)}", form)
                return
            self._send(404, "Not found\n", "text/plain")
        except Exception as exc:
            self._send(400, f"{exc}\n", "text/plain")

    def redirect(self, path: str, form: dict[str, str]) -> None:
        token = form.get("token", "")
        target = f"{path}?token={parse.quote(token)}" if token else path
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def render_index(self, token_value: str) -> str:
        conversations = self.store.list_conversations()
        rows = "\n".join(
            f"<li><a href=\"/conversation/{parse.quote(item['id'])}?token={html.escape(token_value)}\">"
            f"{html.escape(item['title'])}</a> "
            f"<small>{html.escape(item['source'])} {html.escape(item['domain'])}/{html.escape(item['task_type'])}</small></li>"
            for item in conversations
        )
        return page(
            "Executive Assistant Chat",
            f"""
            <section class="panel">
              <h2>New Conversation</h2>
              <form method="post" action="/conversations">
                {token_input(token_value)}
                <label>Title <input name="title" value="Homelab request"></label>
                <label>Domain <input name="domain" value="homelab"></label>
                <label>Task type <input name="task_type" value="research"></label>
                <label><input type="checkbox" name="search_memory" checked> Search memory</label>
                <label><input type="checkbox" name="write_memory"> Write memory</label>
                <label><input type="checkbox" name="plan_ready"> Allow Plan Ready if policy permits</label>
                <button type="submit">Create</button>
              </form>
            </section>
            <section class="panel">
              <h2>Conversations</h2>
              <ul>{rows or '<li>No conversations yet.</li>'}</ul>
            </section>
            """,
        )

    def render_conversation(self, conversation_id: str, token_value: str) -> str:
        conversation = self.store.get_conversation(conversation_id)
        turns = self.store.list_turns(conversation_id, limit=100)
        rendered_turns = "\n".join(
            f"<article class=\"turn {html.escape(turn['role'])}\"><strong>{html.escape(turn['role'])}</strong>"
            f"<pre>{html.escape(turn['content'])}</pre></article>"
            for turn in turns
        )
        return page(
            conversation["title"],
            f"""
            <p><a href="/?token={html.escape(token_value)}">Back to conversations</a></p>
            <section class="panel">
              <h2>{html.escape(conversation['title'])}</h2>
              <p>{html.escape(conversation['domain'])} / {html.escape(conversation['task_type'])}
              source={html.escape(conversation['source'])}</p>
            </section>
            <section>{rendered_turns or '<p>No turns yet.</p>'}</section>
            <section class="panel">
              <form method="post" action="/conversation/{html.escape(parse.quote(conversation_id))}/message">
                {token_input(token_value)}
                <textarea name="message" rows="5" placeholder="Ask the assistant..."></textarea>
                <label><input type="checkbox" name="dry_run" checked> Dry run</label>
                <button type="submit">Send</button>
              </form>
            </section>
            """,
        )


def token_input(token_value: str) -> str:
    return f'<input type="hidden" name="token" value="{html.escape(token_value)}">' if token_value else ""


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e5e7eb; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 1rem; }}
    a {{ color: #93c5fd; }}
    label {{ display: block; margin: .5rem 0; }}
    input, textarea {{ width: 100%; box-sizing: border-box; padding: .5rem; background: #111827; color: #e5e7eb; border: 1px solid #374151; }}
    input[type=checkbox] {{ width: auto; }}
    button {{ padding: .5rem .75rem; }}
    .panel {{ border: 1px solid #334155; background: #111827; border-radius: .75rem; padding: 1rem; margin: 1rem 0; }}
    .turn {{ border-radius: .75rem; padding: .75rem; margin: .75rem 0; }}
    .user {{ background: #1e293b; }}
    .assistant {{ background: #172554; }}
    pre {{ white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  </style>
</head>
<body><main><h1>{html.escape(title)}</h1>{body}</main></body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("EXECUTIVE_CHAT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EXECUTIVE_CHAT_PORT", "8767")))
    parser.add_argument("--state-dir", default=os.environ.get("EXECUTIVE_STATE_DIR", str(DEFAULT_STATE_DIR)))
    parser.add_argument("--db", default=os.environ.get("EXECUTIVE_CHAT_DB", ""))
    parser.add_argument("--policy", default=os.environ.get("EXECUTIVE_POLICY", str(executive.DEFAULT_POLICY)))
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser()
    db_path = Path(args.db).expanduser() if args.db else state_dir / "conversations.sqlite3"
    ChatHandler.store = ConversationStore(db_path)
    ChatHandler.state_dir = state_dir
    ChatHandler.policy_path = Path(args.policy).expanduser()
    ChatHandler.token = os.environ.get("EXECUTIVE_CHAT_TOKEN", "")
    server = ThreadingHTTPServer((args.host, args.port), ChatHandler)
    print(f"executive chat server listening on {args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
