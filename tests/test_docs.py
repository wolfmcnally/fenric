"""The user documentation shows what the Fenric that ships actually does.

The user documentation is ``README.md`` and ``docs/``. Every ``bash`` fence in
it holds one command. A ``fenric`` command is followed by one or more capture
markers, HTML comments that render as nothing:

    <!-- capture {"answer": {...}, "exit": 0, "setup": {...}, "file": ".env"} -->

Each marker says how to reproduce what the documentation shows next: the
first fence after it is exactly what the command prints (standard output, then
standard error, omitting only the optional remote-not-configured status), and
with ``file`` the second fence is that file's content
afterwards. ``answer`` is what the window hands back (the page's payload), or
``"cancel"``, ``"expire"`` or ``"headless"``; without it no window may open.
``setup`` writes files before the run. A file without a final newline is shown
with one, since a fence always ends its last line. Every capture is rerun here, in a fresh
copy of ``docs/examples``, and must match byte for byte. A command whose
capture answers a dialog shows that dialog's image, rendered from the same
command by ``python preview/run.py docs``.

To refresh every capture after a deliberate change, run this file with
``FENRIC_UPDATE_DOCS=1``: it rewrites the captured fences in place and fails,
so an updating run can never pass as a check.

Commands that need the network or the real chart toolchain are listed in
``EXTERNAL`` and observed outside the gate; any other command fails here.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

from fenric.backend import HARD_LIMIT_SECONDS, SAFETY_NET_SECONDS
from fenric.charts import TOOL_TIMEOUT_SECONDS, setup_charts
from fenric.cli import build_parser, main
from fenric.gallery import load_gallery
from fenric.spec import _COMPONENTS, MAX_TIMEOUT_SECONDS, VERB_FIELDS, validate_spec
from fenric.store import STORE_TIMEOUT_SECONDS

from .support import cancels, expires, install_bun_stub, returns

PROJECT = Path(__file__).resolve().parents[1]
DOCS = PROJECT / "docs"
USER_DOCS = (PROJECT / "README.md", *sorted(DOCS.glob("*.md")))
FENCE = re.compile(r"^```(\w*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
CAPTURE = re.compile(r"^<!-- capture (\{.*\}) -->$")
IMAGE = re.compile(r"^!\[[^\]]+\]\((?:docs/)?images/([\w-]+)\.png\)$")
DIALOG_ANSWERS = ("cancel", "expire")

# Commands that need the network or the real chart toolchain, with what
# observes them. They are run outside the gate before a release.
VERSION = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["version"]
EXTERNAL = {
    "uv tool install --reinstall .": "operator-run replacement from the reviewed source directory",
    "pipx reinstall fenric": "operator-run replacement from the recorded source and options",
    "uv tool uninstall fenric": "operator-run removal by the owning uv tool manager",
    "pipx uninstall fenric": "operator-run removal by the owning pipx tool manager",
    "fenric remote off": "operator-run remote configuration change; no real settings in this gate",
    "uv tool install .": "clean tool install from the project directory",
    f"uv tool install fenric-{VERSION}-py3-none-any.whl": "clean tool install of the built wheel",
    "pipx install .": "clean pipx install from the project directory",
    "fenric remote setup": "bounded tailnet HTTPS/owner-header probe observed outside the gate",
    "fenric setup-charts": "provisioning the chart toolchain with Bun and the network",
}


@dataclass
class Fence:
    language: str
    body: str
    start: int  # index of the opening line
    end: int  # index of the closing line


@dataclass
class Capture:
    spec: dict
    line: int
    command: str
    outputs: list[Fence] = field(default_factory=list)


@dataclass
class Doc:
    path: Path
    lines: list[str]
    commands: list[tuple[Fence, list[str], list[Capture]]]  # fence, images, captures


def parse(path: Path) -> Doc:
    lines = path.read_text().split("\n")
    items: list[tuple[str, object]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        opening = re.fullmatch(r"```(\w*)", line)
        if opening:
            close = lines.index("```", index + 1)
            body = "".join(f"{text}\n" for text in lines[index + 1 : close])
            items.append(("fence", Fence(opening.group(1), body, index, close)))
            index = close + 1
            continue
        if match := CAPTURE.match(line):
            items.append(("capture", (json.loads(match.group(1)), index)))
        elif match := IMAGE.match(line):
            items.append(("image", match.group(1)))
        index += 1
    commands: list[tuple[Fence, list[str], list[Capture]]] = []
    pending: Capture | None = None
    for kind, item in items:
        if kind == "fence" and item.language == "bash":
            commands.append((item, [], []))
            pending = None
        elif kind == "fence" and pending is not None:
            pending.outputs.append(item)
            if len(pending.outputs) == (2 if "file" in pending.spec else 1):
                pending = None
        elif kind == "capture":
            assert commands, f"{path.name}: a capture with no command before it"
            spec, line_number = item
            pending = Capture(spec, line_number, commands[-1][0].body.strip())
            commands[-1][2].append(pending)
        elif kind == "image" and commands and not commands[-1][2]:
            commands[-1][1].append(item)
    return Doc(path, lines, commands)


DOCUMENTS = [parse(path) for path in USER_DOCS]


def answers_a_dialog(capture: Capture) -> bool:
    answer = capture.spec.get("answer")
    return isinstance(answer, dict) or answer in DIALOG_ANSWERS


def run_capture(
    capture: Capture,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str | None]:
    """Run one capture's command in a fresh workspace; return exit, output, file."""
    workspace = Path(tempfile.mkdtemp(dir=root))
    shutil.copytree(DOCS / "examples", workspace / "examples")
    for name, content in capture.spec.get("setup", {}).items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    answer = capture.spec.get("answer")
    window = {"cancel": cancels, "expire": expires}.get(answer) if isinstance(answer, str) else None
    with monkeypatch.context() as scoped:
        scoped.chdir(workspace)
        scoped.setenv("COLUMNS", "80")
        scoped.delenv("FENRIC_HEADLESS", raising=False)
        if answer == "headless":
            scoped.setenv("FENRIC_HEADLESS", "1")
        elif isinstance(answer, dict):
            scoped.setattr("fenric.backend.default_backend", lambda: returns(answer))
        elif window is not None:
            scoped.setattr("fenric.backend.default_backend", window)
        words = shlex.split(capture.command)
        while words and re.fullmatch(r"[A-Z_]+=.*", words[0]):
            name, _, value = words.pop(0).partition("=")
            scoped.setenv(name, value)
        assert words[:1] == ["fenric"], f"not a fenric command and not listed: {capture.command}"
        capsys.readouterr()
        try:
            status = main(words[1:])
        except SystemExit as exit_:
            status = int(exit_.code or 0)
        printed = capsys.readouterr()
    written = None
    if "file" in capture.spec:
        target = workspace / capture.spec["file"]
        written = target.read_text() if target.exists() else "(no file)\n"
    # These examples show the command result, not the fixture host's optional
    # phone setup. Keep all other stderr, including actual command errors, and
    # never strip a matching line from stdout (it could be caller content).
    stderr = "".join(
        line
        for line in printed.err.splitlines(keepends=True)
        if line != "fenric: remote: unavailable: not configured\n"
    )
    return status, printed.out + stderr, written


