"""Test the skill-policy snapshot generator.

The generator is wired into the gateway systemd unit's ExecStartPre, so
correctness here directly affects what the gateway enforces. We exercise:

  - happy path against the real config/skills/ tree on disk
  - empty skill dir (no SKILL.md files) -> empty snapshot, no crash
  - loader error -> fail-open snapshot with an error marker

The output schema is the contract with ``local_only_policy.load_snapshot``,
so we also round-trip through ``load_snapshot`` to confirm the loader can
read what the generator writes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .generate_skill_policy import SCHEMA_VERSION, build_snapshot_dict, main
from .local_only_policy import load_snapshot


def _write_skill(skills_dir: Path, skill_id: str, *, local_only: bool) -> None:
    d = skills_dir / skill_id
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\n"
        f"id: {skill_id}\n"
        f"name: {skill_id}\n"
        f"description: test skill\n"
        f"local_only: {'true' if local_only else 'false'}\n"
        f"required_tools: []\n"
        f"required_task_classes: []\n"
        f"version: 1\n"
        f"---\n"
        f"body\n",
        encoding="utf-8",
    )


def test_build_snapshot_dict_against_real_skills_dir():
    """Smoke against the real config/skills/ — every shipped skill must appear."""
    snap = build_snapshot_dict()
    assert snap["schema"] == SCHEMA_VERSION
    assert "skills" in snap
    # Spot-check two real skills with known local_only values (see survey).
    skills = snap["skills"]
    assert "intake-classify" in skills
    assert skills["intake-classify"]["local_only"] is True
    assert "execute-task" in skills
    assert skills["execute-task"]["local_only"] is False


def test_build_snapshot_dict_with_empty_skills_dir(tmp_path):
    snap = build_snapshot_dict(tmp_path)
    assert snap["schema"] == SCHEMA_VERSION
    assert snap["skills"] == {}


def test_build_snapshot_dict_with_synthetic_skills(tmp_path):
    _write_skill(tmp_path, "test-local", local_only=True)
    _write_skill(tmp_path, "test-cloud", local_only=False)
    snap = build_snapshot_dict(tmp_path)
    assert snap["skills"]["test-local"]["local_only"] is True
    assert snap["skills"]["test-cloud"]["local_only"] is False


def test_main_writes_snapshot_atomic_and_loadable(tmp_path):
    _write_skill(tmp_path / "skills", "test-local", local_only=True)
    out_path = tmp_path / "out" / "policy.json"
    rc = main(
        [
            "--output", str(out_path),
            "--skills-dir", str(tmp_path / "skills"),
        ]
    )
    assert rc == 0
    assert out_path.is_file()
    # The temp file used during atomic write must NOT remain.
    assert not (out_path.with_suffix(out_path.suffix + ".tmp")).exists()

    # Round-trip through the loader the gateway will use.
    snap = load_snapshot(out_path)
    assert snap.get_skill("test-local").local_only is True


def test_main_writes_fail_open_snapshot_on_loader_error(tmp_path, monkeypatch):
    """If the skills loader raises, the generator writes an empty snapshot
    rather than failing the gateway startup."""
    from . import generate_skill_policy as gen

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated loader failure")

    monkeypatch.setattr(gen, "load_skill_registry", _boom)
    out_path = tmp_path / "policy.json"
    rc = main(["--output", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["skills"] == {}
    assert "load_error" in payload
    assert "simulated loader failure" in payload["load_error"]
