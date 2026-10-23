"""Filesystem custody and data-only planning in synthetic installations."""

import hashlib
import json
import os
import shlex
import socket
import subprocess
import zipfile
from pathlib import Path

from fenric import lifecycle as l
from fenric.cli import build_parser, main


def skill(path, *, version=None):
    path.mkdir(parents=True)
    declared = "" if version is None else f"version: {version}\n"
    (path / "SKILL.md").write_text(
        "---\nname: fenric\ndescription: A synthetic skill.\n" + declared + "---\nRead guide.md.\n"
    )
    (path / "guide.md").write_text("Synthetic guide\n")
    return path


def own(path, name="fenric", scope="global"):
    s = l.tree(path)
    (path / l.RECEIPT).write_bytes(
        l.canonical(
            {
                "schema": "fenric.skill-ownership.v1",
                "name": name,
                "scope": scope,
                "path": path.relative_to(path.parents[2]).as_posix()
                if path.parent.name == "skills"
                else path.name,
                "bundle_sha256": s["bundle_sha256"],
                "version": s["version"]["value"],
            }
        )
    )


def observe(home, project=None):
    return l.inventory(home, project, None)


def test_manifest_binary_empty_directory_and_skill_version(tmp_path):
    p = skill(tmp_path / "skill")
    first = l.tree(p)
    assert first["version"] == {"state": "absent", "value": None}
    (p / "data.bin").write_bytes(b"\x00\xff")
    second = l.tree(p)
    assert second["bundle_sha256"] != first["bundle_sha256"]
    (p / "empty").mkdir()
    third = l.tree(p)
    assert second["bundle_sha256"] != third["bundle_sha256"]
    assert l.tree(p) == third
    (p / "SKILL.md").write_text("---\nversion: 1.2\nversion: 1.3\n---\n")
    assert l.tree(p)["state"] == "invalid-skill-version"


def test_ownership_intact_drift_and_strict_receipts(tmp_path):
    p = skill(tmp_path / "skill", version="1.0")
    assert l.ownership(l.tree(p), "fenric", "global", "skill") == "unowned"
    own(p)
    assert l.ownership(l.tree(p), "fenric", "global", "skill") == "owned-intact"
    assert l.ownership(l.tree(p), "fenric", "project", "skill") == "drift"
    (p / "guide.md").write_text("Changed\n")
    assert l.ownership(l.tree(p), "fenric", "global", "skill") == "drift"
    for raw in (b'{"name":1,"name":2}', b'{"a":NaN}'):
        (p / l.RECEIPT).write_bytes(raw)
        assert l.tree(p)["state"] == "invalid-receipt"


def test_missing_hardlink_fifo_symlink_loop_and_limits(tmp_path, monkeypatch):
    assert l.tree(tmp_path / "absent")["state"] == "missing"
    p = skill(tmp_path / "skill")
    os.link(p / "guide.md", p / "duplicate")
    assert l.tree(p)["state"] == "hardlinked"
    (p / "duplicate").unlink()
    os.mkfifo(p / "fifo")
    assert l.tree(p)["state"] == "nonregular"
    (p / "fifo").unlink()
    (p / "escape").symlink_to(tmp_path)
    assert l.tree(p)["state"] == "member-symlink"
    (p / "escape").unlink()
    (tmp_path / "loop").symlink_to("loop")
    assert l.tree(tmp_path / "loop")["state"] == "link-loop-or-limit"
    monkeypatch.setitem(l.LIMITS, "file_bytes", 4)
    assert l.tree(p)["state"] == "limit-exceeded"


def test_changed_during_read_and_unreadable(tmp_path, monkeypatch):
    p = skill(tmp_path / "skill")
    (p / "guide.md").chmod(0)
    assert l.tree(p)["state"] == "unreadable"
    (p / "guide.md").chmod(0o600)
    original = os.read
    changed = False

    def change(fd, n):
        nonlocal changed
        raw = original(fd, n)
        if raw and not changed:
            changed = True
            (p / "guide.md").write_text("Changed during scan\n")
        return raw

    monkeypatch.setattr(os, "read", change)
    assert l.tree(p)["state"] == "changed-during-read"


