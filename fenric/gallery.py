"""Load a local screenshot manifest as an ordinary ask wizard request."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Any

from fenric.images import MAX_IMAGE_BYTES, MAX_IMAGES, PNG_PREFIX
from fenric.spec import SpecError, validate_spec


def _read_file(path: Path, limit: int, field: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise SpecError(field, "must be a bounded regular file")
            data = handle.read(limit + 1)
    except SpecError:
        raise
    except (OSError, ValueError):
        raise SpecError(field, "could not read a bounded regular file") from None
    if len(data) > limit:
        raise SpecError(field, "exceeds the file size limit")
    return data


def load_gallery(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a PNG manifest; return a validated raw ask spec without opening a dialog.

    Image files resolve relative to the manifest. Errors are value-free
    ``SpecError`` instances. No path, image or answer is written anywhere.
    """
    manifest = Path(path)
    try:
        raw = json.loads(_read_file(manifest, 65536, "gallery"))
    except SpecError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise SpecError("gallery", "must contain valid JSON") from None
    if not isinstance(raw, dict) or not set(raw) <= {
        "images",
        "title",
        "message",
        "app",
        "timeout",
    }:
        raise SpecError("gallery", "has an invalid manifest shape")
    images = raw.get("images")
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
        raise SpecError("gallery.images", "must contain between 1 and 10 images")
    questions = []
    for index, entry in enumerate(images):
        here = f"gallery.images[{index}]"
        if not isinstance(entry, dict) or not set(entry) <= {
            "id",
            "name",
            "file",
            "caption",
            "required",
        }:
            raise SpecError(here, "has an invalid image shape")
        for key in ("id", "name", "file"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise SpecError(f"{here}.{key}", "must be non-empty text")
        png = _read_file(manifest.parent / entry["file"], MAX_IMAGE_BYTES, f"{here}.file")
        questions.append(
            {
                "id": entry["id"],
                "prompt": entry["name"],
                "detail": entry.get("caption"),
                "multiline": True,
                "required": entry.get("required", False),
                "image": {
                    "data": PNG_PREFIX + base64.b64encode(png).decode("ascii"),
                    "alt": entry["name"],
                },
            }
        )
    spec = {key: value for key, value in raw.items() if key != "images"}
    spec.update(verb="ask", presentation="wizard", questions=questions)
    validate_spec(spec)
    return spec
