"""Explicit, reviewed setup of shared agent instructions; no native authority."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

BEGIN = "<!-- fenric-guidance:begin -->\n"
END = "<!-- fenric-guidance:end -->\n"
SCHEMA = "fenric.guidance-plan.v1"
READ_PATHS = {"claude": ".claude/CLAUDE.md", "codex": ".codex/AGENTS.md"}
UNMANAGED = re.compile(r"(?m)^#{1,6} Fenric(?:[ \t—:-].*)?$", re.IGNORECASE)


class GuidanceError(ValueError):
    """Setup refused without inventing ownership or authority."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def template() -> str:
    return (Path(__file__).parent / "_guidance/instructions.md").read_text(encoding="utf-8")


def home_path(value: str | None) -> Path:
    """Explicit home, or HOME only; never fall back to another account's database entry."""
    selected = value if value is not None else os.environ.get("HOME")
    if not selected:
        raise GuidanceError("home unavailable; select --home explicitly")
    return Path(selected).resolve(strict=True)


def merged(text: str, body: str, remove: bool) -> tuple[str, str]:
    """Replace only hash-verified owned content; other Fenric sections need human review."""
    begins, ends = text.count(BEGIN), text.count(END)
    if begins or ends:
        if begins != 1 or ends != 1:
            raise GuidanceError("duplicate or incomplete managed guidance")
        start = text.index(BEGIN)
        finish = text.index(END) + len(END)
        owned = text[start + len(BEGIN) : finish - len(END)]
        metadata, separator, previous = owned.partition("\n")
        match = re.fullmatch(
            r"<!-- fenric-guidance:sha256=([a-f0-9]{64});join=([01]) -->", metadata
        )
        if start >= finish or not separator or not match or digest(previous.encode()) != match[1]:
            raise GuidanceError("managed guidance modified; review user edits")
        if not remove and UNMANAGED.search(text[:start] + text[finish:]):
            raise GuidanceError("conflicting unmanaged Fenric section; review user edits")
        join = match[2]
        if join == "1":
            if not start or text[start - 1] != "\n":
                raise GuidanceError("managed guidance separator modified")
            if remove:
                start -= 1
        block = (
            BEGIN
            + f"<!-- fenric-guidance:sha256={digest(body.encode())};join={join} -->\n"
            + body
            + END
        )
        return text[:start] + ("" if remove else block) + text[finish:], "managed"
    # A bare mention/path is not guidance. A heading is a conservative review lead,
    # never proof of equivalent instructions or installer ownership.
    if UNMANAGED.search(text):
        raise GuidanceError("unmanaged Fenric section; reconcile explicitly, no duplicate appended")
    if remove:
        return text, "absent"
    separator = "" if not text or text.endswith("\n") else "\n"
    join = "1" if separator else "0"
    block = (
        BEGIN
        + f"<!-- fenric-guidance:sha256={digest(body.encode())};join={join} -->\n"
        + body
        + END
    )
    return text + separator + block, "absent"


