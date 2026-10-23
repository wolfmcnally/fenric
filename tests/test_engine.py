"""The request flow: one engine behind the command and the library."""

from __future__ import annotations

from pathlib import Path

import pytest

import fenric
from fenric.engine import render_text, run
from fenric.spec import validate_spec

from .support import (
    ScriptedBackend,
    cancels,
    expires,
    install_bun_stub,
    returns,
    submits,
    unavailable,
)

CONFIRM = {"verb": "confirm", "message": "Proceed?"}


def test_outcomes_map_to_the_contracts_exit_codes(tmp_path: Path, monkeypatch) -> None:
    answered = run(CONFIRM, backend=submits("yes"))
    assert (answered.status, answered.exit_code, answered.value) == ("ok", 0, True)
    cancelled = run(CONFIRM, backend=cancels())
    assert (cancelled.status, cancelled.exit_code, cancelled.value) == ("cancelled", 2, None)
    expired = run(CONFIRM, backend=expires())
    assert (expired.status, expired.exit_code, expired.detail) == ("unavailable", 4, "timed out")
    absent = run(CONFIRM, backend=unavailable("no display"))
    assert (absent.status, absent.exit_code, absent.detail) == ("unavailable", 4, "no display")
    import re
    import urllib.error
    import urllib.request
    from html import unescape
    from urllib.parse import urlsplit

    from fenric.backend import CANCELLED, EXPIRED, SUBMITTED, UNAVAILABLE, Outcome

    original = b"<!doctype html><h1>Unchanged report</h1>"
    (tmp_path / "index.html").write_bytes(original)
    spec = {"verb": "html", "root": str(tmp_path), "entry": "index.html", "files": ["index.html"]}
    for outcome, expected in [
        (Outcome(SUBMITTED, {"action": "ok", "values": {}}), 0),
        (Outcome(CANCELLED), 2),
        (Outcome(EXPIRED), 4),
        (RuntimeError("SENTINEL"), 4),
        (Outcome(UNAVAILABLE), 4),
    ]:
        urls = []

        class Inspect:
            def show(self, page, session):
                url = re.search(r'id="fenric-html"[^>]*src="([^"]+)"', page.html)[1]
                urls.append(url)
                parsed = urlsplit(url)
                prefix = (
                    parsed.scheme
                    + "://"
                    + parsed.netloc
                    + parsed.path.split("/files/")[0]
                    + "/files/"
                )
                csp = unescape(
                    re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page.html)[
                        1
                    ]
                )
                frames = [
                    part.strip() for part in csp.split(";") if part.strip().startswith("frame-src")
                ]
                assert frames == ["frame-src " + prefix]
                assert urllib.request.urlopen(url).read() == original
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        assert run(spec, backend=Inspect()).exit_code == expected
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(urls[0], timeout=0.2)

    # Failure after the desktop assets listener binds still closes that listener.
    from fenric import engine

    actual_transport = engine.Transport
    bound = []

    def observed_transport(*args, **kwargs):
        listener = actual_transport(*args, **kwargs)
        bound.append(listener.origin + listener.files_path + "index.html")
        return listener

    def failed_start(self):
        raise OSError("SENTINEL")

    with monkeypatch.context() as patch:
        patch.setattr(engine, "Transport", observed_transport)
        patch.setattr(engine.Remote, "start", failed_start)
        renderer = submits("ok")
        result = run(spec, backend=renderer)
        assert result.exit_code == 4 and "SENTINEL" not in result.detail
        assert renderer.pages == [] and len(bound) == 1
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(bound[0], timeout=0.2)


def test_the_library_entry_point_is_the_same_engine() -> None:
    backend = submits(q0="because")
    result = fenric.run(
        {"verb": "ask", "app": "Library", "questions": [{"id": "why", "prompt": "Why?"}]},
        backend=backend,
    )
    assert isinstance(result, fenric.Result)
    assert result.value == {"why": "because"}
    assert len(backend.pages) == 1 and backend.pages[0].title == "Library"


