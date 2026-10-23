"""Bounded filesystem observations and data-only lifecycle proposals. Never applies."""

from __future__ import annotations

import argparse
import email.parser
import io
import json
import os
import re
import shutil
import stat
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from fenric import __version__
from fenric.guidance import digest

LIMITS = {
    "file_bytes": 1048576,
    "tree_bytes": 4194304,
    "entries": 128,
    "depth": 8,
    "siblings": 256,
    "shebang_bytes": 4096,
    "artifact_bytes": 16777216,
    "link_hops": 16,
}
SKILLS = ("fenric", "fenric-install")
ROOTS = (".claude/skills", ".agents/skills", ".codex/skills")
RECEIPT = ".fenric-skill.json"
INCOMPLETE = {
    "unreadable",
    "changed-during-read",
    "limit-exceeded",
    "invalid-name",
    "link-loop-or-limit",
}


class ObservationError(ValueError):
    """A closed reason code, never arbitrary filesystem contents."""


def canonical(value: Any) -> bytes:
    """Canonical, newline-free JSON. Observations contain integers, never clock floats."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ObservationError("invalid-receipt")
            out[key] = value
        return out

    def constant(_: str) -> Any:
        raise ObservationError("invalid-receipt")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ObservationError("invalid-receipt") from exc


def identity(s: os.stat_result) -> dict[str, int]:
    return {
        "device": s.st_dev,
        "inode": s.st_ino,
        "mode": stat.S_IMODE(s.st_mode),
        "uid": s.st_uid,
        "gid": s.st_gid,
        "size": s.st_size,
        "mtime_ns": s.st_mtime_ns,
        "ctime_ns": s.st_ctime_ns,
    }


def resolved(path: Path) -> Path:
    """Resolve links with a finite hop count, including intermediate components."""
    pending = list(path.absolute().parts[1:])
    current = Path("/")
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        item = current / part
        if item.is_symlink():
            hops += 1
            if hops > LIMITS["link_hops"]:
                raise ObservationError("link-loop-or-limit")
            target = Path(os.readlink(item))
            if target.is_absolute():
                current = Path("/")
            pending = list(target.parts[1:] if target.is_absolute() else target.parts) + pending
        else:
            current = item
    return current


def directory_fd(path: Path) -> int:
    """Open each resolved absolute component without following a raced link."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_at(fd: int, name: str, limit: int, *, strict: bool = True) -> tuple[dict[str, int], bytes]:
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ObservationError("nonregular")
    if strict and before.st_nlink != 1:
        raise ObservationError("hardlinked")
    if strict and before.st_uid != os.getuid():
        raise ObservationError("foreign-owner")
    if not before.st_mode & 0o444:
        raise ObservationError("unreadable")
    if before.st_size > limit:
        raise ObservationError("limit-exceeded")
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        if identity(os.fstat(child)) != identity(before):
            raise ObservationError("changed-during-read")
        chunks = []
        total = 0
        while True:
            raw = os.read(child, min(65536, limit + 1 - total))
            if not raw:
                break
            chunks.append(raw)
            total += len(raw)
            if total > limit:
                raise ObservationError("limit-exceeded")
        if identity(os.fstat(child)) != identity(before) or identity(
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        ) != identity(before):
            raise ObservationError("changed-during-read")
        return identity(before), b"".join(chunks)
    finally:
        os.close(child)


def read_file(
    path: Path, limit: int = LIMITS["file_bytes"], *, boundary: Path | None = None
) -> tuple[dict[str, Any], bytes]:
    actual = resolved(path)
    if boundary is not None and not actual.is_relative_to(resolved(boundary)):
        raise ObservationError("link-escape")
    fd = directory_fd(actual.parent)
    try:
        meta, raw = read_at(fd, actual.name, limit)
        if resolved(path) != actual or identity(os.stat(actual, follow_symlinks=False)) != meta:
            raise ObservationError("changed-during-read")
        return {"path": str(path), "resolved": str(actual), **meta, "sha256": digest(raw)}, raw
    finally:
        os.close(fd)


