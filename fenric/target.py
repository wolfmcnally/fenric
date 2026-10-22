"""Write secrets into a file the caller names, instead of a store command.

The target's format is decided from its content: dotenv (``KEY=VALUE`` lines,
with or without ``export``), a flat JSON object of strings, or a single
value. A key that exists has its value replaced in place and a key that does
not is appended in the file's own style; every other byte is preserved. A
file in any other shape is refused and left untouched.

Three things never happen here. No diagnostic quotes the path, a key or a
value. The target is never observable half-written: the new content goes to
an owner-only temporary file beside it, which is renamed over it. And a
value is never written in a form its reader would load as something else.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_TARGET_BYTES = 1 << 20
NEW_FILE_MODE = 0o600
DOTENV, FLAT_JSON, SINGLE_VALUE = "dotenv", "flat JSON", "single value"
UNSUPPORTED = "the target file is not in a supported format"

_KEY = r"[A-Za-z_][A-Za-z0-9_]*"
_ASSIGNMENT = re.compile(
    rf"(?P<lead>[ \t]*(?:export[ \t]+)?)(?P<key>{_KEY})(?P<sep>[ \t]*=[ \t]*)(?P<rest>.*)\Z"
)
# The shell takes a line as an assignment only when ``=`` follows the name
# directly; ``KEY = value`` runs a command called KEY.
_SHELL_ASSIGNMENT = re.compile(
    rf"(?P<lead>[ \t]*(?:export[ \t]+)?)(?P<key>{_KEY})(?P<sep>=)(?P<rest>.*)\Z"
)
_DOTENV_KEY = re.compile(rf"{_KEY}\Z")
_COMMENT = r"(?P<tail>[ \t]*|[ \t]+#.*)\Z"
_QUOTED = r"""'[^']*'|"(?:[^"\\]|\\.)*\""""
# The shell also joins quoted pieces; this is the form a value with a single
# quote is written in, so it must be replaceable on the next update.
_SHELL_QUOTED = r"""'[^']*'(?:\\''[^']*')*|"(?:[^"\\]|\\.)*\""""
# The value forms whose end is unambiguous, so that replacing one cannot keep
# part of the old value or drop a command. The shell reads more characters
# as syntax than a dotenv loader does.
_REPLACEABLE = re.compile(rf"""(?:{_QUOTED}|[^\s#'"]*){_COMMENT}""")
_SHELL_REPLACEABLE = re.compile(rf"""(?:{_SHELL_QUOTED}|[^\s#'"\\`$;&|<>()]*){_COMMENT}""")
# A value that opens a quote it does not close on its line, or ends in a
# backslash, runs on into the lines after it.
_CONTINUED = re.compile(r"""'[^']*\Z|"(?:[^"\\]|\\.)*\\?\Z|(?:[^'"].*)?\\\Z""")
# A direnv helper line in an .envrc (``PATH_add bin``, ``source_up``): one
# command word and simple arguments, with nothing the shell would read as
# quoting, expansion, a second command or a continuation. It is kept as it is.
_SHELL_COMMAND = re.compile(
    r"[ \t]*[A-Za-z_][A-Za-z0-9_-]*(?:[ \t]+[A-Za-z0-9_.:/@%+=,~^-]+)*[ \t]*\Z"
)
# Words that open or close a compound command, and commands that declare or
# remove a variable; a line starting with one is never a simple helper.
_NOT_HELPERS = frozenset(
    "if then else elif fi case esac for select while until do done in function time "
    "export readonly declare typeset local unset".split()
)
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
_BARE_VALUE = re.compile(r"[A-Za-z0-9_.:/@%+=,^-]+\Z")
_JSON_STRING = r'"(?:[^"\\]|\\.)*"'
_JSON_MEMBER = re.compile(rf"(?P<key>{_JSON_STRING})(?P<sep>\s*:\s*)(?P<value>{_JSON_STRING})")


class TargetError(RuntimeError):
    """The target cannot be used or updated. The message names no path, key or value."""


@dataclass(frozen=True)
class _Target:
    path: Path
    text: str
    kind: str
    existing: os.stat_result | None
    shell: bool  # read by the shell alone (direnv), not by a dotenv loader
    export: bool  # new assignments take an ``export`` prefix


@dataclass(frozen=True)
class TargetInfo:
    """Non-secret metadata shown before a file update."""

    path: Path
    kind: str
    exists: bool


def check_target(into: str, names: list[str]) -> TargetInfo:
    """Validate the target and return only its non-secret destination metadata."""
    target = _load(into)
    _check_names(target, names)
    return TargetInfo(target.path, target.kind, target.existing is not None)