def snapshot(path: Path) -> dict[str, Any]:
    """Measure the expected instruction read path, not live harness consumption."""
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise GuidanceError("instruction target must be an owned regular file with one hard link")
    raw = resolved.read_bytes()
    raw.decode("utf-8")
    after = resolved.stat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise GuidanceError("instruction target changed during read")
    return {
        "path": str(path),
        "resolved": str(resolved),
        "link": os.readlink(path) if path.is_symlink() else None,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "sha256": digest(raw),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def preview(home: Path, *, remove: bool = False) -> dict[str, Any]:
    """Build the exact proposed plan without changing any instruction file."""
    body = template()
    paths, files, seen = [], [], set()
    for harness, relative in READ_PATHS.items():
        path = home / relative
        try:
            measured = snapshot(path)
            text = Path(measured["resolved"]).read_bytes().decode("utf-8")
            updated, present = merged(text, body, remove)
            identity = measured["device"], measured["inode"]
            paths.append({"harness": harness, "state": "resolved", **measured})
            if identity not in seen:
                seen.add(identity)
                files.append(
                    {
                        "target": measured["resolved"],
                        "before_sha256": measured["sha256"],
                        "after_sha256": digest(updated.encode()),
                        "mode": measured["mode"],
                        "uid": measured["uid"],
                        "gid": measured["gid"],
                        "guidance": present,
                        "diff": "".join(
                            line
                            if line.endswith("\n")
                            else line + "\n\\ No newline at end of file\n"
                            for line in difflib.unified_diff(
                                text.splitlines(True),
                                updated.splitlines(True),
                                fromfile=measured["resolved"],
                                tofile=measured["resolved"],
                                n=0,
                            )
                        ),
                    }
                )
        except FileNotFoundError:
            paths.append(
                {
                    "harness": harness,
                    "path": str(path),
                    "state": "broken-link" if path.is_symlink() else "missing",
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, GuidanceError) else "unreadable or invalid target"
            paths.append(
                {"harness": harness, "path": str(path), "state": "conflict", "reason": reason}
            )
    return {
        "schema": SCHEMA,
        "home": str(home),
        "remove": remove,
        "template_sha256": digest(body.encode()),
        "read_paths": paths,
        "files": files,
        "native_adapter": "unverified",
    }


def write_private(path: Path, raw: bytes) -> None:
    """Create one private output without overwriting a prior plan or backup."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise GuidanceError("output file already exists; choose a new name") from exc
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _replace(path: Path, raw: bytes, expected: dict[str, Any]) -> None:
    """Atomic per-file replacement; backups cover partial multi-file completion."""
    fd, name = tempfile.mkstemp(prefix=".fenric-guidance-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, expected["mode"])
        if (temporary.stat().st_uid, temporary.stat().st_gid) != (expected["uid"], expected["gid"]):
            os.chown(temporary, expected["uid"], expected["gid"])
        if snapshot(path)["sha256"] != expected["before_sha256"]:
            raise GuidanceError("instruction content changed before replacement")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(home: Path, plan: dict[str, Any], backup_dir: Path) -> dict[str, Any]:
    """Apply an unchanged preview once, preserving symlinks and private original bytes."""
    if not isinstance(plan, dict) or type(plan.get("remove")) is not bool:
        raise GuidanceError("invalid guidance plan")
    current = preview(home, remove=plan["remove"])
    if plan != current:
        raise GuidanceError("stale or changed guidance plan; preview again")
    if not current["files"] or any(p["state"] == "conflict" for p in current["read_paths"]):
        raise GuidanceError("instruction coverage conflict; no files changed")
    changing = [f for f in current["files"] if f["before_sha256"] != f["after_sha256"]]
    if not changing:
        return {"status": "unchanged", "changed": [], "native_adapter": "unverified"}
    # The explicitly selected fresh directory is local backup state, not auth config.
    try:
        backup_dir.mkdir(mode=0o700, exist_ok=False)
    except (FileExistsError, FileNotFoundError) as exc:
        raise GuidanceError("backup directory must be new inside an existing directory") from exc
    originals = {}
    backups = []
    for index, item in enumerate(changing):
        path = Path(item["target"])
        original = path.read_bytes()
        if digest(original) != item["before_sha256"]:
            raise GuidanceError("instruction content changed before backup")
        originals[str(path)] = original
        backup = backup_dir / f"{index}.before.md"
        write_private(backup, original)
        backups.append({"target": str(path), "backup": str(backup), "sha256": digest(original)})
    write_private(backup_dir / "manifest.json", (json.dumps(backups, indent=2) + "\n").encode())
    if preview(home, remove=plan["remove"]) != current:
        raise GuidanceError("instruction identity changed after backup; no files changed")
    completed = []
    try:
        for item in changing:
            target = Path(item["target"])
            aliases = [p for p in current["read_paths"] if p.get("resolved") == str(target)]
            if any(
                snapshot(Path(p["path"]))
                != {k: v for k, v in p.items() if k not in {"harness", "state"}}
                for p in aliases
            ):
                raise GuidanceError("instruction read path changed before replacement")
            raw, _ = merged(originals[str(target)].decode(), template(), plan["remove"])
            _replace(target, raw.encode(), item)
            completed.append(str(target))
            for alias in aliases:
                observed = snapshot(Path(alias["path"]))
                if (
                    observed["resolved"] != str(target)
                    or observed["link"] != alias["link"]
                    or observed["sha256"] != item["after_sha256"]
                    or observed["mode"] != item["mode"]
                ):
                    raise GuidanceError("instruction readback mismatch")
    except (OSError, RuntimeError, ValueError) as exc:
        # Keep precise completion evidence even when the caller receives a refusal.
        write_private(
            backup_dir / "outcome.json",
            (
                json.dumps(
                    {
                        "status": "unknown-or-partial",
                        "completed": completed,
                        "pending": [f["target"] for f in changing if f["target"] not in completed],
                    },
                    indent=2,
                )
                + "\n"
            ).encode(),
        )
        raise GuidanceError(
            "apply failed; inspect private backup outcome, do not blindly retry"
        ) from exc
    result = {
        "status": "applied",
        "changed": completed,
        "coverage_complete": all(p["state"] == "resolved" for p in current["read_paths"]),
        "native_adapter": "unverified",
    }
    write_private(backup_dir / "outcome.json", (json.dumps(result, indent=2) + "\n").encode())
    return result


def check(home: Path) -> dict[str, Any]:
    plan = preview(home)
    states = []
    for entry in plan["read_paths"]:
        present = next((f for f in plan["files"] if f["target"] == entry.get("resolved")), None)
        states.append(
            {
                "harness": entry["harness"],
                "path": entry["path"],
                "read_path": entry["state"],
                "guidance": (
                    "update-required"
                    if present
                    and present["guidance"] == "managed"
                    and present["before_sha256"] != present["after_sha256"]
                    else present["guidance"]
                    if present
                    else "unverified"
                ),
                **({"reason": entry["reason"]} if "reason" in entry else {}),
            }
        )
    return {
        "status": "complete"
        if all(s["guidance"] == "managed" for s in states)
        else "needs-setup-or-review",
        "harnesses": states,
        "native_adapter": "unverified",
        "read_path_measurement": "filesystem only; live harness loading not proved",
    }


def configure_parser(parser: Any) -> None:
    commands = parser.add_subparsers(dest="guidance_command", required=True)
    read = commands.add_parser(
        "read", allow_abbrev=False, help="Print installed agent usage guidance."
    )
    read.add_argument("--skill", action="store_true", help="print the optional skill entry point")
    read.add_argument(
        "--installer", action="store_true", help="read the passive installer-use guide or skill"
    )
    for name in ("preview", "apply", "check"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--home", help="home whose existing instruction paths are selected")
        if name in {"preview", "apply"}:
            command.add_argument("--plan", required=True, help="private reviewed plan file")
        if name == "preview":
            command.add_argument(
                "--remove", action="store_true", help="preview owned-block removal"
            )
        if name == "apply":
            command.add_argument("--backup-dir", required=True, help="new private backup directory")


def execute(args: Any) -> int:
    """Print usage guidance or setup metadata; never open a dialog or configure security."""
    try:
        if args.guidance_command == "read":
            name = "SKILL.md" if args.skill else "guide.md"
            print(
                (
                    Path(__file__).parent
                    / "_guidance"
                    / ("fenric-install" if args.installer else "fenric")
                    / name
                ).read_text(encoding="utf-8"),
                end="",
            )
            return 0
        home = home_path(args.home)
        if args.guidance_command == "preview":
            result = preview(home, remove=args.remove)
            write_private(Path(args.plan), (json.dumps(result, indent=2) + "\n").encode())
            print(json.dumps(result, indent=2))
            return (
                0
                if result["files"]
                and not any(p["state"] == "conflict" for p in result["read_paths"])
                else 4
            )
        if args.guidance_command == "apply":
            result = apply(home, json.loads(Path(args.plan).read_bytes()), Path(args.backup_dir))
        else:
            result = check(home)
        print(json.dumps(result, indent=2))
        return 4 if result.get("status") == "needs-setup-or-review" else 0
    except (OSError, RuntimeError, ValueError) as exc:
        reason = (
            "packaged guide unavailable"
            if args.guidance_command == "read"
            else "guidance setup unavailable"
        )
        if isinstance(exc, GuidanceError):
            reason = str(exc)
        print(f"fenric: error: {reason}", file=sys.stderr)
        return 3
