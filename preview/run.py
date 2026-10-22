"""The preview harness's commands. From the project directory:

    python preview/run.py install    # download WebKit for Playwright
    python preview/run.py check      # drive every dialog in WebKit
    python preview/run.py gallery    # generate the gallery in gallery/ (not committed)
    python preview/run.py show NAME  # open one sample in the real window
    python preview/run.py docs       # render every documentation image into docs/images/

None of this is part of ./bin/check all, which never needs a browser.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GALLERY = HERE.parent / "gallery"
DOCS = HERE.parent / "docs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preview", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="download WebKit for Playwright into its per-user cache")
    commands.add_parser("check", help="drive every dialog in WebKit; exit nonzero on a failure")
    commands.add_parser("gallery", help="generate the gallery locally and report what changed")
    show = commands.add_parser("show", help="open one sample in the real native window")
    show.add_argument("name", help="a gallery heading, such as confirm or pick-multiple")
    commands.add_parser("docs", help="render each documentation image from the command above it")
    arguments = parser.parse_args(argv)
    command = arguments.command

    if command == "install":
        return subprocess.run([sys.executable, "-m", "playwright", "install", "webkit"]).returncode
    if command == "check":
        import pytest

        return int(pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", str(HERE), str(HERE)]))
    if command == "show":
        return _show(arguments.name)
    if command == "docs":
        return _docs()
    return _gallery()


def _show(name: str) -> int:
    """Open a sample through the engine and the native backend, as a caller would."""
    sys.path.insert(0, str(HERE))
    from samples import SAMPLES

    from fenric.engine import run

    if name not in SAMPLES:
        print(f"preview: no sample by that name; choose from {', '.join(SAMPLES)}", file=sys.stderr)
        return 2
    result = run(SAMPLES[name])
    reason = f": {result.detail}" if result.detail else ""
    print(f"{result.status}{reason} (exit {result.exit_code})")
    return result.exit_code


def _docs() -> int:
    """Render each documentation image from the command above it, and say what changed.

    An image line, ``![...](images/NAME.png)``, that follows a ``bash`` fence
    holding a ``fenric`` command shows the dialog that command opens. The
    command is parsed by Fenric's own command line, from a copy of
    ``docs/examples``, so the image is of exactly the spec the command sends.
    """
    sys.path.insert(0, str(HERE))
    import os
    import shlex
    import shutil
    import tempfile
    from unittest.mock import patch

    from harness import HarnessError, launch
    from playwright.sync_api import sync_playwright

    from fenric.cli import _assemble, build_parser
    from gallery import changes, shoot, write

    commands: dict[str, list[str]] = {}
    setups: dict[str, dict[str, str]] = {}
    image_name = None
    for page in (HERE.parent / "README.md", *sorted(DOCS.glob("*.md"))):
        lines = page.read_text(encoding="utf-8").split("\n")
        command = None
        for index, line in enumerate(lines):
            if line == "```bash":
                command = lines[index + 1]
                image_name = None
            elif line.startswith("<!-- capture"):
                if image_name is not None and image_name not in setups:
                    import json

                    setups[image_name] = json.loads(line[len("<!-- capture ") : -len(" -->")]).get(
                        "setup", {}
                    )
                command = None
            elif image := re.fullmatch(r"!\[[^\]]+\]\((?:docs/)?images/([\w-]+)\.png\)", line):
                if command is None:
                    print(f"preview: {page.name}: an image with no command", file=sys.stderr)
                    return 2
                words = shlex.split(command)
                while words and re.fullmatch(r"[A-Z_]+=.*", words[0]):
                    words.pop(0)
                image_name = f"{image.group(1)}.png"
                commands[image_name] = words[1:]
    origin = os.getcwd()
    files = {}
    try:
        with tempfile.TemporaryDirectory(prefix="fenric-docs-") as scratch:
            with sync_playwright() as playwright:
                browser = launch(playwright)
                try:
                    for name, argv in commands.items():
                        workspace = Path(scratch) / Path(name).stem
                        workspace.mkdir()
                        shutil.copytree(DOCS / "examples", workspace / "examples")
                        for filename, content in setups.get(name, {}).items():
                            target = workspace / filename
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(content)
                        os.chdir(workspace)
                        spec = _assemble(vars(build_parser().parse_args(argv)))
                        with patch("harness.prepare_destination", _documentation_destination):
                            files[name] = shoot(
                                browser, spec, "dark" if name.endswith("-dark.png") else "light"
                            )[0]
                finally:
                    browser.close()
    except HarnessError as exc:
        print(f"preview: {exc}", file=sys.stderr)
        return 2
    finally:
        os.chdir(origin)
    images = DOCS / "images"
    report = changes(images, files)
    write(images, files)
    if not report:
        print(f"DOCS IMAGES UNCHANGED ({len(files)} images)")
        return 0
    print("DOCS IMAGES CHANGED")
    for line in report:
        print(f"  {line}")
    return 0


def _documentation_destination(spec):
    """Keep real target validation; show synthetic host/path metadata in published images."""
    from dataclasses import replace

    from fenric.engine import prepare_destination

    prepared = prepare_destination(spec)
    if prepared.metadata is None:
        return prepared
    metadata = {**prepared.metadata, "host": "Example Mac"}
    if spec.into:
        metadata["display"] = "/example/" + Path(spec.into).name
        metadata["details"] = [
            "Example Mac",
            metadata["display"],
            *prepared.metadata["details"][2:],
        ]
    else:
        metadata["details"] = ["Example Mac", spec.store]
    return replace(prepared, metadata=metadata)


def _gallery() -> int:
    sys.path.insert(0, str(HERE))
    from harness import HarnessError, launch
    from playwright.sync_api import sync_playwright

    from gallery import changes, render, write

    try:
        with sync_playwright() as playwright:
            browser = launch(playwright)
            try:
                files = render(browser)
            finally:
                browser.close()
    except HarnessError as exc:
        print(f"preview: {exc}", file=sys.stderr)
        return 2
    report = changes(GALLERY, files)
    write(GALLERY, files)
    if not report:
        print(f"GALLERY UNCHANGED ({len(files)} files)")
        return 0
    print("GALLERY CHANGED")
    for line in report:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
