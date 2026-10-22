"""No diagnostic, in any failure, quotes a value from the spec or the person.

One sweep drives every refusal and failure path with a distinctive sentinel
in every place a value can enter: the spec, the command line, the scripted
person's answers, the compiler's output, the store command's output, and a
file target's path, content and failing filesystem.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from fenric import charts
from fenric.cli import main

from .support import ScriptedBackend, install_bun_stub, returns, submits

SENTINEL = "SENTINEL-7f3a"


def test_no_failure_path_leaks_a_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    install_bun_stub(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    charts.setup_charts()
    store_script = tmp_path / "store.py"
    store_script.write_text(
        "import sys\nprint(sys.stdin.read())\nprint(sys.argv, file=sys.stderr)\nsys.exit(9)\n"
    )
    store = f"{sys.executable} {store_script} {SENTINEL} {{name}}"
    chart_file = tmp_path / "chart.ts"
    chart_file.write_text(f"const n: number = 'TYPE_ERROR {SENTINEL}';")
    unreadable = tmp_path / f"{SENTINEL}-missing.json"
    vault = tmp_path / SENTINEL
    vault.mkdir()
    nested = vault / f"{SENTINEL}.json"
    nested.write_text(json.dumps({SENTINEL: {SENTINEL: SENTINEL}}))
    dotenv = vault / f"{SENTINEL}.env"
    dotenv.write_text(f"FINE={SENTINEL}\nTWICE={SENTINEL}\nTWICE={SENTINEL}\nODD=a {SENTINEL}\n")

    def into(path: Path, *names: str) -> list[str]:
        return spec(verb="secret", fields=list(names), into=str(path))

    def broken_disk() -> None:
        def refuse(*arguments: object, **options: object) -> None:
            raise OSError(SENTINEL)

        person(submits(f0=f"{SENTINEL}-typed"))()
        monkeypatch.setattr("os.replace", refuse)

    def spec(**fields: object) -> list[str]:
        return ["--spec", json.dumps(fields)]

    def person(backend: ScriptedBackend) -> Callable[[], None]:
        return lambda: monkeypatch.setattr("fenric.backend.default_backend", lambda: backend)

    quiet = person(submits())
    ask = {"verb": "ask", "questions": [{"id": SENTINEL, "prompt": SENTINEL, "options": ["a"]}]}
    gallery_manifest = vault / "gallery.json"
    gallery_bad_json = vault / "bad.json"
    gallery_bad_json.write_text(SENTINEL)
    image = vault / "image.png"
    image.write_bytes((Path(__file__).parents[1] / "docs/examples/gallery-first.png").read_bytes())
    broken_image = vault / "broken.png"
    broken_image.write_bytes(image.read_bytes() + SENTINEL.encode())
    pipe = vault / (SENTINEL + "-pipe")
    os.mkfifo(pipe)

    def gallery(file: Path, *, duplicate: bool = False) -> Callable[[], None]:
        def prepare() -> None:
            entry = {"id": SENTINEL, "name": SENTINEL, "file": str(file)}
            gallery_manifest.write_text(
                json.dumps({"images": [entry, entry] if duplicate else [entry]})
            )
            quiet()

        return prepare

    gallery_args = ["ask", "--gallery-file", str(gallery_manifest)]
    image_ask = {
        "verb": "ask",
        "questions": [
            {
                "id": SENTINEL,
                "prompt": SENTINEL,
                "image": {
                    "data": "data:image/png;base64,"
                    + base64.b64encode(image.read_bytes()).decode(),
                    "alt": SENTINEL,
                },
            }
        ],
    }
    # Each case: (arguments, how the scripted person behaves, expected exit status).
    cases: list[tuple[list[str], Callable[[], None], int]] = [
        (["ask", "--gallery-file", str(unreadable)], quiet, 3),
        (["ask", "--gallery-file", str(gallery_bad_json)], quiet, 3),
        (gallery_args, gallery(unreadable), 3),
        (gallery_args, gallery(vault), 3),
        (gallery_args, gallery(pipe), 3),
        (gallery_args, gallery(broken_image), 3),
        (gallery_args, gallery(image, duplicate=True), 3),
        ([*gallery_args, "--question", SENTINEL], quiet, 3),
        ([*gallery_args, "--spec", SENTINEL], quiet, 3),
        (spec(**image_ask), person(submits(q0=SENTINEL * 1000)), 3),
        (spec(verb=SENTINEL), quiet, 3),
        (spec(verb="notify", message=SENTINEL, **{SENTINEL: 1}), quiet, 3),
        (spec(verb="notify", message=[SENTINEL]), quiet, 3),
        (spec(verb="notify", message="m", timeout=SENTINEL), quiet, 3),
        (spec(verb="pick", message="m", options=[SENTINEL, SENTINEL]), quiet, 3),
        (spec(verb="form", components=[{"type": SENTINEL, "label": SENTINEL}]), quiet, 3),
        (spec(verb="form", catalog=SENTINEL, components=[]), quiet, 3),
        (spec(verb="secret", fields=[f"{SENTINEL} bad name"], store="s"), quiet, 3),
        (spec(verb="secret", fields=[{"name": "k", "value": SENTINEL}], store="s"), quiet, 3),
        (spec(verb="secret", fields=["k"]), quiet, 3),
        (spec(verb="chart", code="x", data=SENTINEL, height=SENTINEL), quiet, 3),
        (["confirm", "--message", "m", f"--{SENTINEL}", SENTINEL], quiet, 3),
        ([SENTINEL], quiet, 3),
        (["--spec", "{" + SENTINEL], quiet, 3),
        (["--spec-file", str(unreadable)], quiet, 3),
        (["notify", *spec(verb="confirm", message=SENTINEL)], quiet, 3),
        (["chart", "--code-file", str(chart_file), "--data-file", str(unreadable)], quiet, 3),
        (["chart", "--code-file", str(chart_file)], quiet, 3),
        (spec(**ask), person(returns({"action": "ok", "values": {"q0": SENTINEL}})), 3),
        (spec(**ask), person(returns({"action": SENTINEL, "values": {}})), 3),
        (spec(**ask), person(returns({"action": "ok", "values": {"q0": 0, SENTINEL: 1}})), 3),
        (spec(**ask), person(returns([SENTINEL])), 3),
        (
            spec(verb="secret", fields=["k"], store=store),
            person(submits(f0=f"{SENTINEL}-typed")),
            3,
        ),
        (
            spec(verb="notify", message=SENTINEL),
            person(ScriptedBackend(RuntimeError(SENTINEL))),
            4,
        ),
        (spec(verb="secret", fields=["k"], store="s", into=str(dotenv)), quiet, 3),
        (into(nested, "k"), quiet, 3),
        (into(vault / f"{SENTINEL}-missing", "k"), quiet, 3),
        (into(vault / SENTINEL / ".env", "k"), quiet, 3),
        (into(vault, "k"), quiet, 3),
        (into(dotenv, "TWICE"), quiet, 3),
        (into(dotenv, "ODD"), quiet, 3),
        (into(dotenv, f"{SENTINEL}.dotted"), quiet, 3),
        (into(dotenv, "K"), person(submits(f0=f"{SENTINEL}\nbroken")), 3),
        (into(dotenv, "K"), person(submits(f0=f"it's ${SENTINEL}")), 3),
        (into(dotenv, "K"), broken_disk, 3),
    ]
    assert len(cases) == 45
    errors = []
    for arguments, behave, expected in cases:
        behave()
        assert main(arguments) == expected, arguments
        captured = capfd.readouterr()
        diagnostics = captured.out + captured.err
        assert diagnostics.strip(), arguments
        assert SENTINEL not in diagnostics, (arguments, diagnostics)
        if expected == 3:
            error = captured.err.removeprefix("fenric: remote: unavailable: not configured\n")
            assert captured.out == "" and error.startswith("fenric: error: ")
            assert error.count("\n") == 1
            errors.append(error.removeprefix("fenric: error: ").strip())
    # The file-target cases each reach the refusal they were written for.
    assert errors[-11:] == [
        "into cannot be given together with store",
        "the target file holds JSON that is not a flat object of strings",
        "the target file does not exist and its name does not say which format to create",
        "the target file's directory does not exist",
        "the target file is not in a supported format",
        "fields[0].name appears more than once as a key in the target file",
        "fields[0]: the value the target file holds is in a form that cannot be replaced safely",
        "fields[0].name cannot be a key in the target file's format",
        "fields[0]: the value holds a line break or a NUL, which the target file cannot hold",
        "fields[0]: the value cannot be written so that"
        " every reader of the target file loads it unchanged",
        "the target file could not be written",
    ]
