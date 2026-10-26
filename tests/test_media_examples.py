"""The public media recipes collect/render only under scripted test custody."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import shutil
import struct
import wave
from pathlib import Path

from fenric import backend
from fenric.cli import build_parser, main
from fenric.html_bundle import document_policy, load_bundle

from .support import submits

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs/examples/audio-demo"


def commands(document: Path):
    text = document.read_text()
    for command in re.findall(r"`((?:FENRIC_HEADLESS=1 )?fenric [^`]+)`", text):
        argv = shlex.split(command)
        headless = argv[0] == "FENRIC_HEADLESS=1"
        if headless:
            argv.pop(0)
        assert argv.pop(0) == "fenric"
        # N is the existing integration document's inherited-descriptor notation.
        parsed = ["7" if value == "N" else value for value in argv]
        try:
            build_parser().parse_args(parsed)
        except SystemExit as exit_:
            assert exit_.code == 0 and "--help" in parsed
        yield headless, argv


def documented(document: Path, prefix: list[str], directory: Path):
    argv = next(argv for _, argv in commands(document) if argv[: len(prefix)] == prefix)
    return [
        word.replace("$STATUS_DIR", str(directory)).replace("$CHECK_DIR", str(directory))
        for word in argv
    ]


def test_documented_capability_probe_never_opens_a_surface(tmp_path, monkeypatch, capsys):
    from fenric import html_bundle

    (tmp_path / "index.html").write_text("<!doctype html><title>Probe</title>")
    (tmp_path / "probe.wav").write_bytes(b"")
    monkeypatch.setenv("FENRIC_HEADLESS", "1")
    monkeypatch.setattr(
        "fenric.remote.Remote.start",
        lambda _: (_ for _ in ()).throw(
            AssertionError("capability probe attempted remote startup")
        ),
    )
    for document in (ROOT / "docs/media.md", ROOT / "fenric/_guidance/fenric/guide.md"):
        list(commands(document))
        args = documented(document, ["html", "--root", "$CHECK_DIR"], tmp_path)
        capsys.readouterr()  # argparse's successful --help presentation above
        assert main(args) == 4
        assert capsys.readouterr().out == "unavailable: headless\n"
        with monkeypatch.context() as scoped:
            scoped.setattr(
                html_bundle, "_TYPES", {k: v for k, v in html_bundle._TYPES.items() if k != ".wav"}
            )
            assert main(args) == 3
            assert capsys.readouterr().out == ""


def test_documented_gallery_manifest_and_result(tmp_path, monkeypatch, capsys):
    text = (ROOT / "docs/media.md").read_text()
    manifest = json.loads(re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)[0])
    for name in ("gallery-first.png", "gallery-second.png"):
        shutil.copyfile(ROOT / "docs/examples" / name, tmp_path / name)
    (tmp_path / "gallery.json").write_text(json.dumps(manifest))
    fake = submits(q0="Readable", q1="")
    monkeypatch.setattr(backend, "default_backend", lambda: fake)
    monkeypatch.chdir(tmp_path)
    assert main(documented(ROOT / "docs/media.md", ["ask", "--gallery-file"], tmp_path)) == 0
    assert json.loads(capsys.readouterr().out) == {"portrait": "Readable", "wide": ""}
    assert len(fake.pages) == 1
    assert json.loads((tmp_path / "gallery-status.json").read_text()) == {
        "status": "unavailable",
        "url": None,
        "reason": "not configured",
    }


def test_original_tiny_audio_is_reproducible_complete_and_selected(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("audio_demo_generator", DEMO / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "tone.wav"
    module.write(output)
    assert output.read_bytes() == (DEMO / "tone.wav").read_bytes()
    assert output.stat().st_size == 9644
    with wave.open(str(output)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 8000)
        assert audio.getnframes() == 4800
        samples = struct.unpack("<4800h", audio.readframes(4800))
        assert any(samples[:2400]) and any(samples[2400:])
        assert max(abs(value) for value in samples) <= 3000
    bundle = load_bundle(str(DEMO), "index.html", ["index.html", "tone.wav"])
    assert bundle.resources["tone.wav"].data == output.read_bytes()
    assert bundle.resources["tone.wav"].content_type == "audio/wav"
    html = bundle.resources["index.html"].data.decode()
    assert '<audio controls preload="metadata" src="tone.wav">' in html
    assert "autoplay" not in html
    policy = document_policy("https://example.test/files/", bundle)
    assert "media-src https://example.test/files/tone.wav" in policy
    fake = submits()
    monkeypatch.setattr(backend, "default_backend", lambda: fake)
    monkeypatch.chdir(ROOT)
    assert (
        main(
            documented(
                ROOT / "docs/media.md", ["html", "--root", "docs/examples/audio-demo"], tmp_path
            )
        )
        == 0
    )
    assert json.loads((tmp_path / "audio-status.json").read_text()) == {
        "status": "unavailable",
        "url": None,
        "reason": "not configured",
    }
    assert capsys.readouterr().out == "ok\n"
    assert len(fake.pages) == 1