def test_an_invalid_spec_is_exit_3_before_any_window(tmp_path: Path, monkeypatch) -> None:
    backend = submits("yes")
    result = run({"verb": "confirm", "message": "m", "timeout": -1}, backend=backend)
    assert (result.status, result.exit_code) == ("error", 3)
    assert result.detail == "timeout must be greater than 0 and at most 3600 seconds"
    assert backend.pages == []
    import os
    import time

    from fenric import html_bundle

    (tmp_path / "index.html").write_text("<h1>Report</h1>")
    (tmp_path / "outside.html").write_text("SENTINEL")
    (tmp_path / "link.html").symlink_to(tmp_path / "outside.html")
    (tmp_path / "directory").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(tmp_path / "pipe.html")
    (tmp_path / "bad.html").write_bytes(b"\xff")
    spec = {"verb": "html", "root": str(tmp_path), "entry": "index.html", "files": ["index.html"]}
    started = time.monotonic()
    for name in [
        "../outside.html",
        "/outside.html",
        "link.html",
        "directory/outside.html",
        "pipe.html",
        "bad.html",
        "missing.html",
        "command.py",
        "./index.html",
    ]:
        result = run({**spec, "files": ["index.html", name]}, backend=backend)
        assert result.exit_code == 3 and "SENTINEL" not in result.detail
        assert name not in result.detail and str(tmp_path) not in result.detail
    (tmp_path / "loop").symlink_to("loop")
    loop = run({**spec, "root": str(tmp_path / "loop")}, backend=backend)
    assert loop.exit_code == 3 and str(tmp_path) not in loop.detail
    nul = run({**spec, "root": "bad\0root"}, backend=backend)
    assert nul.exit_code == 3 and nul.detail.startswith("root ")
    assert time.monotonic() - started < 1 and backend.pages == []
    with monkeypatch.context() as patch:
        patch.setattr(html_bundle, "MAX_FILE_BYTES", 4)
        assert run(spec, backend=backend).exit_code == 3
    with monkeypatch.context() as patch:
        patch.setattr(html_bundle, "MAX_TOTAL_BYTES", 4)
        assert run(spec, backend=backend).exit_code == 3


def test_a_malformed_result_is_exit_3() -> None:
    result = run(CONFIRM, backend=returns({"action": "maybe", "values": {}}))
    assert (result.status, result.exit_code, result.value) == ("error", 3, None)
    assert result.detail == "result.action is not an answer to this dialog"
    assert run(CONFIRM, backend=returns("SENTINEL")).detail == "result is not a result object"


def test_headless_is_unavailable_without_asking_the_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FENRIC_HEADLESS", "1")
    backend = submits("yes")
    result = run(CONFIRM, backend=backend)
    assert (result.status, result.exit_code, result.detail) == ("unavailable", 4, "headless")
    assert backend.pages == []
    invalid = run({"verb": "confirm"}, backend=backend)
    assert (invalid.exit_code, invalid.detail) == (3, "message is required")
    (tmp_path / "index.html").write_text("Report")

    def no_transport(*args, **kwargs):
        raise AssertionError("headless must not bind")

    monkeypatch.setattr("fenric.engine.Transport", no_transport)
    html = {"verb": "html", "root": str(tmp_path), "entry": "index.html", "files": ["index.html"]}
    assert run(html, backend=backend).detail == "headless"


