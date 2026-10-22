"""Image manifests and direct image questions at their public collection boundaries."""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from fenric import backend
from fenric.cli import main
from fenric.engine import run
from fenric.spec import SpecError, validate_spec

from .support import cancels, submits

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"
PREFIX = "data:image/png;base64,"
SENTINEL = "PRIVATE-GALLERY-SENTINEL"


def test_manifest_collection(tmp_path, monkeypatch, capsys):
    for case in [
        "success",
        "reverse",
        "optional",
        "required",
        "length",
        "unicode-limit",
        "extra",
        "cancel",
        "duplicate",
        "missing",
        "oversize",
        "fifo",
        "directory",
        "manifest-size",
        "bad-json",
        "deep-json",
        "mix",
    ]:
        with monkeypatch.context() as scoped:
            _manifest_collection_case(case, tmp_path / case, scoped, capsys)


def _manifest_collection_case(case, tmp_path, monkeypatch, capsys):
    root = tmp_path / "screens"
    root.mkdir(parents=True)
    shutil.copy(EXAMPLES / "gallery-first.png", root / "first.png")
    shutil.copy(EXAMPLES / "gallery-second.png", root / "second.png")
    images = [
        {"id": "mobile", "name": "Mobile", "caption": "Portrait sample", "file": "first.png"},
        {"id": "wide", "name": "Wide", "caption": "Landscape sample", "file": "second.png"},
    ]
    if case == "reverse":
        images.reverse()
    values = {"q0": "first comment", "q1": "second comment"}
    if case in ("optional", "required"):
        values["q1"] = ""
        images[1]["required"] = case == "required"
    if case == "length":
        values["q0"] = "x" * 1001
    if case == "unicode-limit":
        values["q0"] = "\U0001f600" * 500
    if case == "extra":
        values["unknown"] = "unexpected"
    if case == "duplicate":
        images[1]["id"] = "mobile"
    if case in ("missing", "oversize", "fifo", "directory"):
        file = root / SENTINEL
        images[0]["file"] = file.name
        if case == "oversize":
            with file.open("wb") as handle:
                handle.truncate(2 * 1024 * 1024 + 1)
        elif case == "fifo":
            os.mkfifo(file)
        elif case == "directory":
            file.mkdir()
    manifest = root / "review.json"
    manifest.write_text(json.dumps({"images": images}))
    if case == "manifest-size":
        manifest.write_text(" " * 65537)
    if case == "bad-json":
        manifest.write_text(SENTINEL)
    if case == "deep-json":
        manifest.write_text("[" * 2000 + "]" * 2000)
    fake = cancels() if case == "cancel" else submits(**values)
    monkeypatch.setattr(backend, "default_backend", lambda: fake)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FENRIC_HEADLESS", raising=False)
    arguments = ["ask", "--gallery-file", str(manifest), "--desktop-only"]
    if case == "mix":
        arguments += ["--question", SENTINEL]
    if case == "fifo":
        child = subprocess.run(
            [sys.executable, "-m", "fenric.cli", *arguments],
            capture_output=True,
            text=True,
            timeout=3,
        )
        code, out, err = child.returncode, child.stdout, child.stderr
    else:
        code = main(arguments)
        out, err = capsys.readouterr()
    err = err.removeprefix("fenric: remote: unavailable: disabled\n")
    invalid = case in {
        "required",
        "length",
        "extra",
        "duplicate",
        "missing",
        "oversize",
        "fifo",
        "directory",
        "manifest-size",
        "bad-json",
        "deep-json",
        "mix",
    }
    assert SENTINEL not in out + err
    if invalid:
        assert code == 3 and not out and "error" in err
        if case in ("fifo", "oversize", "manifest-size"):
            assert "must be a bounded regular file" in err
        if case == "directory":
            assert "could not read a bounded regular file" in err
        if case not in ("required", "length", "extra"):
            assert fake.pages == []
    elif case == "cancel":
        assert code == 2 and out == "cancelled\n" and not err
    else:
        assert code == 0 and not err
        expected = {"mobile": values["q0"], "wide": values["q1"]}
        if case == "reverse":
            expected = {"wide": "first comment", "mobile": "second comment"}
        assert json.loads(out) == expected
        assert "Caller image" in fake.pages[0].html
        assert "Portrait sample" in fake.pages[0].html