def write_target(
    into: str,
    names: list[str],
    filled: list[tuple[int, str]],
    *,
    expected_path: Path | None = None,
    expected_kind: str | None = None,
) -> None:
    """Write each filled field (its index in ``names``, and its value) into the target.

    The file is read again here, because it may have changed while the dialog
    was open. The whole update is computed first, so one value that cannot be
    written leaves the file exactly as it was.
    """
    if not filled:
        return
    target = _load(into)
    if expected_path is not None and target.path != expected_path:
        raise TargetError("the target resolution changed while the dialog was open")
    if expected_kind is not None and target.kind != expected_kind:
        raise TargetError("the target format changed while the dialog was open")
    _check_names(target, names)
    for index, value in filled:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise TargetError(f"fields[{index}]: the value is not valid text") from None
    if target.kind == FLAT_JSON:
        text = _update_json(target.text, [(names[index], value) for index, value in filled])
    else:
        for index, value in filled:
            if any(character in value for character in "\n\r\x00"):
                raise TargetError(
                    f"fields[{index}]: the value holds a line break or a NUL,"
                    " which the target file cannot hold"
                )
        if target.kind == DOTENV:
            text = _update_dotenv(target, names, filled)
        else:
            ending = re.search(r"(?:\r\n|\r|\n)?\Z", target.text)
            text = filled[0][1] + (ending.group() if ending else "")
    _replace(target, text.encode("utf-8"))


def _load(into: str) -> _Target:
    # The name the caller gave decides how a file is read, even through a link.
    name = Path(into).expanduser().name
    try:
        path = Path(into).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        # A link that loops is a RuntimeError here, and its text holds the path.
        raise TargetError("the target file could not be read") from None
    try:
        try:
            existing = path.stat()
        except FileNotFoundError:
            return _new_target(path, name)
        # Checked before opening: a pipe or a device would block or never end.
        if not stat.S_ISREG(existing.st_mode):
            raise TargetError(UNSUPPORTED)
        with path.open("rb") as handle:
            raw = handle.read(MAX_TARGET_BYTES + 1)
    except OSError as exc:
        raise TargetError("the target file could not be read") from exc
    if len(raw) > MAX_TARGET_BYTES:
        raise TargetError(UNSUPPORTED)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TargetError(UNSUPPORTED) from None
    if "\x00" in text:
        raise TargetError(UNSUPPORTED)
    kind, export = _detect(text, name)
    return _Target(path, text, kind, existing, name.endswith(".envrc"), export)


def _new_target(path: Path, name: str) -> _Target:
    if not path.parent.is_dir():
        raise TargetError("the target file's directory does not exist")
    shell = name.endswith(".envrc")
    if shell or name.endswith(".env"):
        return _Target(path, "", DOTENV, None, shell, shell)
    if name.endswith(".json"):
        return _Target(path, "", FLAT_JSON, None, False, False)
    raise TargetError(
        "the target file does not exist and its name does not say which format to create"
    )


def _detect(text: str, name: str) -> tuple[str, bool]:
    """Return the target's format and whether its assignments all take ``export``."""
    if text.lstrip()[:1] in ("{", "["):
        _flat_members(text)
        return FLAT_JSON, False
    lines = [line for line in _lines(text) if line.strip()]
    candidates = [line for line in lines if not line.lstrip().startswith("#")]
    shell = name.endswith(".envrc")
    assignment = _SHELL_ASSIGNMENT if shell else _ASSIGNMENT
    if shell:
        candidates = [line for line in candidates if assignment.match(line) or not _helper(line)]
    if not candidates:
        # Empty, comments, or only helper lines: no assignment shows the style.
        if shell:
            return DOTENV, True
        if name.startswith(".env") or name.endswith(".env"):
            return DOTENV, False
        if not lines and name.endswith(".json"):
            return FLAT_JSON, False
        raise TargetError(UNSUPPORTED)
    assignments = [assignment.match(line) for line in candidates]
    if all(assignments):
        if any(_CONTINUED.match(match["rest"]) for match in assignments if match):
            raise TargetError(UNSUPPORTED)
        return DOTENV, all("export" in match["lead"] for match in assignments if match)
    if len(lines) == 1 and not shell:
        # An .envrc is a shell script: a line it cannot read is never a value.
        return SINGLE_VALUE, False
    raise TargetError(UNSUPPORTED)


def _helper(line: str) -> bool:
    """Whether an .envrc line is a simple helper command that Fenric leaves alone."""
    return bool(_SHELL_COMMAND.match(line)) and line.split()[0] not in _NOT_HELPERS


def _lines(text: str, keepends: bool = False) -> list[str]:
    """Split on the line endings a dotenv reader knows, and on nothing else."""
    lines = _LINE.findall(text)
    return lines if keepends else [line.rstrip("\r\n") for line in lines]