def test_the_fallback_file_holds_a_plain_text_rendering(tmp_path: Path) -> None:
    payload = "\ufeff\tcopy exactly\r\nend\n"
    assert render_text(
        validate_spec({"verb": "notify", "message": "Copy", "copy_text": payload})
    ).endswith(payload)
    spec = {
        "verb": "ask",
        "app": "Deploy bot",
        "message": "Before the release",
        "questions": [
            {"id": "go", "prompt": "Ship it?", "options": ["Yes", "No"], "other": True},
            {"id": "note", "prompt": "Anything to add?", "detail": "Optional context"},
        ],
    }
    target = tmp_path / "request.txt"
    result = run(spec, backend=expires(), fallback_file=target)
    assert result.exit_code == 4
    assert target.read_text() == render_text(validate_spec(spec))
    assert target.read_text() == (
        "Deploy bot asks (ask)\n"
        "Before the release\n"
        "1. Ship it?\n"
        "   - Yes\n"
        "   - No\n"
        "   - Something else\n"
        "2. Anything to add?\n"
        "   Optional context\n"
    )
    wizard = {
        **spec,
        "presentation": "wizard",
        "questions": [
            spec["questions"][0],
            {**spec["questions"][1], "when": {"question": "go", "equals": "Yes"}},
        ],
    }
    assert run(wizard, backend=expires(), fallback_file=target).exit_code == 4
    assert (
        "Only when question 1 is active and its offered option 'Yes' is selected."
        in target.read_text()
    )
    from fenric.gallery import load_gallery

    gallery = load_gallery(Path(__file__).parents[1] / "docs/examples/image-comments.json")
    assert run(gallery, backend=expires(), fallback_file=target).exit_code == 4
    assert target.read_text().count("an image is attached; it cannot be shown as text") == 2
    assert "data:image" not in target.read_text()
    untouched = tmp_path / "answered.txt"
    assert run(CONFIRM, backend=submits("yes"), fallback_file=untouched).exit_code == 0
    assert not untouched.exists()


def test_a_backend_that_raises_is_unavailable() -> None:
    result = run(CONFIRM, backend=ScriptedBackend(RuntimeError("SENTINEL display lost")))
    assert (result.status, result.exit_code, result.detail) == (
        "unavailable",
        4,
        "render failure",
    )


def test_a_chart_is_compiled_before_the_backend_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_bun_stub(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    fenric.charts.setup_charts()

    backend = submits()
    bad = run({"verb": "chart", "code": "TYPE_ERROR SENTINEL"}, backend=backend)
    assert (bad.exit_code, bad.detail) == (3, "the chart code did not pass the type check")
    assert backend.pages == []

    good = run({"verb": "chart", "code": "draw(data);", "data": [1, 2]}, backend=backend)
    assert (good.status, good.exit_code, good.value) == ("ok", 0, None)
    (page,) = backend.pages
    assert 'id="fenric-chart"' in page.html and "Content-Security-Policy" not in page.html
    assert "(() =&gt; {draw(data);})();" in page.html


def test_an_unusable_file_target_is_exit_3_before_any_window(tmp_path: Path) -> None:
    target = tmp_path / "token.json"
    target.write_text('{"token": {"access": "SENTINEL"}}')
    backend = submits(f0="SENTINEL")
    result = run({"verb": "secret", "fields": ["token"], "into": str(target)}, backend=backend)
    assert (result.status, result.exit_code, result.value) == ("error", 3, None)
    assert result.detail == "the target file holds JSON that is not a flat object of strings"
    assert backend.pages == []
    assert target.read_text() == '{"token": {"access": "SENTINEL"}}'


def test_only_filled_fields_reach_the_file_and_one_bad_value_writes_none(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("FIRST=1\nSECOND=2\n")
    spec = {"verb": "secret", "fields": ["FIRST", "SECOND", "THIRD"], "into": str(target)}
    result = run(spec, backend=submits(f0="new", f1="", f2="added"))
    assert (result.status, result.exit_code, result.value, result.detail) == ("ok", 0, None, None)
    assert target.read_text() == "FIRST=new\nSECOND=2\nTHIRD=added\n"

    result = run(spec, backend=submits(f0="SENTINEL-fine", f1="", f2="SENTINEL\nbroken"))
    assert (result.status, result.exit_code, result.value) == ("error", 3, None)
    assert (
        result.detail
        == "fields[2]: the value holds a line break or a NUL, which the target file cannot hold"
    )
    assert target.read_text() == "FIRST=new\nSECOND=2\nTHIRD=added\n"
    assert run(spec, backend=submits(f0="", f1="", f2="")).status == "ok"
    assert [entry.name for entry in tmp_path.iterdir()] == [".env"]
