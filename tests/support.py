"""Shared test support: the scripted render backend and a controlled Bun stub.

``ScriptedBackend`` is the second implementation of the render seam. It
records every page it is asked to show and replays one scripted outcome, so
a test drives the engine exactly as a window would without opening one.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

from fenric.backend import CANCELLED, EXPIRED, SUBMITTED, UNAVAILABLE, Outcome
from fenric.chrome import Page
from fenric.session import Session


class ScriptedBackend:
    """A render backend that plays the person's part from a script."""

    def __init__(self, outcome: Outcome | Exception) -> None:
        self.outcome = outcome
        self.pages: list[Page] = []

    def show(self, page: Page, session: Session) -> Outcome:
        self.pages.append(page)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def submits(action: str = "ok", **values: Any) -> ScriptedBackend:
    """A person who answers. Dotted keys are passed with ``__`` for the dot."""
    payload = {"action": action, "values": {key.replace("__", "."): v for key, v in values.items()}}
    return ScriptedBackend(Outcome(SUBMITTED, payload=payload))


def returns(payload: Any) -> ScriptedBackend:
    """A window that hands back ``payload`` verbatim, well formed or not."""
    return ScriptedBackend(Outcome(SUBMITTED, payload=payload))


def cancels() -> ScriptedBackend:
    return ScriptedBackend(Outcome(CANCELLED))


def expires() -> ScriptedBackend:
    return ScriptedBackend(Outcome(EXPIRED))


def unavailable(reason: str) -> ScriptedBackend:
    return ScriptedBackend(Outcome(UNAVAILABLE, reason=reason))


# A stand-in for Bun. It records each invocation, prints a marker and the
# chart source on both streams (so a test can prove Fenric discards them),
# fails the type check when the source holds TYPE_ERROR, "transpiles" by
# wrapping the source, and misbehaves on request: a file named fail-install
# or hang beside it makes the install fail or the type check never return.
_BUN_STUB = """#!PYTHON
import pathlib, sys, time
args = sys.argv[1:]
here = pathlib.Path(__file__).resolve().parent
with open(here / "calls.log", "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\\n")
def source(path):
    return pathlib.Path(path).read_text(encoding="utf-8")
if args[:2] == ["install", "--frozen-lockfile"]:
    pathlib.Path("node_modules").mkdir(exist_ok=True)
    sys.exit(1 if (here / "fail-install").exists() else 0)
if args[:3] == ["x", "--no-install", "tsc"]:
    code = source(pathlib.Path(args[-1]).parent / "chart.ts")
    print("STUB-COMPILER-OUTPUT", code)
    print("STUB-COMPILER-OUTPUT", code, file=sys.stderr)
    if (here / "hang").exists():
        time.sleep(30)
    sys.exit(1 if "TYPE_ERROR" in code else 0)
if args[:1] == ["build"]:
    code = source(args[1])
    pathlib.Path(args[args.index("--outfile") + 1]).write_text(
        "(() => {" + code + "})();", encoding="utf-8"
    )
    sys.exit(0)
sys.exit(64)
"""


def install_bun_stub(directory: Path) -> Path:
    """Write the Bun stand-in into ``directory`` and return the calls log path."""
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "bun"
    stub.write_text(_BUN_STUB.replace("PYTHON", sys.executable, 1), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return directory / "calls.log"
