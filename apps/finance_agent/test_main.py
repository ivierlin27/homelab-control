"""Unit tests for apps.finance_agent.main (Sprint F2 skeleton)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from apps.finance_agent.main import (
    DEFAULT_PRINCIPAL,
    LEDGER_MAIN_FILE,
    VERSION_LABEL,
    ledger_state,
    main,
    render_status,
)


# --- pure-function tests --------------------------------------------------

def test_ledger_state_no_directory(tmp_path: Path) -> None:
    state = ledger_state(tmp_path / "does-not-exist")
    assert state["initialized"] is False
    assert state["ledger_dir"] == str(tmp_path / "does-not-exist")


def test_ledger_state_directory_but_no_main_file(tmp_path: Path) -> None:
    (tmp_path / "ledger").mkdir()
    state = ledger_state(tmp_path / "ledger")
    assert state["initialized"] is False


def test_ledger_state_initialized(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    main_file = ledger / LEDGER_MAIN_FILE
    main_file.write_text("option \"title\" \"Kevin's ledger\"\n", encoding="utf-8")

    state = ledger_state(ledger)

    assert state["initialized"] is True
    assert state["main_file"] == str(main_file)
    assert state["main_file_bytes"] == main_file.stat().st_size


# --- rendering tests ------------------------------------------------------

def test_render_status_uninitialized_human_string_matches_f2_spec() -> None:
    # F2 acceptance: exact output for the empty-state case.
    state = {"initialized": False, "ledger_dir": "/wherever", "main_file": "x"}
    rendered = render_status(state, as_json=False)
    assert rendered == f"{DEFAULT_PRINCIPAL} {VERSION_LABEL} — no ledger initialized"


def test_render_status_initialized_human_string() -> None:
    state = {
        "initialized": True,
        "ledger_dir": "/home/k/finance/ledger",
        "main_file": "/home/k/finance/ledger/main.beancount",
        "main_file_bytes": 42,
    }
    rendered = render_status(state, as_json=False)
    assert "ledger initialized at /home/k/finance/ledger" in rendered
    assert rendered.startswith(f"{DEFAULT_PRINCIPAL} {VERSION_LABEL}")


def test_render_status_json_includes_metadata() -> None:
    state = {"initialized": False, "ledger_dir": "/tmp/x", "main_file": "/tmp/x/main.beancount"}
    rendered = render_status(state, as_json=True)
    payload = json.loads(rendered)
    assert payload["principal"] == DEFAULT_PRINCIPAL
    assert payload["version"] == VERSION_LABEL
    assert "package_version" in payload
    assert payload["initialized"] is False


# --- CLI integration via main() with --skip-boot --------------------------

def test_main_status_uninitialized_prints_expected_string(tmp_path: Path) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--skip-boot", "status", "--ledger-dir", str(tmp_path)])
    assert rc == 0
    out = buf.getvalue().strip()
    assert out == f"{DEFAULT_PRINCIPAL} {VERSION_LABEL} — no ledger initialized"


def test_main_status_json_emits_valid_json(tmp_path: Path) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--skip-boot", "status", "--ledger-dir", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["principal"] == DEFAULT_PRINCIPAL
    assert payload["initialized"] is False


def test_main_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        main(["--skip-boot"])


# --- subprocess smoke (mirrors the F2 acceptance command line) ------------

def test_module_entrypoint_subprocess(tmp_path: Path) -> None:
    """Mirror the acceptance invocation. Validates packaging/__main__-ish wiring.

    Uses --skip-boot because pytest can't load the agent-finance identity
    state file (it only exists on Alienware).
    """
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "apps.finance_agent.main",
            "--skip-boot",
            "status",
            "--ledger-dir",
            str(tmp_path),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == (
        f"{DEFAULT_PRINCIPAL} {VERSION_LABEL} — no ledger initialized"
    )
