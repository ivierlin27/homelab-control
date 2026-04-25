#!/usr/bin/env python3
"""Small web UI for inspecting and controlling homelab agent activity."""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import parse


QUEUE_NAMES = {"author": "agent-homelab", "review": "agent-review"}
SERVICE_NAMES = {
    "author": "alienware-author-agent.service",
    "review": "alienware-review-agent.service",
    "dispatcher": "alienware-agent-event-dispatcher.service",
    "report": "alienware-agent-platform-report.service",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def queue_root(state_dir: Path, queue: str) -> Path:
    if queue not in QUEUE_NAMES:
        raise ValueError(f"unknown queue: {queue}")
    return state_dir / QUEUE_NAMES[queue]


def queue_files(root: Path, folder: str) -> list[dict[str, Any]]:
    path = root / folder
    files = sorted(path.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    items = []
    for file in files[:50]:
        payload = load_json(file)
        items.append(
            {
                "name": file.name,
                "path": str(file),
                "mtime": datetime.fromtimestamp(file.stat().st_mtime, UTC).isoformat(),
                "action": payload.get("action", ""),
                "title": payload.get("title", payload.get("error", "")),
                "pr_url": payload.get("pr_url", ""),
                "decision": payload.get("decision", ""),
            }
        )
    return items


def build_snapshot(state_dir: Path) -> dict[str, Any]:
    platform_status = load_json(state_dir / "platform-status.json")
    queues: dict[str, Any] = {}
    for name in QUEUE_NAMES:
        root = queue_root(state_dir, name)
        queues[name] = {
            "heartbeat": load_json(root / "heartbeat.json"),
            "inbox": queue_files(root, "inbox"),
            "processing": queue_files(root, "processing"),
            "failed": queue_files(root, "failed"),
            "recent_done": queue_files(root, "done")[:10],
        }
    return {"generated_at": utc_now(), "platform_status": platform_status, "queues": queues}


def service_action(service: str, action: str) -> dict[str, Any]:
    if service not in SERVICE_NAMES:
        raise ValueError(f"unknown service: {service}")
    if action not in {"restart", "stop", "start"}:
        raise ValueError(f"unsupported service action: {action}")
    unit = SERVICE_NAMES[service]
    completed = subprocess.run(
        ["systemctl", "--user", action, unit],
        check=False,
        capture_output=True,
        text=True,
    )
    return {"unit": unit, "action": action, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def retry_failed_job(state_dir: Path, queue: str, name: str) -> dict[str, Any]:
    root = queue_root(state_dir, queue)
    source = root / "failed" / name
    if not source.exists() or source.suffix != ".json" or source.name.endswith(".error.json"):
        raise ValueError(f"failed job not found: {name}")
    target = root / "inbox" / name
    if target.exists():
        raise ValueError(f"inbox job already exists: {name}")
    shutil.move(str(source), target)
    error_file = root / "failed" / f"{source.stem}.error.json"
    if error_file.exists():
        archive = root / "failed" / f"{source.stem}.error.{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
        shutil.move(str(error_file), archive)
    return {"retried": str(target)}


def cancel_queued_job(state_dir: Path, queue: str, name: str) -> dict[str, Any]:
    root = queue_root(state_dir, queue)
    source = root / "inbox" / name
    if not source.exists() or source.suffix != ".json":
        raise ValueError(f"queued job not found: {name}")
    target = root / "failed" / name
    if target.exists():
        raise ValueError(f"failed job already exists: {name}")
    shutil.move(str(source), target)
    write_json(
        root / "failed" / f"{source.stem}.error.json",
        {"cancelled_at": utc_now(), "cancelled_by": "agent-activity-server", "job_file": str(target)},
    )
    return {"cancelled": str(target)}


def render_html(snapshot: dict[str, Any], token_required: bool, token_value: str = "") -> str:
    status = snapshot.get("platform_status", {})
    healthy = status.get("healthy")
    badge = "healthy" if healthy else "attention needed"
    token_hint = "<p><strong>Actions require token.</strong> Add <code>?token=...</code> to the URL.</p>" if token_required else ""
    sections = []
    for queue_name, queue in snapshot["queues"].items():
        heartbeat = queue.get("heartbeat", {})
        rows = []
        for folder in ("processing", "inbox", "failed", "recent_done"):
            for item in queue[folder]:
                actions = ""
                if folder == "failed":
                    actions = action_form("retry-failed", queue_name, item["name"], "Retry", token_value)
                elif folder == "inbox":
                    actions = action_form("cancel-queued", queue_name, item["name"], "Cancel", token_value)
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(folder)}</td>"
                    f"<td>{html.escape(item['name'])}</td>"
                    f"<td>{html.escape(item.get('action') or '')}</td>"
                    f"<td>{html.escape(str(item.get('title') or item.get('decision') or ''))}</td>"
                    f"<td>{link(item.get('pr_url', ''))}</td>"
                    f"<td>{actions}</td>"
                    "</tr>"
                )
        sections.append(
            f"""
            <section>
              <h2>{html.escape(queue_name.title())} Queue</h2>
              <p>Heartbeat: {html.escape(str(heartbeat.get('updated_at', 'missing')))}; current job: {html.escape(str(heartbeat.get('current_job')))}</p>
              <table>
                <thead><tr><th>State</th><th>File</th><th>Action</th><th>Title / Decision</th><th>PR</th><th>Controls</th></tr></thead>
                <tbody>{''.join(rows) or '<tr><td colspan="6">No recent jobs</td></tr>'}</tbody>
              </table>
            </section>
            """
        )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>Homelab Agent Activity</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #111827; color: #e5e7eb; }}
    a {{ color: #93c5fd; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #374151; padding: 0.5rem; vertical-align: top; }}
    th {{ background: #1f2937; }}
    button {{ padding: 0.25rem 0.5rem; }}
    code {{ background: #1f2937; padding: 0.1rem 0.25rem; }}
    .badge {{ display: inline-block; padding: 0.25rem 0.5rem; border-radius: 0.5rem; background: {'#065f46' if healthy else '#92400e'}; }}
  </style>
</head>
<body>
  <h1>Homelab Agent Activity</h1>
  <p>Status: <span class="badge">{html.escape(badge)}</span> Generated: {html.escape(snapshot.get('generated_at', ''))}</p>
  {token_hint}
  <section>
    <h2>Services</h2>
    {service_forms(token_value)}
  </section>
  {''.join(sections)}
</body>
</html>"""


def link(url: str) -> str:
    if not url:
        return ""
    return f'<a href="{html.escape(url)}">{html.escape(url)}</a>'


def token_input(token_value: str) -> str:
    return f'<input type="hidden" name="token" value="{html.escape(token_value)}">' if token_value else ""


def action_form(action: str, queue: str, job: str, label: str, token_value: str = "") -> str:
    return (
        f'<form method="post" action="/actions/{html.escape(action)}">'
        f'{token_input(token_value)}'
        f'<input type="hidden" name="queue" value="{html.escape(queue)}">'
        f'<input type="hidden" name="job" value="{html.escape(job)}">'
        f'<button type="submit">{html.escape(label)}</button></form>'
    )


def service_forms(token_value: str = "") -> str:
    forms = []
    for service in SERVICE_NAMES:
        buttons = []
        for action in ("restart", "stop", "start"):
            buttons.append(
                f'<form method="post" action="/actions/service" style="display:inline">'
                f'{token_input(token_value)}'
                f'<input type="hidden" name="service" value="{html.escape(service)}">'
                f'<input type="hidden" name="service_action" value="{html.escape(action)}">'
                f'<button type="submit">{html.escape(action)}</button></form>'
            )
        forms.append(f"<p><strong>{html.escape(service)}</strong>: {''.join(buttons)}</p>")
    return "".join(forms)


class ActivityHandler(BaseHTTPRequestHandler):
    state_dir: Path
    token: str

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return {key: values[-1] for key, values in parse.parse_qs(raw).items()}

    def _authorized(self, form: dict[str, str] | None = None) -> bool:
        if not self.token:
            return True
        query = parse.parse_qs(parse.urlparse(self.path).query)
        provided = self.headers.get("X-Agent-Activity-Token") or (form or {}).get("token") or (query.get("token") or [""])[-1]
        return provided == self.token

    def _send(self, status: int, body: str, content_type: str = "text/html") -> None:
        rendered = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)

    def do_GET(self) -> None:  # noqa: N802
        path = parse.urlparse(self.path).path
        snapshot = build_snapshot(self.state_dir)
        if path == "/api/status":
            self._send(200, json.dumps(snapshot, indent=2) + "\n", "application/json")
            return
        query = parse.parse_qs(parse.urlparse(self.path).query)
        token_value = (query.get("token") or [""])[-1]
        self._send(200, render_html(snapshot, token_required=bool(self.token), token_value=token_value))

    def do_POST(self) -> None:  # noqa: N802
        form = self._read_form()
        if not self._authorized(form):
            self._send(403, "Forbidden\n", "text/plain")
            return
        path = parse.urlparse(self.path).path
        try:
            if path == "/actions/service":
                result = service_action(form.get("service", ""), form.get("service_action", ""))
            elif path == "/actions/retry-failed":
                result = retry_failed_job(self.state_dir, form.get("queue", ""), form.get("job", ""))
            elif path == "/actions/cancel-queued":
                result = cancel_queued_job(self.state_dir, form.get("queue", ""), form.get("job", ""))
            else:
                self._send(404, "Not found\n", "text/plain")
                return
            self._send(200, json.dumps(result, indent=2) + "\n\nReturn to / to refresh.\n", "text/plain")
        except Exception as exc:
            self._send(400, f"{exc}\n", "text/plain")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("AGENT_ACTIVITY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_ACTIVITY_PORT", "8766")))
    parser.add_argument("--state-dir", default=os.environ.get("AGENT_STATE_DIR", str(Path.home() / ".local/state/homelab-control")))
    args = parser.parse_args()

    ActivityHandler.state_dir = Path(args.state_dir).expanduser()
    ActivityHandler.token = os.environ.get("AGENT_ACTIVITY_TOKEN", "")
    server = ThreadingHTTPServer((args.host, args.port), ActivityHandler)
    print(f"agent activity server listening on {args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
