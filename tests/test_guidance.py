"""Owned instruction merge and read-path custody, in synthetic homes only."""

import copy
import json
import os
from pathlib import Path

import pytest

from fenric import guidance as g
from fenric.cli import main


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


def files(home, *, alias=False):
    claude = home / ".claude/CLAUDE.md"
    codex = home / ".codex/AGENTS.md"
    claude.parent.mkdir(parents=True)
    codex.parent.mkdir(parents=True)
    claude.write_bytes(b"User's rules without a final newline")
    claude.chmod(0o640)
    if alias:
        codex.symlink_to(Path("../.claude/CLAUDE.md"))
    else:
        codex.write_bytes(b"Independent instructions\n")
    return claude, codex


def test_distinct_and_shared_read_paths_preserve_bytes_and_repeat(tmp_path):
    for alias in (False, True):
        home = tmp_path / str(alias)
        left, right = files(home, alias=alias)
        originals = [p.read_bytes() for p in (left, right)]
        plan = g.preview(home)
        assert len(plan["files"]) == (1 if alias else 2)
        assert all(p["state"] == "resolved" for p in plan["read_paths"])
        assert "\\ No newline at end of file\n" in plan["files"][0]["diff"]
        assert all(
            line.startswith(("---", "+++", "@@", "+", "-", "\\")) and line.endswith("\n")
            for line in plan["files"][0]["diff"].splitlines(True)
        )
        result = g.apply(home, plan, home / "backups")
        assert result["status"] == "applied"
        assert result["coverage_complete"] is True
        assert g.check(home)["status"] == "complete"
        assert g.check(home)["native_adapter"] == "unverified"
        for path, before in zip((left, right), originals, strict=True):
            assert path.read_bytes().startswith(before)
            assert path.read_text().count(g.BEGIN) == 1
        assert left.stat().st_mode & 0o777 == 0o640
        if alias:
            assert right.is_symlink() and os.readlink(right) == "../.claude/CLAUDE.md"
        unchanged = left.read_bytes(), right.read_bytes()
        assert g.apply(home, g.preview(home), home / "not-created")["status"] == "unchanged"
        assert not (home / "not-created").exists()
        assert (left.read_bytes(), right.read_bytes()) == unchanged
        backup_manifest = json.loads((home / "backups/manifest.json").read_text())
        assert len(backup_manifest) == len(plan["files"])
        for row in backup_manifest:
            backup = Path(row["backup"])
            assert g.digest(backup.read_bytes()) == row["sha256"]
            assert backup.stat().st_mode & 0o777 == 0o600
        g.apply(home, g.preview(home, remove=True), home / "removal")
        assert [p.read_bytes() for p in (left, right)] == originals
        assert (
            g.apply(home, g.preview(home, remove=True), home / "not-created-again")["status"]
            == "unchanged"
        )
    home = tmp_path / "crlf"
    left, right = files(home, alias=True)
    original = b"User instructions\r\nPreserve these bytes\r\n"
    left.write_bytes(original)
    plan = g.preview(home)
    assert g.apply(home, plan, home / "backups")["status"] == "applied"
    assert left.read_bytes().startswith(original)
    assert g.digest(left.read_bytes()) == plan["files"][0]["after_sha256"]
    g.apply(home, g.preview(home, remove=True), home / "remove")
    assert left.read_bytes() == right.read_bytes() == original


def test_updates_conflicts_and_unmanaged_mentions(tmp_path, monkeypatch):
    left, right = files(tmp_path)
    g.apply(tmp_path, g.preview(tmp_path), tmp_path / "initial")
    original_body = g.template()
    monkeypatch.setattr(g, "template", lambda: original_body + "\nA new instruction.\n")
    assert g.check(tmp_path)["status"] == "needs-setup-or-review"
    assert all(p["guidance"] == "update-required" for p in g.check(tmp_path)["harnesses"])
    g.apply(tmp_path, g.preview(tmp_path), tmp_path / "update")
    assert "A new instruction." in left.read_text()
    settled = left.read_bytes()
    cases = [
        settled.replace(b"A new instruction.", b"Operator edited content."),
        settled + settled,
        settled + b"\n## Fenric custom rules\nUser content\n",
        "## Fenric — direct user interaction\nCustom owner guidance\n".encode(),
        g.BEGIN.encode(),
    ]
    for raw in cases:
        left.write_bytes(raw)
        plan = g.preview(tmp_path)
        assert plan["read_paths"][0]["state"] == "conflict"
        with pytest.raises(g.GuidanceError, match="coverage conflict"):
            g.apply(tmp_path, plan, tmp_path / "never-created")
        assert left.read_bytes() == raw
    left.write_text("This file mentions fenric --help and a checkout, without a heading.\n")
    assert g.preview(tmp_path)["read_paths"][0]["state"] == "resolved"
    assert right.read_text().count(g.BEGIN) == 1
    left.write_bytes(settled + b"\n## Fenric custom rules\nUser content\n")
    removal = g.preview(tmp_path, remove=True)
    g.apply(tmp_path, removal, tmp_path / "remove-owned")
    assert left.read_bytes().endswith(b"\n## Fenric custom rules\nUser content\n")
    assert g.BEGIN.encode() not in left.read_bytes()


