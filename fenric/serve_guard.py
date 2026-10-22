"""Own a foreground Serve process group until Fenric's input pipe closes."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time


def _signal(group: int, number: int) -> bool:
    try:
        os.killpg(group, number)
        return True
    except ProcessLookupError:
        return False


def _wait_group(group: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _signal(group, 0):
            return True
        time.sleep(0.02)
    return not _signal(group, 0)


def stop_group(child: subprocess.Popen[bytes]) -> None:
    """Stop/reap the owned group, including a non-exec wrapper's descendants."""
    # Serve's foreground context handles Interrupt; TERM may skip its
    # graceful cleanup. Never signal the parent's or another session's group.
    _signal(child.pid, signal.SIGINT)
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    if not _wait_group(child.pid, 2):
        _signal(child.pid, signal.SIGTERM)
        if not _wait_group(child.pid, 2):
            _signal(child.pid, signal.SIGKILL)
    child.wait(timeout=2)


def main() -> int:
    child = subprocess.Popen(
        sys.argv[1:],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # The CLI may be a shell wrapper without exec. The whole newly owned
        # group, not just that wrapper's PID, belongs to this guard.
        while child.poll() is None:
            ready, _, _ = select.select([sys.stdin.buffer], [], [], 0.1)
            if ready and not sys.stdin.buffer.read(1):
                break
    finally:
        stop_group(child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
