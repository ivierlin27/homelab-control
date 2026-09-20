#!/usr/bin/env python3
"""Post move command center daily digest to Discord (Kevin DM or ops channel).

Run via systemd timer on Alienware. Requires:
  DISCORD_BOT_TOKEN
  MOVE_COMMAND_CENTER_URL (default http://192.168.1.69:8780)
  MOVE_DIGEST_CHANNEL_ID  — post to channel (default #ops 1505405631396446268), OR
  MOVE_DIGEST_USER_ID     — DM this user if channel id empty
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Executive bot #ops — agent-executive.yaml / homelab-control channel map
DEFAULT_OPS_CHANNEL_ID = "1505405631396446268"
DISCORD_USER_AGENT = "DiscordBot (https://github.com/ivierlin27/homelab-control, 1.0)"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _request(method: str, url: str, *, token: str, body: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bot {token}",
        "Accept": "application/json",
        "User-Agent": DISCORD_USER_AGENT,
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def fetch_digest() -> str:
    base = _env("MOVE_COMMAND_CENTER_URL", "http://192.168.1.69:8780").rstrip("/")
    req = urllib.request.Request(f"{base}/api/v1/digest", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    text = data.get("text") or ""
    if not text:
        raise RuntimeError("digest API returned empty text")
    return text


def resolve_channel_id(token: str) -> str:
    channel = _env("MOVE_DIGEST_CHANNEL_ID", DEFAULT_OPS_CHANNEL_ID)
    if channel:
        return channel
    user_id = _env("MOVE_DIGEST_USER_ID")
    if not user_id:
        allowed = _env("DISCORD_ALLOWED_USER_IDS")
        if allowed:
            user_id = allowed.split(",")[0].strip()
    if not user_id:
        raise RuntimeError(
            "Set MOVE_DIGEST_CHANNEL_ID or MOVE_DIGEST_USER_ID (or DISCORD_ALLOWED_USER_IDS)"
        )
    dm = _request(
        "POST",
        "https://discord.com/api/v10/users/@me/channels",
        token=token,
        body={"recipient_id": user_id},
    )
    return str(dm["id"])


def post_message(token: str, channel_id: str, content: str) -> None:
    _request(
        "POST",
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        token=token,
        body={"content": content},
    )


def main() -> int:
    token = _env("DISCORD_BOT_TOKEN")
    if not token:
        print("error: DISCORD_BOT_TOKEN not set", file=sys.stderr)
        return 1
    if _env("MOVE_DIGEST_DRY_RUN", "0") in ("1", "true", "yes"):
        print(fetch_digest())
        return 0
    try:
        text = fetch_digest()
        channel_id = resolve_channel_id(token)
        post_message(token, channel_id, text)
        print(f"digest sent to channel {channel_id} ({len(text)} chars)")
        return 0
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, KeyError) as exc:
        print(f"digest failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