def test_scope_conflict_alias_and_case_mismatch(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    p = skill(home / ".claude/skills/fenric")
    alias = home / ".codex/skills/fenric"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(p)
    observed = observe(home)
    assert not observed["conflicts"]
    assert next(s for s in observed["skills"] if s["path"] == str(alias))["link"] == str(p)
    skill(project / ".agents/skills/fenric")
    assert observe(home, project)["conflicts"][0]["reason"] == "scope-or-target-conflict"
    alias.unlink()
    alias.symlink_to(tmp_path)
    assert (
        next(s for s in observe(home)["skills"] if s["path"] == str(alias))["state"]
        == "link-escape"
    )
    p.rename(p.with_name("Fenric"))
    assert any(s["state"] == "name-mismatch" for s in observe(home)["skills"])


def test_deterministic_plan_and_no_adoption(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    missing = l.dry_run(observe(home), "global", targets=("claude",))
    assert all(o["reason"] == "parent-missing" for o in missing["operations"])
    parent = home / ".claude/skills"
    parent.mkdir(parents=True)
    first = l.dry_run(observe(home), "global", targets=("claude",))
    assert all(o["action"] == "add" for o in first["operations"])
    assert l.canonical(first) == l.canonical(
        l.dry_run(observe(home), "global", targets=("claude",))
    )
    core = {k: v for k, v in first.items() if k != "plan_sha256"}
    assert first["plan_sha256"] == hashlib.sha256(l.canonical(core)).hexdigest()
    import shutil

    for name in l.SKILLS:
        shutil.copytree(Path(l.__file__).parent / "_guidance" / name, parent / name)
    same = l.dry_run(observe(home), "global", targets=("claude",))
    assert all(o["action"] == "no-op" and o["ownership"] == "unowned" for o in same["operations"])
    assert same["plan_sha256"] != first["plan_sha256"]
    remove = l.dry_run(observe(home), "global", targets=("claude",), remove=True)
    assert all(o["action"] == "blocked" for o in remove["operations"])
    for name in l.SKILLS:
        own(parent / name, name)
    remove = l.dry_run(observe(home), "global", targets=("claude",), remove=True)
    assert all(o["action"] == "remove" for o in remove["operations"])


def test_runtime_metadata_is_not_execution_or_skill_version(tmp_path):
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    executable = env / "bin/fenric"
    executable.write_text(f"#!{env}/bin/python\nraise RuntimeError('must not run')\n")
    (env / "pyvenv.cfg").write_text("version = 3.11.9\n")
    dist = env / "lib/python3.11/site-packages/fenric-9.9.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: fenric\nVersion: 9.9\n")
    current = l.runtime(executable)
    assert current["version"] == "9.9" and current["loaded_version"] is None
    assert current["manager"] == "unknown" and current["artifact"]["state"] == "unknown"
    (env / "pipx_metadata.json").write_text('{"main_package":{"package":"fenric"}}')
    assert l.runtime(executable)["manager"]["kind"] == "pipx"
    (env / "uv-receipt.toml").write_text('[[tool.requirements]]\nname = "fenric"\n')
    assert l.runtime(executable)["manager"] == "conflict"
    other = dist.with_name("fenric-9.8.dist-info")
    other.mkdir()
    assert l.runtime(executable)["state"] == "conflict"
    executable.write_text("#!/usr/bin/env python\n")
    assert l.runtime(executable)["state"] == "unknown"


def test_local_artifact_binding_and_installed_bytes(tmp_path):
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    executable = env / "bin/fenric"
    executable.write_text(f"#!{env}/bin/python\n")
    (env / "pyvenv.cfg").write_text("version = 3.11.9\n")
    site = env / "lib/python3.11/site-packages"
    dist = site / "fenric-1.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: fenric\nVersion: 1.0\n")
    package = site / "fenric"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = '1.0'\n")
    wheel = tmp_path / "fenric-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("fenric/__init__.py", (package / "__init__.py").read_bytes())
    binding = {
        "url": wheel.as_uri(),
        "archive_info": {"hashes": {"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}},
    }
    direct = dist / "direct_url.json"
    direct.write_text(json.dumps(binding))
    assert l.runtime(executable)["artifact"]["installed_bytes"] == "matched-selected-members"
    binding["archive_info"] = {}
    direct.write_text(json.dumps(binding))
    assert l.runtime(executable)["artifact"]["state"] == "unclaimed"
    binding["archive_info"] = {"hashes": {"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}}
    direct.write_text(json.dumps(binding))
    (package / "extra.py").write_text("# unknown extra\n")
    assert l.runtime(executable)["artifact"]["installed_bytes"] == "mismatch"
    binding["archive_info"]["hashes"]["sha256"] = "0" * 64
    direct.write_text(json.dumps(binding))
    assert l.runtime(executable)["artifact"]["state"] == "mismatch"
    binding["url"] = (tmp_path / "private.txt").as_uri()
    direct.write_text(json.dumps(binding))
    assert l.runtime(executable)["artifact"]["reason"] == "outside-selected-stage"


def test_swapped_fifo_before_open_refuses_without_read(tmp_path, monkeypatch):
    p = skill(tmp_path / "skill")
    original = os.open
    swapped = False

    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "guide.md" and not swapped:
            swapped = True
            (p / "guide.md").unlink()
            os.mkfifo(p / "guide.md")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    assert l.tree(p)["state"] == "changed-during-read"


def test_cli_no_writes_execution_network_or_constitution_changes(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    p = skill(home / ".claude/skills/fenric")
    constitution = home / ".claude/CLAUDE.md"
    constitution.write_bytes(b"Unrelated custom instructions\r\n")
    alias = home / ".codex/AGENTS.md"
    alias.parent.mkdir()
    alias.symlink_to(constitution)
    before = constitution.read_bytes(), constitution.stat(), os.readlink(alias)

    def forbidden(*args, **kwargs):
        raise AssertionError("read-only observation attempted an effect")

    for module, names in (
        (subprocess, ("run", "Popen")),
        (socket, ("socket",)),
        (os, ("system", "replace", "rename", "mkdir", "unlink", "chmod", "execv", "spawnv")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    original = os.open

    def safe_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", safe_open)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    assert main(["lifecycle", "inventory"]) == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["runtime"]["state"] == "missing"
    assert observed["project_shadowing"] == "unobserved"
    assert observed["harness_loaded_skills"] == "unknown"
    assert main(["lifecycle", "plan", "--scope", "global", "--target", "claude"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert main(["lifecycle", "plan", "--scope", "project"]) == 3
    assert main(["--timeout", "10", "lifecycle", "inventory"]) == 3
    assert (constitution.read_bytes(), constitution.stat(), os.readlink(alias)) == before
    assert p.is_dir()


def test_vanishing_member_is_not_an_add(tmp_path, monkeypatch):
    home = tmp_path / "home"
    p = skill(home / ".claude/skills/fenric")
    original = os.stat
    changed = False

    def vanish(path, *args, **kwargs):
        nonlocal changed
        if path == "guide.md" and kwargs.get("dir_fd") is not None and not changed:
            changed = True
            (p / "guide.md").unlink()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", vanish)
    observed = observe(home)
    current = next(s for s in observed["skills"] if s["path"] == str(p))
    assert current["state"] == "changed-during-read" and not observed["complete"]
    plan = l.dry_run(observed, "global", targets=("claude",))
    op = next(o for o in plan["operations"] if o["name"] == "fenric")
    assert op["action"] == "blocked" and plan["complete"] is False


def test_copies_are_independent_but_mutating_aliases_block(tmp_path):
    import shutil

    home = tmp_path / "home"
    left = home / ".claude/skills/fenric"
    right = home / ".codex/skills/fenric"
    shutil.copytree(Path(l.__file__).parent / "_guidance/fenric", left)
    right.parent.mkdir(parents=True)
    shutil.copytree(left, right)
    plan = l.dry_run(observe(home), "global")
    assert all(o["action"] == "no-op" for o in plan["operations"] if o["name"] == "fenric")
    own(left)
    shutil.copyfile(left / l.RECEIPT, right / l.RECEIPT)
    observed = observe(home)
    assert next(s for s in observed["skills"] if s["path"] == str(right))["ownership"] == "drift"
    shutil.rmtree(right)
    right.symlink_to(left)
    plan = l.dry_run(observe(home), "global", remove=True)
    assert all(o["action"] == "blocked" for o in plan["operations"] if o["name"] == "fenric")
    right.unlink()
    right.parent.rmdir()
    right.parent.symlink_to(left.parent)
    plan = l.dry_run(observe(home), "global", remove=True)
    assert all(o["action"] == "blocked" for o in plan["operations"] if o["name"] == "fenric")


def test_metadata_links_cannot_read_outside_selected_environment(tmp_path):
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    executable = env / "bin/fenric"
    executable.write_text(f"#!{env}/bin/python\n")
    outside = tmp_path / "outside.cfg"
    outside.write_text("version = 3.11\n")
    (env / "pyvenv.cfg").symlink_to(outside)
    assert l.runtime(executable)["state"] == "link-escape"


def test_link_hop_limit_is_incomplete_not_success(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    target = home / ".claude/skills/fenric"
    target.parent.mkdir(parents=True)
    target.symlink_to("fenric")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    assert main(["lifecycle", "inventory"]) == 4
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is False
    assert any(s["state"] == "link-loop-or-limit" for s in result["skills"])


def test_frontmatter_parsing_and_resource_policy(tmp_path, monkeypatch):
    p = skill(tmp_path / "skill", version="1.2")
    (p / "SKILL.md").write_text("---\ndescription: ends with ---\nversion: 1.2\n---\nBody\n")
    assert l.tree(p)["version"]["value"] == "1.2"
    os.link(p / "guide.md", tmp_path / "cache-hardlink")
    assert l.tree(p)["state"] == "hardlinked"
    assert l.tree(p, resources=True)["state"] == "observed"
    monkeypatch.setattr(os, "getuid", lambda: p.stat().st_uid + 1)
    assert l.tree(p)["state"] == "foreign-owner"
    assert l.tree(p, resources=True)["state"] == "observed"


def test_packaged_skills_have_local_links_and_no_apply_endpoint():
    import re

    for name in l.SKILLS:
        directory = Path(l.__file__).parent / "_guidance" / name
        assert l.tree(directory)["state"] == "observed"
        text = (directory / "SKILL.md").read_text()
        for target in re.findall(r"\]\(([^)]+)\)", text):
            resolved = (directory / target).resolve(strict=True)
            assert resolved.is_relative_to(directory.resolve())
    guide = Path(l.__file__).parent / "_guidance/fenric-install/guide.md"
    for command in re.findall(r"```bash\n(.*?)\n```", guide.read_text(), re.DOTALL):
        args = shlex.split(command)
        assert args.pop(0) == "fenric" and "\n" not in command
        parsed = build_parser().parse_args(args)
        assert parsed.verb == "lifecycle"
    assert main(["lifecycle", "apply"]) == 3
