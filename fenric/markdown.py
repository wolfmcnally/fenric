"""Bounded immutable snapshots and inert rendering of selected Markdown files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from fenric.sanitize import sanitize_html

MAX_DOCUMENTS = 16
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


class MarkdownError(ValueError):
    """A value-free selected-document refusal."""


def render_markdown(source: str) -> str:
    # Help and unrelated verbs must remain usable without loading this optional path.
    try:
        import mistune
    except ImportError:
        raise MarkdownError("Markdown parser is unavailable") from None

    class Renderer(mistune.HTMLRenderer):
        def link(self, text: str, url: str, title: str | None = None) -> str:
            return text  # Readable text, never navigation or an implicit file request.

        def image(self, text: str, url: str, title: str | None = None) -> str:
            return f"[Image: {text}]"

    parser = mistune.create_markdown(
        renderer=Renderer(escape=True), plugins=["table", "strikethrough"]
    )
    # The inert allowlist supports s, while the parser's plugin emits del.
    rendered = (
        parser(source.removeprefix("\ufeff")).replace("<del>", "<s>").replace("</del>", "</s>")
    )
    return sanitize_html(rendered)


def _read(filename: str) -> bytes:
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK")
    ):
        raise MarkdownError("Markdown file selection is unavailable on this platform")
    path = Path(filename)
    if path.suffix.lower() not in (".md", ".markdown") or ".." in path.parts:
        raise MarkdownError("Markdown files must be explicitly selected Markdown paths")
    path = path.absolute()
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_DOCUMENT_BYTES:
                raise MarkdownError("Markdown file is not a bounded regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_DOCUMENT_BYTES + 1)
            after = os.fstat(fd)
            if len(data) > MAX_DOCUMENT_BYTES or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise MarkdownError("Markdown file changed or exceeded its limit")
            return data
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def load_documents(files: list[str]) -> list[dict[str, str]]:
    """Read only the named files, once. Preserve UTF-8 BOM/newlines for Copy."""
    documents = []
    total = 0
    try:
        for filename in files:
            data = _read(filename)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise MarkdownError("Markdown selection exceeds its total size limit")
            source = data.decode("utf-8")
            if "\0" in source:
                raise MarkdownError("Markdown must be UTF-8 text without NUL")
            documents.append(
                {"name": Path(filename).name, "source": source, "rendered": render_markdown(source)}
            )
    except (OSError, UnicodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, MarkdownError):
            raise
        raise MarkdownError("Markdown selection could not be read or rendered") from None
    return documents