def reason(exc: BaseException) -> str:
    if isinstance(exc, ObservationError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "missing"
    if isinstance(exc, PermissionError):
        return "unreadable"
    if isinstance(exc, UnicodeError):
        return "invalid-name"
    return "unreadable"


def tree(path: Path, *, skill: bool = True, resources: bool = False) -> dict[str, Any]:
    """Read one selected skill directory with bounded descriptor-relative walking."""
    result: dict[str, Any] = {"path": str(path), "state": "observed"}
    entries: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    total = 0
    fd = -1
    try:
        actual = resolved(path)
        result["resolved"] = str(actual)
        if actual.exists() and not actual.is_dir():
            raise ObservationError("nonregular")
        fd = directory_fd(actual)
        initial = os.fstat(fd)
        if not resources and initial.st_uid != os.getuid():
            raise ObservationError("foreign-owner")
        result["identity"] = identity(initial)

        def walk(parent: int, prefix: str, depth: int) -> None:
            nonlocal total
            if depth > LIMITS["depth"]:
                raise ObservationError("limit-exceeded")
            start = identity(os.fstat(parent))
            with os.scandir(parent) as scan:
                names = []
                for item in scan:
                    item.name.encode("utf-8", errors="strict")
                    names.append(item.name)
                    if len(names) + len(entries) > LIMITS["entries"]:
                        raise ObservationError("limit-exceeded")
            names.sort(key=lambda n: n.encode())
            identities = {
                name: identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                for name in names
            }
            for name in names:
                relative = prefix + name
                s = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if identity(s) != identities[name]:
                    raise ObservationError("changed-during-read")
                if not resources and s.st_uid != os.getuid():
                    raise ObservationError("foreign-owner")
                if stat.S_ISLNK(s.st_mode):
                    raise ObservationError("member-symlink")
                if stat.S_ISDIR(s.st_mode):
                    entries.append({"path": relative, "type": "directory"})
                    child = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                    )
                    try:
                        if identity(os.fstat(child)) != identity(s):
                            raise ObservationError("changed-during-read")
                        walk(child, relative + "/", depth + 1)
                        if identity(
                            os.stat(name, dir_fd=parent, follow_symlinks=False)
                        ) != identity(s):
                            raise ObservationError("changed-during-read")
                    finally:
                        os.close(child)
                else:
                    meta, raw = read_at(parent, name, LIMITS["file_bytes"], strict=not resources)
                    total += len(raw)
                    if total > LIMITS["tree_bytes"]:
                        raise ObservationError("limit-exceeded")
                    contents[relative] = raw
                    if relative != RECEIPT:
                        entries.append(
                            {
                                "path": relative,
                                "type": "file",
                                "size": len(raw),
                                "executable": bool(meta["mode"] & 0o111),
                                "sha256": digest(raw),
                            }
                        )
            with os.scandir(parent) as scan:
                after = sorted(item.name for item in scan)
            changed = any(
                identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identities[name]
                for name in names
            )
            if sorted(names) != after or changed or identity(os.fstat(parent)) != start:
                raise ObservationError("changed-during-read")

        walk(fd, "", 0)
        if resolved(path) != actual or identity(os.stat(actual)) != identity(initial):
            raise ObservationError("changed-during-read")
        entries.sort(key=lambda x: x["path"].encode())
        result.update(entries=entries, bundle_sha256=digest(canonical(entries)))
        if skill:
            result["version"] = skill_version(contents.get("SKILL.md", b""))
            result["receipt"] = None
            if RECEIPT in contents:
                receipt = strict_json(contents[RECEIPT])
                if (
                    not isinstance(receipt, dict)
                    or set(receipt)
                    != {"schema", "name", "scope", "path", "bundle_sha256", "version"}
                    or receipt["schema"] != "fenric.skill-ownership.v1"
                    or receipt["name"] not in SKILLS
                    or receipt["scope"] not in ("global", "project")
                    or not isinstance(receipt["path"], str)
                    or Path(receipt["path"]).is_absolute()
                    or ".." in Path(receipt["path"]).parts
                    or not isinstance(receipt["bundle_sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", receipt["bundle_sha256"])
                    or not (receipt["version"] is None or isinstance(receipt["version"], str))
                ):
                    raise ObservationError("invalid-receipt")
                result["receipt"] = receipt
    except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
        result["state"] = (
            "changed-during-read" if fd >= 0 and isinstance(exc, FileNotFoundError) else reason(exc)
        )
    finally:
        if fd >= 0:
            os.close(fd)
    return result


def skill_version(raw: bytes) -> dict[str, Any]:
    """Only one plain top-level frontmatter version scalar is understood."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ObservationError("invalid-skill") from exc
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ObservationError("invalid-skill")
    front = text[4:].split("\n---\n", 1)[0]
    lines = [line for line in front.splitlines() if line.startswith("version:")]
    if not lines:
        return {"state": "absent", "value": None}
    if len(lines) != 1 or not re.fullmatch(r"version: [A-Za-z0-9][A-Za-z0-9.+-]{0,63}", lines[0]):
        raise ObservationError("invalid-skill-version")
    return {"state": "declared", "value": lines[0][9:]}


def ownership(observation: dict[str, Any], name: str, scope: str, location: str) -> str:
    receipt = observation.get("receipt")
    if receipt is None:
        return "unowned"
    expected = {
        "schema": "fenric.skill-ownership.v1",
        "name": name,
        "scope": scope,
        "path": location,
        "bundle_sha256": observation.get("bundle_sha256"),
        "version": observation.get("version", {}).get("value"),
    }
    return "owned-intact" if receipt == expected else "drift"


def runtime(executable: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "unknown",
        "manager": "unknown",
        "version": None,
        "artifact": {"state": "unknown"},
        "loaded_version": None,
    }
    if executable is None:
        result["state"] = "missing"
        return result
    try:
        meta, raw = read_file(executable)
        result["executable"] = meta
        line = raw[: LIMITS["shebang_bytes"]].split(b"\n", 1)[0]
        if not re.fullmatch(rb"#!/[^\s]+", line) or line == b"#!/usr/bin/env":
            return result
        interpreter = Path(os.fsdecode(line[2:]))
        result["interpreter"] = str(interpreter)
        environment = Path(meta["resolved"]).parent.parent
        if Path(meta["resolved"]).parent.name != "bin":
            return result
        if not os.path.lexists(environment / "pyvenv.cfg"):
            result["state"] = "not-a-venv"
            return result
        _, cfg = read_file(environment / "pyvenv.cfg", boundary=environment)
        result["python_version"] = next(
            (
                line.split(" = ", 1)[1]
                for line in cfg.decode().splitlines()
                if line.startswith(("version = ", "version_info = "))
            ),
            None,
        )
        library = environment / "lib"
        if not resolved(library).is_relative_to(resolved(environment)):
            raise ObservationError("link-escape")
        candidates = []
        for python_dir in bounded_children(library):
            if python_dir.name.startswith("python") and python_dir.is_dir():
                if not resolved(python_dir).is_relative_to(resolved(environment)):
                    raise ObservationError("link-escape")
                site = python_dir / "site-packages"
                if site.is_dir():
                    if not resolved(site).is_relative_to(resolved(environment)):
                        raise ObservationError("link-escape")
                    candidates.extend(
                        p
                        for p in bounded_children(site)
                        if p.name.startswith("fenric-") and p.name.endswith(".dist-info")
                    )
        if len(candidates) != 1:
            result["state"] = "conflict" if candidates else "unknown"
            return result
        dist = candidates[0]
        if not resolved(dist).is_relative_to(resolved(environment)):
            raise ObservationError("link-escape")
        _, metadata = read_file(dist / "METADATA", boundary=environment)
        parsed = email.parser.BytesParser().parsebytes(metadata)
        if parsed.get_all("Name") != ["fenric"] or len(parsed.get_all("Version", [])) != 1:
            raise ObservationError("invalid-metadata")
        result.update(state="observed", version=parsed["Version"], distribution=str(dist))
        # Closed list of manager records, read without launching any manager.
        managers = []
        records = []
        for filename in ("uv-receipt.toml", "pipx_metadata.json"):
            p = environment / filename
            if not os.path.lexists(p):
                continue
            measured, body = read_file(p, boundary=environment)
            data = tomllib.loads(body.decode()) if filename.endswith("toml") else strict_json(body)
            named = (
                any(
                    item.get("name") == "fenric"
                    for item in data.get("tool", {}).get("requirements", [])
                )
                if filename.endswith("toml")
                else data.get("main_package", {}).get("package") == "fenric"
            )
            if named:
                managers.append(
                    {
                        "kind": "uv" if filename.endswith("toml") else "pipx",
                        "evidence": measured,
                        "trust": "untrusted-local-metadata",
                    }
                )
            records.append(
                {
                    "file": filename,
                    "state": "recognized" if named else "mismatch",
                    "evidence": measured,
                }
            )
        stage_receipt = environment.parent / "STAGING-RECEIPT.json"
        if os.path.lexists(stage_receipt):
            measured, body = read_file(stage_receipt, boundary=environment.parent)
            data = strict_json(body)
            if (
                data.get("cli") == str(resolved(executable))
                and data.get("version") == result["version"]
            ):
                managers.append(
                    {
                        "kind": "staged-venv",
                        "evidence": measured,
                        "trust": "untrusted-local-metadata",
                    }
                )
            records.append(
                {
                    "file": "STAGING-RECEIPT.json",
                    "state": "recognized"
                    if data.get("cli") == str(resolved(executable))
                    and data.get("version") == result["version"]
                    else "mismatch",
                    "evidence": measured,
                }
            )
        result["manager_records"] = records
        result["manager"] = (
            managers[0] if len(managers) == 1 else "conflict" if managers else "unknown"
        )
        direct = dist / "direct_url.json"
        if os.path.lexists(direct):
            _, body = read_file(direct, boundary=environment)
            source = strict_json(body)
            url = urlsplit(source.get("url", ""))
            claimed = source.get("archive_info", {}).get("hashes", {}).get("sha256")
            if url.scheme == "file" and not url.netloc and not url.query and not url.fragment:
                wheel = Path(unquote(url.path))
                if wheel.suffix != ".whl" or resolved(wheel).parent != resolved(environment.parent):
                    result["artifact"] = {"state": "unknown", "reason": "outside-selected-stage"}
                    return result
                measured, wheel_bytes = read_file(wheel, LIMITS["artifact_bytes"])
                result["artifact"] = {
                    "state": (
                        "unclaimed"
                        if claimed is None
                        else "observed"
                        if claimed == measured["sha256"]
                        else "mismatch"
                    ),
                    "sha256": measured["sha256"],
                    "path": str(wheel),
                    "trust": "untrusted-local-metadata",
                }
                if wheel.suffix == ".whl" and claimed == measured["sha256"]:
                    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
                        result["artifact"]["installed_bytes"] = wheel_matches(archive, dist.parent)
    except (
        OSError,
        ValueError,
        RuntimeError,
        UnicodeError,
        TypeError,
        AttributeError,
        zipfile.BadZipFile,
    ) as exc:
        result["state"] = reason(exc)
    return result


def wheel_matches(archive: zipfile.ZipFile, site: Path) -> str:
    members = [i for i in archive.infolist() if i.filename.startswith("fenric/") and not i.is_dir()]
    if len(members) > LIMITS["entries"] or len({i.filename for i in members}) != len(members):
        raise ObservationError("limit-exceeded")
    total = 0
    expected = {}
    for item in members:
        if ".." in Path(item.filename).parts or item.file_size > LIMITS["tree_bytes"]:
            raise ObservationError("invalid-artifact")
        total += item.file_size
        if total > LIMITS["artifact_bytes"]:
            raise ObservationError("limit-exceeded")
        expected[item.filename] = digest(archive.read(item))
    installed = tree(site / "fenric", skill=False)
    if installed["state"] != "observed":
        return installed["state"]
    found = {
        "fenric/" + e["path"]: e["sha256"]
        for e in installed["entries"]
        if e["type"] == "file" and "__pycache__" not in Path(e["path"]).parts
    }
    if expected != found:
        return "mismatch"
    return "matched-selected-members" if members else "unknown"


def bounded_children(path: Path) -> list[Path]:
    children = []
    with os.scandir(path) as scan:
        for item in scan:
            item.name.encode("utf-8", errors="strict")
            children.append(path / item.name)
            if len(children) > LIMITS["siblings"]:
                raise ObservationError("limit-exceeded")
    return sorted(children, key=lambda p: p.name.encode())


def inventory(home: Path, project: Path | None, executable: Path | None) -> dict[str, Any]:
    scopes = {"global": resolved(home)}
    if project is not None:
        scopes["project"] = resolved(project)
    if any(not p.is_dir() for p in scopes.values()):
        raise ValueError("invalid-input")
    selected = [
        (scope, name, base / relative / name)
        for scope, base in scopes.items()
        for relative in ROOTS
        for name in SKILLS
    ]
    canonical_roots = set()
    for _, _, p in selected:
        try:
            if p.is_dir() and not p.is_symlink():
                canonical_roots.add(resolved(p))
        except (OSError, ValueError, RuntimeError):
            continue
    skills = []
    for scope, name, path in selected:
        try:
            base = scopes[scope]
            actual = resolved(path)
            if not actual.is_relative_to(base):
                raise ObservationError("link-escape")
            if path.parent.is_dir():
                siblings = bounded_children(path.parent)
                if any(p.name.casefold() == name.casefold() and p.name != name for p in siblings):
                    raise ObservationError("name-mismatch")
            if path.is_symlink() and not actual.exists():
                item = {
                    "path": str(path),
                    "resolved": str(actual),
                    "state": "broken-link",
                    "link": os.readlink(path),
                }
            elif path.is_symlink() and actual not in canonical_roots:
                item = {
                    "path": str(path),
                    "resolved": str(actual),
                    "state": "alias-review",
                    "link": os.readlink(path),
                }
            else:
                item = tree(path)
                if path.is_symlink():
                    item["link"] = os.readlink(path)
        except (OSError, ValueError, RuntimeError, UnicodeError) as exc:
            item = {"path": str(path), "state": reason(exc)}
        item.update(scope=scope, name=name)
        item["location"] = path.relative_to(scopes[scope]).as_posix()
        item["ownership"] = ownership(item, name, scope, item["location"])
        skills.append(item)
    conflicts = []
    for name in SKILLS:
        present = [s for s in skills if s["name"] == name and s["state"] != "missing"]
        present_scopes = {s["scope"] for s in present}
        if len(present_scopes) > 1:
            conflicts.append(
                {
                    "name": name,
                    "reason": "scope-or-target-conflict",
                    "paths": sorted(s["path"] for s in present),
                }
            )
    result = {
        "schema": "fenric.lifecycle-inventory.v1",
        "fenric_version": __version__,
        "limits": LIMITS,
        "inputs": {
            "home": str(home),
            "resolved_home": str(scopes["global"]),
            "project": str(project) if project else None,
            "resolved_project": str(scopes.get("project")) if project else None,
            "executable": str(executable) if executable else None,
        },
        "runtime": runtime(executable),
        "skills": skills,
        "conflicts": conflicts,
        "project_shadowing": "observed" if project else "unobserved",
        "harness_loaded_skills": "unknown",
        "native_adapter": "unverified",
    }
    result["complete"] = (
        all(s["state"] not in INCOMPLETE for s in skills)
        and result["runtime"]["state"] not in INCOMPLETE
    )
    return result


def dry_run(
    observed: dict[str, Any],
    scope: str,
    *,
    remove: bool = False,
    targets: tuple[str, ...] = ("claude", "codex"),
) -> dict[str, Any]:
    if scope == "project" and observed["inputs"]["project"] is None:
        raise ValueError("project-required")
    desired = {
        name: tree(Path(__file__).parent / "_guidance" / name, resources=True) for name in SKILLS
    }
    if any(s["state"] != "observed" for s in desired.values()):
        raise ObservationError("packaged-skill-unavailable")
    operations = []
    for item in observed["skills"]:
        if item["scope"] != scope:
            continue
        roots = {"claude": ROOTS[0], "agents": ROOTS[1], "codex": ROOTS[2]}
        base = Path(
            observed["inputs"]["resolved_home"]
            if scope == "global"
            else observed["inputs"]["resolved_project"]
        )
        if not any(Path(item["path"]) == base / roots[t] / item["name"] for t in targets):
            continue
        action, why = "blocked", item["state"]
        same = item.get("bundle_sha256") == desired[item["name"]]["bundle_sha256"]
        conflicting = any(c["name"] == item["name"] for c in observed["conflicts"])
        if item["state"] == "missing":
            action = "no-op" if remove else "add"
            why = "absent"
            if not remove and not Path(item["path"]).parent.is_dir():
                action, why = "blocked", "parent-missing"
        elif item["state"] == "observed" and not conflicting:
            if not remove and same:
                action, why = "no-op", "identical"
            elif item["ownership"] == "owned-intact":
                action, why = ("remove" if remove else "update"), "owned-intact"
            else:
                why = item["ownership"]
        if conflicting:
            action, why = "blocked", "scope-or-target-conflict"
        operations.append(
            {
                "path": item["path"],
                "name": item["name"],
                "scope": scope,
                "action": action,
                "reason": why,
                "ownership": item["ownership"],
                "expected": item,
                "parent": parent_identity(Path(item["path"]).parent),
                "desired_bundle_sha256": None if remove else desired[item["name"]]["bundle_sha256"],
            }
        )
    for operation in operations:
        item = operation["expected"]
        aliases = [o for o in operations if o["expected"].get("resolved") == item.get("resolved")]
        if operation["action"] in ("add", "update", "remove") and (
            item.get("link") is not None or len(aliases) > 1
        ):
            operation.update(action="blocked", reason="duplicate-alias")
    core = {
        "schema": "fenric.lifecycle-plan.v1",
        "scope": scope,
        "remove": remove,
        "targets": sorted(set(targets)),
        "inventory": observed,
        "complete": observed["complete"],
        "desired": desired,
        "operations": operations,
        "runtime_operation": "manual-only; no runtime change proposed",
        "status": (
            "blocked"
            if any(o["action"] == "blocked" for o in operations)
            else "incomplete"
            if not observed["complete"]
            else "dry-run"
        ),
        "authority": "none; data only; no apply endpoint",
    }
    return {**core, "plan_sha256": digest(canonical(core))}


def parent_identity(path: Path) -> dict[str, Any]:
    try:
        return {"resolved": str(resolved(path)), "identity": identity(path.stat())}
    except OSError as exc:
        return {"state": reason(exc)}


def configure_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="lifecycle_command", required=True)
    for name in ("inventory", "plan"):
        child = commands.add_parser(name, allow_abbrev=False)
        child.add_argument("--home", help="explicit existing home; defaults to HOME")
        child.add_argument("--project", help="explicit existing project; no ancestor search")
        child.add_argument("--executable", help="selected executable to inspect, never run")
        if name == "plan":
            child.add_argument("--scope", choices=("global", "project"), required=True)
            child.add_argument(
                "--remove", action="store_true", help="propose owned skill removal only"
            )
            child.add_argument(
                "--target",
                choices=("claude", "codex", "agents"),
                action="append",
                help="selected conventional skill root; repeat; default claude,codex",
            )


def execute(args: Any) -> int:
    try:
        home = args.home or os.environ.get("HOME")
        if not home:
            raise ValueError("home-required")
        selected = args.executable or shutil.which("fenric")
        observed = inventory(
            Path(home).absolute(),
            Path(args.project).absolute() if args.project else None,
            Path(selected).absolute() if selected else None,
        )
        result = (
            dry_run(
                observed,
                args.scope,
                remove=args.remove,
                targets=tuple(sorted(set(args.target or ("claude", "codex")))),
            )
            if args.lifecycle_command == "plan"
            else observed
        )
        incomplete = (
            any(s["state"] in INCOMPLETE for s in observed["skills"])
            or observed["runtime"]["state"] in INCOMPLETE
        )
        code = 4 if incomplete or result.get("status") == "blocked" else 0
        payload = canonical(result).decode() + "\n"
        sys.stdout.write(payload)
        return code
    except (OSError, ValueError, RuntimeError, UnicodeError, MemoryError, RecursionError):
        print("fenric: error: lifecycle observation unavailable or invalid input", file=sys.stderr)
        return 3
