"""Opt-in, system-wide manual Bash PermissionRequest handler.

Run with an owner-configured JSON filename, as a module or isolated script.
Importing this module installs nothing and launches no dialog or command.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LIFETIME_SECONDS = 900
MIN_DELIVERY_SECONDS = 600
MAX_JSON_BYTES = 65_536


class _Unavailable(ValueError):
    """No helper decision: the original native permission flow remains in force."""


def _require(condition: bool) -> None:
    if not condition:
        raise _Unavailable


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _json(raw: bytes) -> dict[str, Any]:
    from fenric.remote import _json as decode

    _require(0 < len(raw) <= MAX_JSON_BYTES)
    value = decode(raw)
    _require(type(value) is dict)
    _canonical(value)
    return value


def _configuration(filename: str) -> dict[str, Any]:
    path = Path(filename)
    info = path.stat()
    _require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_size <= MAX_JSON_BYTES
    )
    config = _json(path.read_bytes())
    _require(
        set(config) == {"schema", "host_label", "expected_remote_login", "relay_command"}
        and config["schema"] == "fenric.claude-permission.v1"
    )
    for name in ["host_label", "expected_remote_login"]:
        _require(type(config[name]) is str and bool(config[name].strip()))
    relay = config["relay_command"]
    _require(
        type(relay) is list
        and bool(relay)
        and all(type(part) is str and "\0" not in part for part in relay)
        and Path(relay[0]).is_absolute()
        and Path(relay[0]).is_file()
        and os.access(relay[0], os.X_OK)
    )
    return config


@dataclass(frozen=True)
class _Request:
    original_json: str
    digest: str
    request_id: str
    expires_at_ms: int
    deadline: float

    def remaining(self) -> float:
        return min(
            self.deadline - time.monotonic(),
            (self.expires_at_ms - int(time.time() * 1000)) / 1000,
        )


def _request(event: dict[str, Any], started: tuple[int, float]) -> _Request:
    _require(event.get("hook_event_name") == "PermissionRequest")
    _require(event.get("tool_name") == "Bash" and event.get("permission_mode") == "default")
    _require(type(event.get("session_id")) is str and bool(event["session_id"]))
    _require("tool_use_id" not in event)
    _require(type(event.get("cwd")) is str and Path(event["cwd"]).is_absolute())
    tool_input = event.get("tool_input")
    _require(type(tool_input) is dict and set(tool_input) <= {"command", "description", "timeout"})
    _require(type(tool_input.get("command")) is str and bool(tool_input["command"].strip()))
    _require("description" not in tool_input or type(tool_input["description"]) is str)
    _require(
        "timeout" not in tool_input
        or (type(tool_input["timeout"]) is int and tool_input["timeout"] > 0)
    )
    original = _canonical(event)
    return _Request(
        original.decode(),
        hashlib.sha256(original).hexdigest(),
        str(uuid.uuid4()),
        started[0] + LIFETIME_SECONDS * 1000,
        started[1] + LIFETIME_SECONDS,
    )


def _decision(allow: bool) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"}
            if allow
            else {
                "behavior": "deny",
                "message": "Permission declined, cancelled or unavailable",
                "interrupt": True,
            },
        }
    }


def _collect(request: _Request, config: dict[str, Any]) -> bool:
    from fenric import backend
    from fenric.engine import run
    from fenric.remote import _identity, read_config

    try:
        remote_config = read_config()
        _require(
            remote_config is not None and remote_config["enabled"] and not remote_config["announce"]
        )
        before_identity = _identity(remote_config["tailscale"], 5)
        _require(before_identity[1] == config["expected_remote_login"])
    except Exception:
        raise _Unavailable from None
    _require(request.remaining() > 0)
    original = json.loads(request.original_json)
    expiry = datetime.datetime.fromtimestamp(request.expires_at_ms / 1000, datetime.UTC).isoformat()
    spec = {
        "verb": "ask",
        "title": config["host_label"] + " / Claude / Bash permission",
        "message": (
            "Original pending Bash request in:\n"
            + original["cwd"]
            + "\n\n"
            + original["tool_input"]["command"]
            + "\n\nComplete input (JSON):\n"
            + json.dumps(original["tool_input"], ensure_ascii=False, sort_keys=True)
            + "\n\nAllow once applies only to this pending request. No input, rule or permission "
            "updates are applied. Remote Deny or Cancel rejects it.\nImmutable expiry: "
            + expiry
            + "\nOnly the authenticated remote surface can allow. Closing the local window "
            "returns to the native permission controls without granting permission."
        ),
        "timeout": request.remaining(),
        "questions": [
            {
                "id": "decision",
                "kind": "choice",
                "prompt": "Allow this exact Bash once?",
                "options": [
                    {"id": "allow", "label": "Allow once"},
                    {"id": "deny", "label": "Deny"},
                ],
            }
        ],
    }
    cancel = threading.Event()
    stop = threading.Event()
    timer = threading.Timer(max(0, request.remaining()), cancel.set)
    timer.daemon = True
    parent = os.getppid()
    sessions: list[Any] = []
    relay_started = False
    failed = False
    remote_unavailable = False

    def watch_parent() -> None:
        while not stop.wait(0.1):
            if os.getppid() != parent:
                cancel.set()
                return

    class ObservingBackend:
        def show(self, page: Any, session: Any) -> Any:
            sessions.append(session)
            return backend.default_backend().show(page, session)

    def availability(status: dict[str, Any]) -> None:
        nonlocal relay_started, failed, remote_unavailable
        if status["status"] != "available":
            remote_unavailable = True
            cancel.set()
            return
        if relay_started or request.remaining() < MIN_DELIVERY_SECONDS:
            failed = True
            cancel.set()
            return
        relay_started = True
        payload = {
            "schema": "fenric.permission-availability.v1",
            "status": "available",
            "request_id": request.request_id,
            "native_session_id": original["session_id"],
            "host_label": config["host_label"],
            "expires_at_ms": request.expires_at_ms,
            "url": status["url"],
        }
        try:
            sent = subprocess.run(
                config["relay_command"],
                input=_canonical(payload),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if sent.returncode:
                raise _Unavailable
        except Exception:  # noqa: BLE001 -- No uncertain relay retry or native allow.
            failed = True
            cancel.set()

    watcher = threading.Thread(target=watch_parent, daemon=True)
    timer.start()
    watcher.start()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run(
                spec, backend=ObservingBackend(), remote_status=availability, cancel=cancel
            )
        if request.remaining() <= 0 or os.getppid() != parent or (failed and relay_started):
            return False
        if failed and not relay_started:
            raise _Unavailable
        marker = getattr(sessions[0], "_source", None) if sessions else None
        if marker == "desktop" or remote_unavailable or result.status == "unavailable":
            raise _Unavailable
        after_identity = _identity(remote_config["tailscale"], 5)
        remote_won = (
            type(marker) is str
            and uuid.UUID(marker).version == 4
            and str(uuid.UUID(marker)) == marker
        )
        return (
            not failed
            and relay_started
            and request.remaining() > 0
            and os.getppid() == parent
            and before_identity == after_identity
            and remote_won
            and result.status == "ok"
            and result.value == {"decision": "allow"}
        )
    except _Unavailable:
        raise
    except Exception:  # noqa: BLE001 -- Collection errors never become native consent.
        return False
    finally:
        timer.cancel()
        stop.set()
        watcher.join(timeout=1)


def main() -> int:
    if sys.argv[1:] == ["--help"]:
        print("usage: python -I -m fenric.claude_permission CONFIG")
        return 0
    if len(sys.argv) != 2:
        return 4
    try:
        raw = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            print("Fenric approval unavailable; native permission controls apply.", file=sys.stderr)
            return 0
        event = _json(raw)
    except Exception:  # noqa: BLE001 -- No fabricated callback for an unreadable event.
        return 4
    if event.get("hook_event_name") != "PermissionRequest":
        return 0
    started = (int(time.time() * 1000), time.monotonic())
    try:
        config = _configuration(sys.argv[1])
        request = _request(event, started)
    except Exception:
        print("Fenric approval unavailable; native permission controls apply.", file=sys.stderr)
        return 0
    try:
        allow = _collect(request, config)
        allow = allow and request.remaining() > 0
    except _Unavailable:
        print("Fenric approval unavailable; native permission controls apply.", file=sys.stderr)
        return 0
    except Exception:  # noqa: BLE001 -- Unexpected failures do not authorize native execution.
        allow = False
    print(json.dumps(_decision(allow), separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
