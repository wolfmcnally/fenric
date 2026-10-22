"""Hand one secret value to the caller's store command.

The command template is split the way a shell would split it, but no shell
runs it. ``{name}`` is replaced in each argument with the field's name, the
value goes to the command's standard input, and whatever the command prints
is discarded. Nothing here keeps or logs the value.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

STORE_TIMEOUT_SECONDS = 30


class StoreError(RuntimeError):
    """The store command could not be run or reported failure."""


def prepare_store(template: str, names: list[str]) -> tuple[tuple[str, ...], ...]:
    """Resolve each field's executable and exact argv before displaying them."""
    prepared = []
    try:
        template_args = shlex.split(template)
        for name in names:
            arguments = [part.replace("{name}", name) for part in template_args]
            program = shutil.which(arguments[0])
            if not program:
                raise ValueError
            arguments[0] = str(Path(program).absolute())
            prepared.append(tuple(arguments))
        return tuple(prepared)
    except (OSError, ValueError, IndexError):
        raise StoreError("store command could not be completed") from None


def run_store(arguments: tuple[str, ...], value: str) -> None:
    """Run exactly the displayed executable/arguments for one field."""
    try:
        completed = subprocess.run(
            arguments,
            input=value.encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=STORE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise StoreError("store command could not be completed") from exc
    if completed.returncode != 0:
        raise StoreError("store command reported failure")
