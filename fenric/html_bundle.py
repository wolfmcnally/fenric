"""Bounded byte snapshots of explicitly selected HTML presentation files."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

MAX_FILES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class HtmlError(ValueError):
    """A fixed value-free bundle preparation failure."""


@dataclass(frozen=True)
class Resource:
    data: bytes
    content_type: str


@dataclass(frozen=True)
class HtmlBundle:
    entry: str
    resources: Mapping[str, Resource]


def valid_member_name(name: object) -> bool:
    """Names are exact POSIX relative paths, never filesystem/URL expressions."""
    return (
        isinstance(name, str)
        and bool(name)
        and len(name.encode("utf-8", errors="replace")) <= 1024
        and not any(c in name for c in ("\\", "\0", "?", "#"))
        and all(part not in ("", ".", "..") for part in name.split("/"))
        and Path(name).suffix.lower() in _TYPES
    )


def is_html_entry(name: str) -> bool:
    """The entry is an HTML document, not a supporting asset."""
    return Path(name).suffix.lower() in (".html", ".htm")


def _read(root: int, name: str) -> bytes:
    directory = os.dup(root)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise HtmlError("HTML bundle contains an unsupported file")
            chunks: list[bytes] = []
            size = 0
            while chunk := os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HtmlError("HTML bundle exceeds its size limit")
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or size != after.st_size:
                raise HtmlError("HTML bundle changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def load_bundle(root: str, entry: str, files: list[str]) -> HtmlBundle:
    """Read only the explicit selection; relative roots use the process cwd."""
    if (
        not 0 < len(files) <= MAX_FILES
        or len(set(files)) != len(files)
        or entry not in files
        or not is_html_entry(entry)
        or not all(valid_member_name(name) for name in files)
    ):
        raise HtmlError("HTML bundle selection is invalid")
    resources: dict[str, Resource] = {}
    try:
        selected_root = Path(root).resolve(strict=True)
        descriptor = os.open(selected_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            total = 0
            for name in files:
                data = _read(descriptor, name)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise HtmlError("HTML bundle exceeds its size limit")
                kind = _TYPES[Path(name).suffix.lower()]
                if "charset=utf-8" in kind:
                    data.decode("utf-8")
                resources[name] = Resource(data, kind)
        finally:
            os.close(descriptor)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, HtmlError):
            raise
        raise HtmlError("HTML bundle could not be read") from None
    return HtmlBundle(entry, MappingProxyType(resources))


def document_policy(prefix: str) -> str:
    """Every navigable selected document has the same explicit source boundary."""
    return (
        f"default-src 'none'; script-src {prefix} 'unsafe-inline'; "
        f"style-src {prefix} 'unsafe-inline'; img-src {prefix} data: blob:; "
        f"font-src {prefix} data: blob:; connect-src {prefix}; "
        "base-uri 'none'; form-action 'none'; object-src 'none'; "
        "frame-src 'none'; worker-src 'none'; sandbox allow-scripts"
    )
