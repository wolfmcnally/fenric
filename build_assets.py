"""Build the browser bundles that ship inside the Fenric package.

Run from anywhere with the project's Python:

    python build_assets.py

It installs the locked build-time dependencies with Bun, type-checks the
TypeScript sources with ``tsc`` in strict mode, bundles each entry point,
copies the manifest set into the package for ``fenric setup-charts``, writes
the license notices of every npm package the bundler included, and records
the SHA-256 of every input and output in
``fenric/_assets/build-manifest.json``. The test suite compares that record
with the files on disk, so a source edit without a rebuild fails the gate.
The gate itself never needs Bun.

    python build_assets.py --check

builds into a temporary directory instead and fails unless every output is
byte-identical to the committed file. The digest record shows that the
committed bundles belong to a recorded build of the current sources; the
check shows that they are what those sources compile to.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "assets" / "ts"
ASSETS = ROOT / "fenric" / "_assets"
TOOLCHAIN = ROOT / "fenric" / "_toolchain"
MANIFEST_SET = ("package.json", "bun.lock", "tsconfig.json")
BUNDLES = {"chrome.ts": "chrome.js", "remote.ts": "remote.js", "d3-runtime.ts": "d3.js"}
NOTICES = "THIRD_PARTY_NOTICES.md"
_PACKAGE = re.compile(r"(?:^|/)node_modules/((?:@[^/]+/)?[^/]+)/")
_NOTICES_HEAD = (
    "# Third-party notices\n\n"
    "The chart runtime in this directory, `d3.js`, bundles the npm packages below. "
    "Each is listed with its version and the license its `package.json` declares, "
    "followed by its license file as published. Fenric itself is under the MIT "
    "license in `LICENSE`.\n"
)


def _run(*arguments: str) -> str:
    completed = subprocess.run(
        arguments, cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=None, text=True
    )
    return completed.stdout.strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(paths: list[Path]) -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): _digest(path) for path in sorted(paths)}


def _build(bun: str, assets: Path, toolchain: Path) -> dict[str, str]:
    """Bundle every entry point into ``assets``, write the notices of what was
    bundled, copy the manifest set, and return each bundled package's version."""
    assets.mkdir(parents=True, exist_ok=True)
    toolchain.mkdir(parents=True, exist_ok=True)
    bundled: set[str] = set()
    with tempfile.TemporaryDirectory() as scratch:
        for source, bundle in BUNDLES.items():
            metafile = Path(scratch) / f"{bundle}.meta.json"
            _run(
                bun,
                "build",
                f"assets/ts/{source}",
                "--target",
                "browser",
                "--format",
                "iife",
                "--minify",
                "--outfile",
                str(assets / bundle),
                f"--metafile={metafile}",
            )
            for name in json.loads(metafile.read_text(encoding="utf-8"))["inputs"]:
                match = _PACKAGE.search(name)
                if match:
                    bundled.add(match.group(1))
    versions = _write_notices(sorted(bundled), assets / NOTICES)
    for name in MANIFEST_SET:
        shutil.copyfile(ROOT / name, toolchain / name)
    return versions


def _write_notices(packages: list[str], target: Path) -> dict[str, str]:
    """Write each bundled package's license file into one notices document."""
    sections = [_NOTICES_HEAD]
    versions: dict[str, str] = {}
    for package in packages:
        directory = ROOT / "node_modules" / package
        metadata = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        licenses = sorted(
            path
            for path in directory.iterdir()
            if path.name.upper().startswith(("LICENSE", "LICENCE"))
        )
        if not licenses:
            raise SystemExit(f"build_assets: {package} has no license file to ship")
        text = licenses[0].read_text(encoding="utf-8").strip()
        versions[package] = metadata["version"]
        sections.append(
            f"## {package} {metadata['version']}\n\nLicense: {metadata['license']}\n\n"
            f"```text\n{text}\n```\n"
        )
    target.write_text("\n".join(sections), encoding="utf-8")
    return versions


def _check(bun: str) -> int:
    """Rebuild elsewhere and compare with the committed outputs."""
    with tempfile.TemporaryDirectory() as scratch:
        fresh_assets, fresh_toolchain = Path(scratch) / "assets", Path(scratch) / "toolchain"
        _build(bun, fresh_assets, fresh_toolchain)
        outputs = [*BUNDLES.values(), NOTICES]
        pairs = [(fresh_assets / name, ASSETS / name) for name in outputs]
        pairs += [(fresh_toolchain / name, TOOLCHAIN / name) for name in MANIFEST_SET]
        stale = [
            committed.relative_to(ROOT).as_posix()
            for fresh, committed in pairs
            if not committed.is_file() or fresh.read_bytes() != committed.read_bytes()
        ]
    if stale:
        print("build_assets: STALE", ", ".join(stale), file=sys.stderr)
        return 1
    print(f"build_assets: CHECK PASS ({len(pairs)} outputs match a fresh build)")
    return 0


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments not in ([], ["--check"]):
        print("usage: build_assets.py [--check]", file=sys.stderr)
        return 2
    bun = shutil.which("bun")
    if bun is None:
        print("build_assets: Bun is required on PATH", file=sys.stderr)
        return 1
    _run(bun, "install", "--frozen-lockfile")
    _run(bun, "x", "--no-install", "tsc", "-p", "tsconfig.json")
    if arguments:
        return _check(bun)
    bundled = _build(bun, ASSETS, TOOLCHAIN)

    inputs = [*SOURCES.rglob("*.ts"), *(ROOT / name for name in MANIFEST_SET), Path(__file__)]
    outputs = [
        *(ASSETS / bundle for bundle in BUNDLES.values()),
        ASSETS / NOTICES,
        *(TOOLCHAIN / name for name in MANIFEST_SET),
    ]
    manifest = {
        "bundled": bundled,
        "inputs": _record(inputs),
        "outputs": _record(outputs),
        "tools": {
            "bun": _run(bun, "--version"),
            "typescript": _run(bun, "x", "--no-install", "tsc", "--version"),
        },
    }
    (ASSETS / "build-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("build_assets: wrote", ", ".join(sorted(manifest["outputs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
