"""Selected document snapshots, unsafe content and acknowledgment boundaries."""

from __future__ import annotations

import base64
import html
import os
import re
from pathlib import Path

import pytest

from fenric.chrome import build_page
from fenric.cli import main
from fenric.markdown import MAX_DOCUMENT_BYTES, MarkdownError, load_documents, render_markdown
from fenric.result import ResultError, validate_result
from fenric.spec import SpecError, validate_spec
from tests.support import returns


def test_exact_snapshot_and_inert_rendering(tmp_path: Path) -> None:
    source = (
        "\ufeff# Notes\r\n\r\n**bold** [link](file:///private/secret)\r\n"
        '<img src="https://bad.invalid/x" onerror="alert(1)">\r\n'
        "![alt](neighbor.png)\r\n\r\n```html\n<script>literal</script>\n```\n"
    )
    p = tmp_path / "notes.md"
    p.write_bytes(source.encode())
    spec = validate_spec({"verb": "markdown", "files": [str(p)]})
    p.write_text("later edit")
    page = build_page(spec)
    encoded = re.search(r'data-copy-base64="([^"]*)"', page.html)
    assert encoded and base64.b64decode(encoded[1]).decode() == source
    inner = html.unescape(re.search(r'srcdoc="([^"]*)"', page.html)[1])
    assert "<strong>bold</strong>" in inner and "&lt;script&gt;literal&lt;/script&gt;" in inner
    assert not re.search(r"<(?:script|img|a|form|input|iframe)\b", inner)
    assert "file:///private/secret" not in inner
    assert "script-src 'none'" in html.unescape(inner) and 'sandbox=""' in page.html
    assert page.resizable and page.width == 760
    assert validate_result(spec, {"action": "ok", "values": {}}) is None
    with pytest.raises(ResultError):
        validate_result(spec, {"action": "yes", "values": {}})


def test_reader_refuses_links_special_files_and_invalid_bytes(tmp_path: Path) -> None:
    good = tmp_path / "good.md"
    good.write_text("safe")
    link = tmp_path / "link.md"
    link.symlink_to(good)
    linked_parent = tmp_path / "alias"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    bad = tmp_path / "invalid.md"
    bad.write_bytes(b"\xff")
    large = tmp_path / "large.md"
    large.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))
    fifo = tmp_path / "fifo.md"
    os.mkfifo(fifo)
    for path in [
        link,
        linked_parent / "good.md",
        bad,
        large,
        fifo,
        tmp_path,
        tmp_path / "absent.md",
    ]:
        with pytest.raises(MarkdownError) as exc:
            load_documents([str(path)])
        assert str(tmp_path) not in str(exc.value)
    for files in [[], ["x.md"] * 17, [str(good)] * 2, [False]]:
        with pytest.raises(SpecError):
            validate_spec({"verb": "markdown", "files": files})


def test_empty_document_table_and_total_limit(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_bytes(b"")
    table = tmp_path / "table.markdown"
    table.write_text("| One | Two |\n| --- | --- |\n| a | b |\n")
    docs = load_documents([str(empty), str(table)])
    assert docs[0]["source"] == "" and "<table>" in docs[1]["rendered"]
    files = []
    for i in range(5):
        p = tmp_path / f"{i}.md"
        p.write_bytes(b" " * MAX_DOCUMENT_BYTES)
        files.append(str(p))
    with pytest.raises(MarkdownError, match="total size"):
        load_documents(files)


def test_cli_and_json_use_same_snapshot(monkeypatch, capsys, tmp_path: Path) -> None:
    p = tmp_path / "first.md"
    p.write_bytes(b"# First\r\n")
    backend = returns({"action": "ok", "values": {}})
    monkeypatch.setattr("fenric.backend.default_backend", lambda: backend)
    assert main(["markdown", "--file", str(p), "--view", "source", "--height", "500"]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert "data-md-source" in backend.pages[0].html
    assert 'data-md-rendered height="500"' in backend.pages[0].html
    assert main(["--spec", '{"verb":"markdown","files":[]}']) == 3
    assert "files" in capsys.readouterr().err


def test_formatting_and_missing_parser_are_value_free(monkeypatch) -> None:
    import sys

    assert "<s>removed</s>" in render_markdown("~~removed~~")
    monkeypatch.setitem(sys.modules, "mistune", None)
    with pytest.raises(MarkdownError, match="^Markdown parser is unavailable$"):
        render_markdown("private source must not appear in the error")


def test_image_labels_and_unsupported_reader_platform(monkeypatch) -> None:
    rendered = render_markdown("![a & b](x) ![**bold**](y)")
    assert "[Image: a &amp; b]" in rendered
    assert "[Image: <strong>bold</strong>]" in rendered
    assert "<img" not in rendered
    with monkeypatch.context() as context:
        context.delattr(os, "O_NOFOLLOW")
        with pytest.raises(MarkdownError, match="unavailable on this platform"):
            load_documents(["private-name.md"])


@pytest.mark.parametrize("pattern", ["[", "*", "[a](", "[*"])
def test_maximum_size_parser_adversarial_regression(pattern) -> None:
    import subprocess
    import sys

    source = (pattern * (MAX_DOCUMENT_BYTES // len(pattern) + 1))[:MAX_DOCUMENT_BYTES]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from fenric.markdown import render_markdown; "
            'render_markdown(sys.stdin.read()); print("rendered")',
        ],
        input=source,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0 and result.stdout == "rendered\n"
