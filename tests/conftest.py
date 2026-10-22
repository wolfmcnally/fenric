"""Shared fixtures.

No test inherits the caller's headless switch or chart cache, and none can
reach the native window: a test, or a mutation of the code under it, that
falls through to the default backend fails instead of opening a dialog.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FENRIC_HEADLESS", raising=False)
    monkeypatch.setenv("FENRIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FENRIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("FENRIC_DESKTOP_ONLY", raising=False)
    monkeypatch.setattr("fenric.backend.default_backend", _no_window)


def _no_window() -> None:
    raise AssertionError("a test reached the native window backend")
