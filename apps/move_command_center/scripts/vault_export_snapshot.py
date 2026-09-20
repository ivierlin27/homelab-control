#!/usr/bin/env python3
"""Fetch vault markdown export from move API and save on Alienware."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path


def main() -> int:
    base = os.environ.get("MOVE_COMMAND_CENTER_URL", "http://192.168.1.69:8780").rstrip("/")
    out_dir = Path(
        os.environ.get(
            "MOVE_VAULT_EXPORT_DIR",
            str(Path.home() / ".local/state/homelab-control/move-vault-exports"),
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f"{base}/api/v1/export/vault", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    markdown = data.get("markdown") or ""
    if not markdown:
        print("export returned empty markdown", file=sys.stderr)
        return 1
    path = out_dir / f"{date.today().isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    print(f"wrote {path} ({len(markdown)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