@pytest.fixture
def chart_toolchain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bun = tmp_path / "bun"
    install_bun_stub(bun)
    monkeypatch.setenv("PATH", f"{bun}:/usr/bin:/bin")
    setup_charts()


def test_every_documented_command_runs_as_written(
    chart_toolchain: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The capture policy must not hide command failures or caller content.
    # Exercise the actual capture boundary, then restore the real CLI for all
    # documented commands below.
    def streams(_args: list[str]) -> int:
        print("fenric: remote: unavailable: not configured")
        print("fenric: remote: unavailable: not configured", file=sys.stderr)
        print("fenric: error: invalid request", file=sys.stderr)
        print("fenric: remote: unavailable: configuration error", file=sys.stderr)
        return 3

    with monkeypatch.context() as scoped:
        scoped.setattr(sys.modules[__name__], "main", streams)
        result = run_capture(Capture({}, 0, "fenric notify"), tmp_path, scoped, capsys)
    assert result == (
        3,
        "fenric: remote: unavailable: not configured\n"
        "fenric: error: invalid request\n"
        "fenric: remote: unavailable: configuration error\n",
        None,
    )
    updating = os.environ.get("FENRIC_UPDATE_DOCS") == "1"
    mismatches, seen = [], 0
    for doc in DOCUMENTS:
        replacements: dict[int, tuple[int, str]] = {}
        for fence, _, captures in doc.commands:
            command = fence.body.strip()
            assert "\n" not in command, f"{doc.path.name}: one command per fence"
            if command in EXTERNAL:
                continue
            assert captures, f"{doc.path.name}: no capture after `{command}`"
            for capture in captures:
                seen += 1
                expected = 2 if "file" in capture.spec else 1
                assert len(capture.outputs) == expected, f"{doc.path.name}:{capture.line + 1}"
                status, printed, written = run_capture(capture, tmp_path, monkeypatch, capsys)
                where = f"{doc.path.name}:{capture.line + 1} `{command}`"
                if status != capture.spec.get("exit", 0):
                    mismatches.append(f"{where}: exit {status}")
                for shown, actual in zip(capture.outputs, (printed, written), strict=False):
                    # A fence always ends its last line, so a file without a
                    # final newline is shown with one.
                    if actual and not actual.endswith("\n"):
                        actual += "\n"
                    if shown.body != actual:
                        mismatches.append(f"{where}: shows {shown.body!r}, prints {actual!r}")
                        replacements[shown.start] = (shown.end, actual)
        if updating and replacements:
            lines = list(doc.lines)
            for start in sorted(replacements, reverse=True):
                end, actual = replacements[start]
                lines[start + 1 : end] = actual.rstrip("\n").split("\n") if actual else []
            doc.path.write_text("\n".join(lines))
    assert seen >= 40
    assert not updating, "captures rewritten; rerun without FENRIC_UPDATE_DOCS"
    assert mismatches == []


def test_every_external_command_is_still_documented() -> None:
    documented = {fence.body.strip() for doc in DOCUMENTS for fence, _, _ in doc.commands}
    assert set(EXTERNAL) <= documented


def test_each_worked_example_prints_what_the_documentation_shows() -> None:
    """Every worked example answers its dialog, shows it, and shows a cancel or a refusal."""
    (examples,) = [doc for doc in DOCUMENTS if doc.path.name == "examples.md"]
    sections = re.split(r"^## ", "\n".join(examples.lines), flags=re.MULTILINE)[1:]
    assert len(sections) >= 7
    for fence, images, captures in examples.commands:
        assert images, f"no image for `{fence.body.strip()}`"
        assert any(answers_a_dialog(capture) for capture in captures), fence.body
    answers = [capture.spec.get("answer") for _, _, cs in examples.commands for capture in cs]
    assert "cancel" in answers and "expire" in answers


def test_public_api_tutorial_matches_exports_and_runs_without_a_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fenric

    document = (DOCS / "api.md").read_text()
    reference = sections(DOCS / "api.md", "##")
    assert first_column(reference["Public exports"]) == set(fenric.__all__)
    assert first_column(sections(DOCS / "api.md", "###")["Result"]) == {
        item.name for item in dataclass_fields(fenric.Result)
    }
    assert first_column(reference["run_integrated and IntegratedResult"]) == {
        item.name for item in dataclass_fields(fenric.IntegratedResult)
    }
    assert first_column(reference["run"]) == {
        name for name in inspect.signature(fenric.run).parameters if not name.startswith("_")
    }
    snippets = [body for language, body in FENCE.findall(document) if language == "python"]
    assert len(snippets) == 3
    shutil.copytree(DOCS / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    # The actual tutorial runs for both boolean answers and cancellation.
    # Invalid-input and pure gallery examples must never ask for a renderer.
    for action in ("yes", "no", "cancel"):
        backend = cancels() if action == "cancel" else returns({"action": action, "values": {}})
        with monkeypatch.context() as scoped:
            scoped.setattr("fenric.backend.default_backend", lambda: backend)
            namespace: dict = {}
            exec(compile(snippets[0], "api.md:confirm", "exec"), namespace)
            result = namespace["result"]
            assert result.status == ("cancelled" if action == "cancel" else "ok")
            assert result.value is (None if action == "cancel" else action == "yes")
            assert len(backend.pages) == 1
    for snippet in snippets[1:]:
        exec(compile(snippet, "api.md:pure", "exec"), {})

    # Packaged agent recipes use the same actual API; they are not new action executors.
    import yaml

    directory = PROJECT / "fenric/_guidance/fenric"
    guide = (directory / "guide.md").read_text()
    skill = (directory / "SKILL.md").read_text()
    metadata = yaml.safe_load(skill.split("---\n", 2)[1])
    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == directory.name == "fenric"
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"])
    assert 1 <= len(metadata["name"]) <= 64
    assert 1 <= len(metadata["description"]) <= 1024
    for target in re.findall(r"\]\(([^)]+)\)", skill):
        assert "/" not in target and (directory / target).is_file()
    specs = []
    for language, body in FENCE.findall(guide):
        if language == "json":
            raw = json.loads(body)
            if "images" in raw:
                # Gallery manifests have their own public loader, not a verb field.
                gallery = tmp_path / "agent-gallery"
                gallery.mkdir()
                for image, fixture in zip(
                    raw["images"], ("gallery-first.png", "gallery-second.png"), strict=True
                ):
                    shutil.copyfile(DOCS / "examples" / fixture, gallery / image["file"])
                manifest = gallery / "gallery.json"
                manifest.write_text(json.dumps(raw))
                raw = load_gallery(manifest)
            validate_spec(raw)
            specs.append(raw)
        elif language == "bash":
            argv = shlex.split(body)
            assert argv.pop(0) == "fenric" and "\n" not in body.strip()
            build_parser().parse_args(argv)
    review = next(raw for raw in specs if raw["verb"] == "ask")
    for decision, index in (("decline", 0), ("approve", 1)):
        for comment in (
            "",
            "Thanks!",
            "Use a different subject",
            "Only after review",
            "Do not send",
            "  Café with blank lines\n\nLast line.\n  ",
        ):
            backend = returns({"action": "ok", "values": {"q0": index, "q1": comment}})
            with monkeypatch.context() as scoped:
                scoped.setattr("fenric.backend.default_backend", lambda: backend)
                result = fenric.run(review, desktop_only=True)
            assert (result.exit_code, result.value) == (
                0,
                {"decision": decision, "comments": comment},
            )
    for backend, code in ((cancels(), 2), (expires(), 4), (returns({"decision": "approve"}), 3)):
        with monkeypatch.context() as scoped:
            scoped.setattr("fenric.backend.default_backend", lambda: backend)
            result = fenric.run(review, desktop_only=True)
        assert result.exit_code == code and result.value is None


def test_every_dialog_command_shows_its_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from preview.run import _documentation_destination

    monkeypatch.setattr("socket.gethostname", lambda: "private-render-host")
    target = tmp_path / ".env"
    prepared = _documentation_destination(
        validate_spec({"verb": "secret", "fields": ["API_KEY"], "into": str(target)})
    )
    assert prepared.target.path == target.resolve()
    assert prepared.metadata["host"] == "Example Mac"
    assert prepared.metadata["display"] == "/example/.env"
    shown = json.dumps(prepared.metadata)
    assert str(tmp_path) not in shown and "private-render-host" not in shown
    assert not target.exists()
    assert (
        _documentation_destination(validate_spec({"verb": "notify", "message": "Example"})).metadata
        is None
    )
    referenced = set()
    for doc in DOCUMENTS:
        for fence, images, captures in doc.commands:
            if any(answers_a_dialog(capture) for capture in captures):
                assert len(images) == 1, f"{doc.path.name}: `{fence.body.strip()}` needs one image"
            for name in images:
                assert (DOCS / "images" / f"{name}.png").is_file(), name
                referenced.add(name)
    assert sorted(path.stem for path in (DOCS / "images").glob("*.png")) == sorted(referenced)


def test_every_example_spec_validates_and_is_shown_with_its_image() -> None:
    examples = (DOCS / "examples.md").read_text()
    for path in sorted((DOCS / "examples").glob("*.json")):
        raw = json.loads(path.read_text())
        if isinstance(raw, dict) and ("verb" in raw or "images" in raw):
            gallery = "images" in raw and "verb" not in raw
            validate_spec(load_gallery(path) if gallery else raw)
            shown = re.search(
                rf"`examples/{path.stem}\.json`:\n\n```json\n(.*?)\n```", examples, re.DOTALL
            )
            assert shown and json.loads(shown.group(1)) == raw, path.name
            flag = "--gallery-file" if gallery else "--spec-file"
            assert f"{flag} examples/{path.stem}.json\n```\n\n![" in examples, path.name
    for name in re.findall(r"\]\((examples/[^)]+)\)", examples):
        assert (DOCS / name).exists(), name


def test_documented_limits_match_the_code() -> None:
    commands = (DOCS / "commands.md").read_text()
    security = (DOCS / "security.md").read_text()
    hard, net, longest = (
        f"{HARD_LIMIT_SECONDS:g}",
        f"{SAFETY_NET_SECONDS:g}",
        f"{MAX_TIMEOUT_SECONDS + SAFETY_NET_SECONDS:g}",
    )
    for text in (
        f"it is {hard} seconds",
        f"N + {net} seconds",
        f"the largest timeout, {MAX_TIMEOUT_SECONDS}, a window can stay open for {longest}",
        f"at most {MAX_TIMEOUT_SECONDS}",
    ):
        assert text in commands, text
    for text in (
        f"{hard} seconds by default",
        f"N + {net} seconds",
        f"limited to {TOOL_TIMEOUT_SECONDS:g} seconds and each store command to "
        f"{STORE_TIMEOUT_SECONDS:g}",
    ):
        assert text in security, text


def sections(path: Path, level: str) -> dict[str, str]:
    """Each heading of ``level`` (such as ``###``) and the text under it."""
    parts = re.split(rf"^{level} (.+)$", path.read_text(), flags=re.MULTILINE)
    return {
        heading.strip(): re.split(r"^#{1,3} ", body, flags=re.MULTILINE)[0]
        for heading, body in zip(parts[1::2], parts[2::2], strict=True)
    }


def first_column(text: str) -> set[str]:
    """The backticked names in the first column of the first table in ``text``."""
    rows, started = [], False
    for line in text.splitlines():
        if line.startswith("|"):
            started = True
            rows.append(line)
        elif started:
            break
    names: set[str] = set()
    for row in rows[2:]:
        names.update(re.findall(r"`([^`]+)`", row.split("|")[1]))
    return names


def test_every_option_is_documented_and_every_documented_option_exists() -> None:
    parser = build_parser()
    verbs = parser._subparsers._group_actions[0].choices
    reference = sections(DOCS / "commands.md", "###")
    common = first_column(sections(DOCS / "commands.md", "##")["Options for standalone invocation"])

    def options(command_parser) -> set[str]:
        return {
            flag
            for action in command_parser._actions
            for flag in action.option_strings
            if flag.startswith("--") and flag != "--help"
        }

    assert common == options(parser) - {"--version"}
    for name, verb_parser in verbs.items():
        documented = first_column(reference[name]) if name != "setup-charts" else set()
        assert documented == options(verb_parser) - common, name
        if verb_parser._subparsers is not None:
            commands = verb_parser._subparsers._group_actions[0].choices
            for command, command_parser in commands.items():
                assert first_column(reference[f"{name} {command}"]) == options(command_parser)
    assert set(verbs) <= set(reference)


def test_every_spec_field_is_documented_and_every_documented_field_exists() -> None:
    reference = sections(DOCS / "spec.md", "###")
    every = first_column(sections(DOCS / "spec.md", "##")["Every spec"])
    assert every == {"verb", "app", "timeout"}
    for verb, fields in VERB_FIELDS.items():
        assert first_column(reference[verb]) == set(fields), verb
    catalog = sections(DOCS / "spec.md", "##")["Form catalog, version 1"]
    rows = {}
    for row in [line for line in catalog.splitlines() if line.startswith("|")][2:]:
        cells = row.split("|")
        rows[cells[1].strip(" `")] = set(re.findall(r"`(\w+)`", cells[2]))
    assert rows == {kind: set(allowed) for kind, (allowed, _) in _COMPONENTS.items()}