def _check_names(target: _Target, names: list[str]) -> None:
    if target.kind == SINGLE_VALUE and len(names) != 1:
        raise TargetError(
            "the target file holds a single value and the request has more than one field"
        )
    if target.kind != DOTENV:
        return
    replaceable = _SHELL_REPLACEABLE if target.shell else _REPLACEABLE
    assignment = _SHELL_ASSIGNMENT if target.shell else _ASSIGNMENT
    found = [match for match in map(assignment.match, _lines(target.text)) if match]
    for index, name in enumerate(names):
        if not _DOTENV_KEY.match(name):
            raise TargetError(f"fields[{index}].name cannot be a key in the target file's format")
        present = [match for match in found if match["key"] == name]
        if len(present) > 1:
            raise TargetError(
                f"fields[{index}].name appears more than once as a key in the target file"
            )
        if present and not replaceable.match(present[0]["rest"]):
            raise TargetError(
                f"fields[{index}]: the value the target file holds is in a form"
                " that cannot be replaced safely"
            )


def _dotenv_value(value: str, shell: bool) -> str | None:
    """Return ``value`` as its reader will load it unchanged, or None when no form does.

    The shell reads single quotes literally. A dotenv file may also be read
    by a dotenv loader, and those disagree with the shell and with each other
    about escapes and ``${}``; only forms they all read alike are written.
    """
    if _BARE_VALUE.match(value):
        return value
    if shell:
        return "'" + value.replace("'", "'\\''") + "'"
    if "'" not in value:
        if "${" in value or "\\\\" in value or value.endswith("\\"):
            return None
        return f"'{value}'"
    if any(character in value for character in '\\"$`'):
        return None
    return f'"{value}"'


def _update_dotenv(target: _Target, names: list[str], filled: list[tuple[int, str]]) -> str:
    ending = re.search(r"\r\n|\r|\n", target.text)
    newline = ending.group() if ending else "\n"
    replaceable = _SHELL_REPLACEABLE if target.shell else _REPLACEABLE
    lines = _lines(target.text, keepends=True)
    for index, value in filled:
        written = _dotenv_value(value, target.shell)
        if written is None:
            raise TargetError(
                f"fields[{index}]: the value cannot be written so that"
                " every reader of the target file loads it unchanged"
            )
        for position, line in enumerate(lines):
            body = line.rstrip("\r\n")
            match = (_SHELL_ASSIGNMENT if target.shell else _ASSIGNMENT).match(body)
            if match is None or match["key"] != names[index]:
                continue
            old = replaceable.match(match["rest"])
            if old is None:  # _check_names refuses this; never write past it
                raise TargetError(UNSUPPORTED)
            tail = old["tail"]
            lines[position] = (
                f"{match['lead']}{match['key']}{match['sep']}{written}{tail}{line[len(body) :]}"
            )
            break
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(f"{'export ' if target.export else ''}{names[index]}={written}{newline}")
    return "".join(lines)


def _flat_members(text: str) -> list[re.Match[str]]:
    """Return the members of a flat JSON object of strings, as spans of ``text``."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate key")
        return dict(pairs)

    try:
        value = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        raise TargetError(UNSUPPORTED) from None
    if not isinstance(value, dict) or not all(isinstance(item, str) for item in value.values()):
        raise TargetError("the target file holds JSON that is not a flat object of strings")
    members = list(_JSON_MEMBER.finditer(text))
    if [json.loads(member["key"]) for member in members] != list(value):
        raise TargetError(UNSUPPORTED)
    return members


def _update_json(text: str, pairs: list[tuple[str, str]]) -> str:
    """Edit the object as text, so layout and existing escape spellings survive."""

    def encode(item: str) -> str:
        return json.dumps(item, ensure_ascii=text.isascii())

    if not text.strip():
        text = "{}\n"
    for name, value in pairs:
        members = _flat_members(text)
        existing = next((m for m in members if json.loads(m["key"]) == name), None)
        if existing is not None:
            text = text[: existing.start("value")] + encode(value) + text[existing.end("value") :]
        elif members:
            last = members[-1]
            before = text[: last.start()]
            indent = before[len(before.rstrip()) :]
            member = f",{indent}{encode(name)}{last['sep']}{encode(value)}"
            text = text[: last.end()] + member + text[last.end() :]
        else:
            opening, closing = text.index("{"), text.rindex("}")
            text = f"{text[:opening]}{{\n  {encode(name)}: {encode(value)}\n{text[closing:]}"
    return text


def _replace(target: _Target, data: bytes) -> None:
    """Replace the target with ``data`` by renaming a complete temporary file over it.

    The temporary file is created exclusively and owner-only, and is given
    the target's owner, group and mode only once it is complete.
    """
    path, existing = target.path, target.existing
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if existing is not None:
            created = os.stat(temporary)
            if (created.st_uid, created.st_gid) != (existing.st_uid, existing.st_gid):
                os.chown(temporary, existing.st_uid, existing.st_gid)
        os.chmod(temporary, stat.S_IMODE(existing.st_mode) if existing else NEW_FILE_MODE)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                raise TargetError(
                    "the target file could not be written,"
                    " and a temporary file beside it could not be removed"
                ) from None
        raise TargetError("the target file could not be written") from exc
