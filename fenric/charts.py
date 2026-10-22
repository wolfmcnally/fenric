"""The chart toolchain: where it lives, how it is provisioned, how it compiles.

A chart is TypeScript the caller wrote. Before any window opens it is
type-checked in strict mode against the d3 types and then transpiled. Both
steps need Bun, TypeScript and the d3 type definitions, which
``fenric setup-charts`` installs once into a per-user cache from the manifest
and frozen lockfile shipped inside the package. Fenric never downloads a
runtime, and it never echoes what the tools print.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path

SETUP_COMMAND = "fenric setup-charts"
CACHE_ENV = "FENRIC_CACHE_DIR"
TOOL_TIMEOUT_SECONDS = 120
_MANIFEST_SET = ("package.json", "bun.lock", "tsconfig.json")
_READY = "ready"
_CACHE_FAILURE = "the chart toolchain cache could not be used"
_CHART_ENV = "declare const data: any;\ndeclare const root: HTMLDivElement;\n"


class ChartError(RuntimeError):
    """A chart could not be compiled, or its toolchain is unavailable."""


def chart_cache_dir() -> Path:
    """Return the per-user directory that holds the provisioned toolchain."""
    override = os.environ.get(CACHE_ENV)
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "fenric"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "fenric"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "fenric"
    return base / f"charts-{_lock_digest()[:16]}"


def setup_charts() -> None:
    """Provision the chart toolchain, or raise :class:`ChartError`."""
    try:
        _provision()
    except (OSError, UnicodeError) as exc:
        raise ChartError(_CACHE_FAILURE) from exc


def compile_chart(code: str) -> str:
    """Type-check and transpile chart ``code``; return browser JavaScript."""
    try:
        return _compile(code)
    except (OSError, UnicodeError) as exc:
        raise ChartError(_CACHE_FAILURE) from exc


def _provision() -> None:
    bun = _bun()
    cache = chart_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / _READY).unlink(missing_ok=True)
    for name in _MANIFEST_SET:
        (cache / name).write_bytes(_shipped(name))
    _tool([bun, "install", "--frozen-lockfile"], cache, "the chart toolchain install failed")
    (cache / _READY).write_text(_lock_digest(), encoding="utf-8")


def _compile(code: str) -> str:
    cache = chart_cache_dir()
    try:
        ready = (cache / _READY).read_text(encoding="utf-8") == _lock_digest()
    except (OSError, UnicodeError):
        ready = False
    if not ready:
        raise ChartError(f"the chart toolchain is not provisioned; run `{SETUP_COMMAND}`")
    bun = _bun()
    with tempfile.TemporaryDirectory(dir=cache, prefix="job-") as job_dir:
        job = Path(job_dir)
        (job / "chart.ts").write_text(code, encoding="utf-8")
        (job / "env.d.ts").write_text(_CHART_ENV, encoding="utf-8")
        (job / "tsconfig.json").write_text(
            json.dumps(
                {
                    "extends": "../tsconfig.json",
                    "compilerOptions": {
                        "types": ["d3"],
                        "typeRoots": ["../node_modules/@types"],
                    },
                    "include": [],
                    "files": ["env.d.ts", "chart.ts"],
                }
            ),
            encoding="utf-8",
        )
        _tool(
            [bun, "x", "--no-install", "tsc", "-p", str(job / "tsconfig.json")],
            cache,
            "the chart code did not pass the type check",
        )
        _tool(
            [
                bun,
                "build",
                str(job / "chart.ts"),
                "--target",
                "browser",
                "--format",
                "iife",
                "--outfile",
                str(job / "chart.js"),
            ],
            cache,
            "the chart code could not be compiled",
        )
        return (job / "chart.js").read_text(encoding="utf-8")


def _shipped(name: str) -> bytes:
    return resources.files("fenric").joinpath("_toolchain", name).read_bytes()


def _lock_digest() -> str:
    return hashlib.sha256(_shipped("bun.lock")).hexdigest()


def _bun() -> str:
    bun = shutil.which("bun")
    if bun is None:
        raise ChartError(
            f"Bun is required on PATH for charts; install it, then run `{SETUP_COMMAND}`"
        )
    return bun


def _tool(arguments: list[str], cwd: Path, failure: str) -> None:
    """Run one toolchain step with its output discarded."""
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChartError(failure) from exc
    if completed.returncode != 0:
        raise ChartError(failure)