def _chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def test_inline_images():
    for case in [
        "valid",
        "choice",
        "url",
        "base64",
        "crc",
        "dimensions",
        "animated",
        "trailing",
        "no-data",
        "count",
        "total",
        "pixels",
        "alt",
        "image-size",
        "markup",
        "utf16-too-long",
    ]:
        _inline_images_case(case)


def _inline_images_case(case):
    png = (EXAMPLES / "gallery-first.png").read_bytes()
    question = {
        "id": "shot",
        "prompt": "Screenshot",
        "required": False,
        "multiline": True,
        "image": {"data": PREFIX + base64.b64encode(png).decode(), "alt": "Screenshot"},
    }
    if case == "choice":
        question.update(kind="choice", options=["one"])
        del question["multiline"]
    if case == "url":
        question["image"]["data"] = "https://example.invalid/" + SENTINEL
    if case == "base64":
        question["image"]["data"] = PREFIX + SENTINEL
    if case == "crc":
        png = png[:29] + bytes([png[29] ^ 1]) + png[30:]
    if case == "dimensions":
        png = png[:8] + _chunk(b"IHDR", struct.pack(">IIBBBBB", 8193, 1, 8, 2, 0, 0, 0)) + png[33:]
    if case == "animated":
        png = png[:33] + _chunk(b"acTL", struct.pack(">II", 1, 0)) + png[33:]
    if case == "trailing":
        png += b"extra"
    if case == "no-data":
        png = png[:33] + _chunk(b"IEND", b"")
    if case in ("crc", "dimensions", "animated", "trailing", "no-data"):
        question["image"]["data"] = PREFIX + base64.b64encode(png).decode()
    if case == "alt":
        question["image"]["alt"] = ""
    if case == "image-size":
        question["image"]["data"] = PREFIX + "A" * (4 * ((2 * 1024 * 1024 + 2) // 3) + 4)
    if case == "markup":
        question["prompt"] = "<script>alert(1)</script>"
        question["image"]["alt"] = '" onload="alert(1)'
    questions = [question]
    if case == "count":
        questions = [{**question, "id": str(i)} for i in range(11)]
    if case == "total":
        padded = png[:33] + _chunk(b"tEXt", b"comment\0" + b"x" * 1_800_000) + png[33:]
        data = PREFIX + base64.b64encode(padded).decode()
        questions = [
            {**question, "id": str(i), "image": {"data": data, "alt": "Image"}} for i in range(5)
        ]
    if case == "pixels":
        large = (
            png[:8] + _chunk(b"IHDR", struct.pack(">IIBBBBB", 4000, 4000, 8, 2, 0, 0, 0)) + png[33:]
        )
        data = PREFIX + base64.b64encode(large).decode()
        questions = [
            {**question, "id": str(i), "image": {"data": data, "alt": "Image"}} for i in range(2)
        ]
    raw = {"verb": "ask", "presentation": "wizard", "questions": questions}
    if case not in ("valid", "markup", "utf16-too-long"):
        with pytest.raises(SpecError) as error:
            validate_spec(raw)
        assert SENTINEL not in str(error.value)
        return
    fake = submits(q0="\U0001f600" * 501 if case == "utf16-too-long" else "Looks good")
    result = run(raw, backend=fake, desktop_only=True)
    if case == "utf16-too-long":
        assert result.exit_code == 3 and result.value is None
        return
    assert result.value == {"shot": "Looks good"}
    html = fake.pages[0].html
    assert "img-src data:; frame-src &#x27;none&#x27;" in html
    assert "<script>alert(1)</script>" not in html
    assert '" onload="alert(1)' not in html
    assert 'maxlength="1000"' in html
