"""The ``fenric`` command: invocation forms, output, and exit status."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

from fenric.cli import main
from fenric.spec import VERBS

from .support import ScriptedBackend, cancels, submits

PICK = {"verb": "pick", "message": "Which?", "options": ["red", "green"]}


def scripted(monkeypatch: pytest.MonkeyPatch, backend: ScriptedBackend) -> ScriptedBackend:
    monkeypatch.setattr("fenric.backend.default_backend", lambda: backend)
    return backend


def test_help_exits_zero_and_names_the_program(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("help must not start a dialog or configure a server")

    monkeypatch.setattr("sys.argv", ["fenric"])
    monkeypatch.setattr("fenric.cli.run", unexpected)
    monkeypatch.setattr("fenric.cli.remote.setup", unexpected)
    monkeypatch.setattr("fenric.cli.setup_charts", unexpected)
    monkeypatch.setattr("fenric.cli.CallerSignals", unexpected)
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert captured.out.startswith("usage: fenric")
    assert captured.err == ""
    for argv in ([], None):
        assert main(argv) == 0
        bare = capsys.readouterr()
        assert bare.out == captured.out and bare.err == ""
    monkeypatch.setattr("fenric.guidance.home_path", unexpected)
    monkeypatch.delenv("HOME", raising=False)
    for args in ([], ["--skill"]):
        assert main(["guidance", "read", *args]) == 0
        read = capsys.readouterr()
        assert read.out.startswith("---\n" if args else "# Working with people")
        assert read.err == ""

    def missing_resource(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("synthetic missing packaged resource")

    with monkeypatch.context() as scoped:
        scoped.setattr("fenric.guidance.Path.read_text", missing_resource)
        assert main(["guidance", "read"]) == 3
    failed = capsys.readouterr()
    assert failed.out == "" and failed.err == "fenric: error: packaged guide unavailable\n"


def test_help_lists_every_verb_and_the_placeholder_is_gone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    listed = capsys.readouterr().out
    assert len(VERBS) == 9
    for verb in (*VERBS, "setup-charts"):
        assert f"\n    {verb} " in listed
    assert main(["version"]) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("fenric: error: invalid invocation")


def test_the_four_invocation_forms_reach_the_engine_alike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(PICK))
    forms = [
        ["pick", "--message", "Which?", "--option", "red", "--option", "green"],
        ["--spec", json.dumps(PICK)],
        ["--spec", "-"],
        ["--spec-file", str(spec_file)],
    ]
    pages = []
    for form in forms:
        backend = scripted(monkeypatch, submits(choice=1))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(PICK)))
        assert main(form) == 0
        assert capsys.readouterr().out == '"green"\n'
        pages.append(backend.pages[0].html)
    assert len(set(pages)) == 1
    payload = '\ufeff\t"literal"\r\nend\n'
    copy_file = tmp_path / "payload.txt"
    copy_file.write_bytes(payload.encode("utf-8"))
    backend = scripted(monkeypatch, submits("ok"))
    assert main(["notify", "--message", "Copy", "--copy-file", str(copy_file)]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert base64.b64encode(payload.encode()).decode() in backend.pages[0].html
    assert (
        main(["notify", "--message", "Copy", "--copy-file", str(copy_file), "--copy-text", "other"])
        == 3
    )
    assert "error" in capsys.readouterr().err
    assert (
        main(
            [
                "notify",
                "--message",
                "Copy",
                "--spec",
                json.dumps({"copy_text": "spec"}),
                "--copy-file",
                str(copy_file),
            ]
        )
        == 3
    )
    assert "give the copy payload once" in capsys.readouterr().err
    copy_file.write_bytes(b"\xff")
    assert main(["notify", "--message", "Copy", "--copy-file", str(copy_file)]) == 3
    assert "could not be read" in capsys.readouterr().err


def test_verb_options_override_spec_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = scripted(monkeypatch, submits("yes"))
    spec = json.dumps({"message": "from the spec", "app": "Spec app", "yes_label": "Go"})
    arguments = ["confirm", "--spec", spec, "--message", "from the option", "--app", "Flag app"]
    assert main([*arguments, "--timeout", "9"]) == 0
    assert capsys.readouterr().out == "true\n"
    page = backend.pages[0]
    assert "from the option" in page.html and "from the spec" not in page.html
    assert (page.title, page.timeout) == ("Flag app", 9.0)
    assert ">Go</button>" in page.html
    assert main(["notify", "--spec", json.dumps({"verb": "confirm", "message": "m"})]) == 3
    assert "names a different verb" in capsys.readouterr().err


def test_a_usage_error_exits_3_without_echoing_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for arguments in (
        ["confirm", "--mesage", "SENTINEL"],
        ["SENTINEL-verb"],
        ["notify", "--message", "m", "--timeout", "SENTINEL"],
        ["--spec", "{SENTINEL not json"],
        ["--spec-file", "no-such-SENTINEL.json"],
        ["--spec", "{}", "--spec-file", "SENTINEL"],
        ["--app", "SENTINEL"],
        ["remote"],
        ["--"],
    ):
        assert main(arguments) == 3
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("fenric: error: ") and captured.err.count("\n") == 1
        assert "SENTINEL" not in captured.err


def test_a_collecting_verb_prints_json_and_a_showing_verb_prints_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripted(monkeypatch, submits(q0="né", q1="x"))
    assert main(["ask", "--question", "Name?", "--question", "Note?"]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        '{"q1":"né","q2":"x"}\n',
        "fenric: remote: unavailable: not configured\n",
    )

    scripted(monkeypatch, submits())
    assert main(["notify", "--message", "Done"]) == 0
    assert capsys.readouterr().out == "ok\n"


def test_cancel_and_unavailable_print_a_status_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripted(monkeypatch, cancels())
    assert main(["confirm", "--message", "Proceed?"]) == 2
    assert capsys.readouterr().out == "cancelled\n"

    monkeypatch.setenv("FENRIC_HEADLESS", "1")
    fallback = tmp_path / "fallback.txt"
    assert main(["notify", "--message", "hello", "--fallback-file", str(fallback)]) == 4
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        "unavailable: headless\n",
        "fenric: remote: unavailable: headless\n",
    )
    assert fallback.read_text() == "Fenric asks (notify)\nhello\n"


def test_secret_without_a_store_command_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["secret", "--field", "token=API token"]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "fenric: error: store or into is required: "
        "the secret verb needs a store command or a file\n"
    )


def test_secret_into_writes_the_file_and_prints_only_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "service.env"
    target.write_text("# keys\nAPI_KEY=old\n")
    monkeypatch.setattr(
        "fenric.backend.default_backend", lambda: submits(f0="SENTINEL new", f1="SENTINEL-2")
    )
    arguments = ["secret", "--field", "API_KEY=API key", "--field", "OTHER", "--into", str(target)]
    assert main(arguments) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("ok\n", "fenric: remote: unavailable: not configured\n")
    assert target.read_text() == "# keys\nAPI_KEY='SENTINEL new'\nOTHER=SENTINEL-2\n"

    assert main([*arguments, "--store", "keep {name}"]) == 3
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        "",
        "fenric: error: into cannot be given together with store\n",
    )
