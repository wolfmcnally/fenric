"""Bounded static PNG data for caller-provided question illustrations."""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from typing import Any

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 10
MAX_COMMENT_LENGTH = 1000
PNG_PREFIX = "data:image/png;base64,"


def validate_image(raw: object) -> dict[str, Any]:
    """Return normalized inline image metadata; failures never echo input."""
    if not isinstance(raw, dict) or set(raw) != {"data", "alt"}:
        raise ValueError("must contain data and alt")
    data, alt = raw["data"], raw["alt"]
    if not isinstance(alt, str) or not alt.strip():
        raise ValueError("must have non-empty alternative text")
    if not isinstance(data, str) or not data.startswith(PNG_PREFIX):
        raise ValueError("must be an inline PNG data URL")
    encoded = data[len(PNG_PREFIX) :]
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError("exceeds the image size limit")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("must contain valid base64 PNG data") from None
    if len(png) > MAX_IMAGE_BYTES:
        raise ValueError("exceeds the image size limit")
    width, height = _png_dimensions(png)
    return {"data": data, "alt": alt, "width": width, "height": height, "bytes": len(png)}


def _png_dimensions(png: bytes) -> tuple[int, int]:
    """Check framing/checksums and bounds; the browser owns pixel decoding."""
    invalid = "must contain a bounded static PNG"
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(invalid)
    offset = 8
    width = height = 0
    saw_data = False
    while offset + 12 <= len(png):
        length = int.from_bytes(png[offset : offset + 4], "big")
        kind = png[offset + 4 : offset + 8]
        end = offset + 8 + length
        if end + 4 > len(png):
            raise ValueError(invalid)
        chunk = png[offset + 8 : end]
        checksum = int.from_bytes(png[end : end + 4], "big")
        if zlib.crc32(kind + chunk) != checksum or kind in (b"acTL", b"fcTL", b"fdAT"):
            raise ValueError(invalid)
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError(invalid)
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (
                not 0 < width <= 8192
                or not 0 < height <= 8192
                or width * height > 16_000_000
                or depth not in depths.get(color, ())
                or compression != 0
                or filtering != 0
                or interlace not in (0, 1)
            ):
                raise ValueError(invalid)
        elif kind == b"IHDR":
            raise ValueError(invalid)
        if kind == b"IDAT":
            saw_data = True
        if kind == b"IEND":
            if length or not saw_data or end + 4 != len(png):
                raise ValueError(invalid)
            return width, height
        offset = end + 4
    raise ValueError(invalid)
