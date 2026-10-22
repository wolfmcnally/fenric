"""Render every sample dialog in light and dark, and say what changed.

The gallery is regenerated whole: images, an index page and a manifest of
versions and digests. Before anything is written, the new bytes are compared
with what is on disk, and the report names every file that differs.
"""

from __future__ import annotations

import hashlib
import html
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from harness import SCALE, open_dialog
from playwright.sync_api import Browser
from samples import SAMPLES

SCHEMES = ("light", "dark")
# How far into its countdown a dialog with a timeout is shown.
RING_SHOWN_AT_MS = 3_000


def shoot(browser: Browser, raw: dict[str, Any], scheme: str) -> tuple[bytes, list[int]]:
    """Screenshot one dialog, fitted to its content; return the image and its size."""
    dialog = open_dialog(browser, raw, scheme=scheme)
    try:
        if dialog.spec.timeout is not None:
            dialog.page.clock.run_for(RING_SHOWN_AT_MS)
        dialog.fit()
        viewport = dialog.page.viewport_size
        image = dialog.page.screenshot(animations="disabled", caret="hide")
        return image, [viewport["width"], viewport["height"]]
    finally:
        dialog.close()


def render(browser: Browser) -> dict[str, bytes]:
    """Return every gallery file's name and bytes."""
    files: dict[str, bytes] = {}
    sizes: dict[str, list[int]] = {}
    for name, raw in SAMPLES.items():
        for scheme in SCHEMES:
            files[f"{name}-{scheme}.png"], sizes[f"{name}-{scheme}.png"] = shoot(
                browser, raw, scheme
            )
    manifest = {
        "browser": f"webkit {browser.version}",
        "playwright": version("playwright"),
        "scale": SCALE,
        "sizes": dict(sorted(sizes.items())),
        "images": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    files["index.html"] = _index(manifest["browser"]).encode()
    return files


def changes(directory: Path, files: dict[str, bytes]) -> list[str]:
    """Each file that would be added, changed or removed, as a report line."""
    report = []
    present = (
        {path.name for path in directory.iterdir() if path.is_file()}
        if directory.is_dir()
        else set()
    )
    for name in sorted(present | set(files)):
        if name not in files:
            report.append(f"removed {name}")
        elif name not in present:
            report.append(f"added {name}")
        elif (directory / name).read_bytes() != files[name]:
            report.append(f"changed {name}")
    return report


def write(directory: Path, files: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_file() and path.name not in files:
            path.unlink()
    for name, data in files.items():
        (directory / name).write_bytes(data)


def _index(browser: str) -> str:
    rows = []
    for name, raw in SAMPLES.items():
        spec = html.escape(json.dumps(raw, indent=2, ensure_ascii=False))
        images = "".join(
            f'<figure><img src="{name}-{scheme}.png" alt="{name}, {scheme}" '
            f'loading="lazy"><figcaption>{scheme}</figcaption></figure>'
            for scheme in SCHEMES
        )
        rows.append(
            f'<section id="{name}"><h2>{html.escape(name)}</h2>'
            f'<div class="pair">{images}</div>'
            f"<details><summary>Spec</summary><pre>{spec}</pre></details></section>"
        )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Fenric gallery</title><style>"
        "body{font:15px/1.5 -apple-system,system-ui,sans-serif;margin:24px;"
        "background:#f4f4f5;color:#18181b}"
        "h1{font-size:22px}h2{font-size:17px;margin:28px 0 8px}"
        ".pair{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}"
        "figure{margin:0}img{display:block;width:auto;max-width:100%;border-radius:10px;"
        "box-shadow:0 1px 4px rgba(0,0,0,.25);zoom:0.5}"
        "figcaption{font-size:13px;color:#52525b;margin-top:4px}"
        "pre{background:#fff;padding:12px;border-radius:8px;overflow:auto;font-size:12px}"
        "@media (prefers-color-scheme: dark){body{background:#18181b;color:#f4f4f5}"
        "figcaption{color:#a1a1aa}pre{background:#27272a}}"
        "</style></head><body>"
        "<h1>Fenric gallery</h1><p>Every dialog in light and dark, rendered by "
        f"{html.escape(browser)}"
        " from the chrome the package ships. Regenerate with "
        "<code>python preview/run.py gallery</code> from the project directory.</p>"
        + "".join(rows)
        + "</body></html>\n"
    )
