"""One WebKit browser for the whole run; a missing browser stops the run with the fix."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from harness import HarnessError, launch
from playwright.sync_api import Browser, sync_playwright


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            launched = launch(playwright)
        except HarnessError as exc:
            pytest.exit(str(exc), returncode=2)
        yield launched
        launched.close()