def test_stale_plan_changed_alias_and_missing_paths_refuse(tmp_path, monkeypatch):
    left, right = files(tmp_path, alias=True)
    plan = g.preview(tmp_path)
    left.write_text("Owner changed instructions\n")
    with pytest.raises(g.GuidanceError, match="stale"):
        g.apply(tmp_path, plan, tmp_path / "no-backup")
    assert not (tmp_path / "no-backup").exists()
    plan = g.preview(tmp_path)
    altered = copy.deepcopy(plan)
    altered["files"][0]["target"] = str(tmp_path / "not-an-instruction-file")
    with pytest.raises(g.GuidanceError, match="stale"):
        g.apply(tmp_path, altered, tmp_path / "no-backup")
    monkeypatch.setattr(g, "template", lambda: "Changed template\n")
    with pytest.raises(g.GuidanceError, match="stale"):
        g.apply(tmp_path, plan, tmp_path / "no-backup")
    plan = g.preview(tmp_path)
    right.unlink()
    right.symlink_to("missing")
    with pytest.raises(g.GuidanceError, match="stale"):
        g.apply(tmp_path, plan, tmp_path / "no-backup")
    probe = g.check(tmp_path)
    assert probe["harnesses"][1]["read_path"] == "broken-link"
    assert probe["status"] != "complete"
    assert probe["native_adapter"] == "unverified"
    right.unlink()
    assert g.check(tmp_path)["harnesses"][1]["read_path"] == "missing"
    assert not right.exists()
    right.hardlink_to(left)
    assert g.preview(tmp_path)["read_paths"][0]["state"] == "conflict"


def test_atomic_failure_reports_partial_and_keeps_all_backups(tmp_path, monkeypatch):
    left, right = files(tmp_path)
    originals = [left.read_bytes(), right.read_bytes()]
    replace = g._replace
    calls = []

    def fail_second(path, raw, expected):
        calls.append(path)
        if len(calls) == 2:
            assert left.read_text().count(g.BEGIN) == 1
            assert all((tmp_path / f"backups/{i}.before.md").exists() for i in range(2))
            raise RuntimeError("synthetic symlink-loop failure")
        replace(path, raw, expected)

    monkeypatch.setattr(g, "_replace", fail_second)
    with pytest.raises(g.GuidanceError, match="do not blindly retry"):
        g.apply(tmp_path, g.preview(tmp_path), tmp_path / "backups")
    assert left.read_text().count(g.BEGIN) == 1
    assert right.read_bytes() == originals[1]
    outcome = json.loads((tmp_path / "backups/outcome.json").read_text())
    assert outcome["status"] == "unknown-or-partial"
    assert outcome["completed"] == [str(left)]
    assert outcome["pending"] == [str(right)]
    assert [(tmp_path / f"backups/{i}.before.md").read_bytes() for i in range(2)] == originals
    assert not list(tmp_path.rglob(".fenric-guidance-*"))


def test_cli_default_home_and_private_plan_no_security_side_effect(tmp_path, capsys, monkeypatch):
    files(tmp_path, alias=True)
    plan = tmp_path / "review.json"
    assert main(["guidance", "preview", "--plan", str(plan)]) == 0
    assert plan.stat().st_mode & 0o777 == 0o600
    assert main(["guidance", "preview", "--plan", str(plan)]) == 3
    assert "output file already exists; choose a new name" in capsys.readouterr().err
    assert (
        main(["guidance", "apply", "--plan", str(plan), "--backup-dir", str(tmp_path / "backup")])
        == 0
    )
    assert main(["guidance", "check"]) == 0
    assert '"native_adapter": "unverified"' in capsys.readouterr().out
    assert not (tmp_path / ".claude/settings.json").exists()
    assert not (tmp_path / ".codex/config.toml").exists()
    removal = tmp_path / "remove.json"
    assert main(["guidance", "preview", "--remove", "--plan", str(removal)]) == 0
    assert (
        main(
            ["guidance", "apply", "--plan", str(removal), "--backup-dir", str(tmp_path / "removed")]
        )
        == 0
    )
    left = tmp_path / ".claude/CLAUDE.md"
    assert left.read_bytes() == b"User's rules without a final newline"
    (tmp_path / ".codex/AGENTS.md").unlink()
    (tmp_path / ".codex").rmdir()
    partial = tmp_path / "partial.json"
    assert main(["guidance", "preview", "--plan", str(partial)]) == 0
    before = left.read_bytes()
    assert (
        main(
            ["guidance", "apply", "--plan", str(partial), "--backup-dir", str(tmp_path / "backup")]
        )
        == 3
    )
    assert "backup directory must be new inside an existing directory" in capsys.readouterr().err
    assert left.read_bytes() == before
    assert (
        main(
            ["guidance", "apply", "--plan", str(partial), "--backup-dir", str(tmp_path / "partial")]
        )
        == 0
    )
    assert '"coverage_complete": false' in capsys.readouterr().out
    assert not (tmp_path / ".codex").exists()
    assert main(["--timeout", "10", "guidance", "check"]) == 3
    monkeypatch.delenv("HOME")
    assert main(["guidance", "check"]) == 3
    assert "home unavailable" in capsys.readouterr().err
