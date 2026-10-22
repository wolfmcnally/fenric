"""The committed bundles belong to a recorded build of the current sources.

These checks need no Bun. They bind the committed outputs to the build
record and the build record to the files on disk; the build script's check
mode is what rebuilds and compares.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((PROJECT / "fenric/_assets/build-manifest.json").read_text())
MANIFEST_SET = ("package.json", "bun.lock", "tsconfig.json")


def digest(relative: str) -> str:
    return hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest()


def test_committed_bundles_match_their_recorded_build() -> None:
    sources = sorted(
        path.relative_to(PROJECT).as_posix() for path in PROJECT.glob("assets/ts/**/*")
    )
    assert sources == ["assets/ts/chrome.ts", "assets/ts/d3-runtime.ts", "assets/ts/remote.ts"]
    assert sorted(MANIFEST["inputs"]) == sorted([*sources, *MANIFEST_SET, "build_assets.py"])
    assert sorted(MANIFEST["outputs"]) == [
        "fenric/_assets/THIRD_PARTY_NOTICES.md",
        "fenric/_assets/chrome.js",
        "fenric/_assets/d3.js",
        "fenric/_assets/remote.js",
        *(f"fenric/_toolchain/{name}" for name in sorted(MANIFEST_SET)),
    ]
    for kind in ("inputs", "outputs"):
        stale = [path for path, recorded in MANIFEST[kind].items() if digest(path) != recorded]
        assert stale == [], f"{kind} changed since the last build: run build_assets.py"
    assert set(MANIFEST["tools"]) == {"bun", "typescript"}


def test_the_shipped_toolchain_manifest_is_the_authored_one() -> None:
    for name in MANIFEST_SET:
        shipped = (PROJECT / "fenric/_toolchain" / name).read_bytes()
        assert shipped == (PROJECT / name).read_bytes(), name
    shipped_files = sorted(path.name for path in (PROJECT / "fenric/_toolchain").iterdir())
    assert shipped_files == sorted(MANIFEST_SET)


def test_every_bundled_package_has_its_license_in_the_notices() -> None:
    notices = (PROJECT / "fenric/_assets/THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", notices, flags=re.MULTILINE)[1:]
    listed = {}
    for section in sections:
        heading, _, body = section.partition("\n")
        name, _, version = heading.rpartition(" ")
        license_line = re.search(r"^License: (\S+)$", body, re.MULTILINE)
        text = re.search(r"^```text\n(.+?)\n```$", body, re.MULTILINE | re.DOTALL)
        assert license_line and text and len(text.group(1)) > 100, name
        listed[name] = version
    assert MANIFEST["bundled"], "the build record names no bundled package"
    assert listed == MANIFEST["bundled"]
    assert "d3" in listed
