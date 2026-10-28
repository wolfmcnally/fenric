"""Opt-in synchronous default-mode Bash hook; activation is owner-controlled.

This is not an auto-mode classifier adapter and never settles a prior denial.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

from fenric.claude_permission import MAX_JSON_BYTES, _decision, _request
from fenric.host import Coordinator, Host
from fenric.mappings import _approval
from fenric.protocol import canonical, decision_digest, parse
from fenric.session import CallerSignals

LIFETIME_MS = 540_000  # Earlier than Claude's default 600s synchronous command-hook timeout.


def configuration(filename: str) -> dict:
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > MAX_JSON_BYTES
        ):
            raise ValueError
        config = parse(os.read(descriptor, MAX_JSON_BYTES + 1))
    finally:
        os.close(descriptor)
    if (
        set(config)
        != {
            "schema",
            "host",
            "uid",
            "project",
            "cwd",
            "session_id",
            "participant",
            "principal_policy",
            "folded_present",
            "coordinator",
            "expected_remote_login",
        }
        or config["schema"] != "fenric.native-permission.v1"
    ):
        raise ValueError
    if (
        config["host"] != socket.gethostname()
        or type(config["uid"]) is not int
        or config["uid"] != os.getuid()
        or type(config["folded_present"]) is not bool
        or not all(type(config[k]) is str and config[k] for k in ("project", "principal_policy"))
        or not Path(config["cwd"]).is_absolute()
    ):
        raise ValueError
    return config


def request(event: dict, config: dict, *, lifetime_ms: int = LIFETIME_MS) -> dict:
    issued = int(time.time() * 1000)
    _request(event, (issued, time.monotonic()))  # Exact default/Bash event, no tool_use_id.
    if (
        not 0 < lifetime_ms <= LIFETIME_MS
        or event["cwd"] != config["cwd"]
        or (config["session_id"] is not None and event["session_id"] != config["session_id"])
        or config["host"] != socket.gethostname()
        or config["uid"] != os.getuid()
    ):
        raise ValueError
    native = {
        k: None
        for k in (
            "request_id",
            "thread_id",
            "turn_id",
            "item_id",
            "tool_use_id",
            "approval_id",
            "started_at_ms",
            "agent_id",
        )
    }
    native.update(harness="claude", route="claude.permission", method="Bash")
    remote = config["folded_present"]
    if remote != (config["coordinator"] is not None):
        raise ValueError
    if remote and (not config["session_id"] or not config["participant"]):
        raise ValueError
    context = {
        "protocol": "fenric.operator-response.v1",
        "interaction_id": str(uuid.uuid4()),
        "audience": "fenric.native-permission",
        "host": config["host"],
        "environment": "native-permission-hook",
        "project": config["project"],
        "participant": config["participant"],
        "session": event["session_id"],
        "native": native,
        "kind": "approval",
        "original": json.loads(canonical(event)),
        "scope": {"category": "one-call-tool", "tool": "Bash"},
        "principal_policy": config["principal_policy"],
        "allowed_surfaces": ["remote" if remote else "local"],
        "issued_at_ms": issued,
        "expires_at_ms": issued + lifetime_ms,
        "destination": None,
        "choices": ["allow", "deny"],
    }
    context["expected_observations"] = [
        {
            "surface": "remote" if remote else "local",
            "observed_subject": config["expected_remote_login"] if remote else str(config["uid"]),
            "authentication_basis": "tailscale_user_login" if remote else "local_process_uid",
            "node": config["host"],
            "session": None,
        }
    ]
    spec, schema = _approval(context)
    context.update(
        response_schema=schema,
        presentation={
            "mapping": "fenric.claude-permission-request.v1",
            "spec": spec,
        },
    )
    return {
        "type": "request",
        "context": context,
        "decision_digest": decision_digest(context),
        "transport": {
            k: str(uuid.uuid4())
            for k in (
                "adapter_instance",
                "host_generation",
                "renderer_instance",
                "channel_id",
            )
        },
    }


def collect(event: dict, config: dict, *, backend=None, cancel=None) -> tuple[dict, dict | None]:
    response = None
    try:
        retained = request(event, config)
        before = hashlib.sha256(canonical(retained)).digest()
        coordinator = None
        if config["folded_present"]:
            value = config["coordinator"]
            coordinator = Coordinator(
                fingerprint=value["fingerprint"],
                folded=value["folded"],
                designation=value["designation"],
                nexus=value["nexus"],
                scope=value["scope"],
                binding=(
                    config["host"],
                    event["session_id"],
                    retained["transport"]["host_generation"],
                ),
            )
        response = Host().collect(
            retained,
            coordinator=coordinator,
            folded_present=config["folded_present"],
            backend=backend,
            cancel=cancel,
        )
        allow = (
            before == hashlib.sha256(canonical(retained)).digest()
            and response is not None
            and response["collection"] == "answered"
            and response["answer"] == "allow"
            and int(time.time() * 1000) < retained["context"]["expires_at_ms"]
        )
    except Exception:
        allow = False
    return _decision(allow), response


def main() -> int:
    if sys.argv[1:] == ["--help"]:
        print("usage: python -I -m fenric.native_permission OWNER_PROFILE.json")
        return 0
    decision = _decision(False)
    cancel, stop = threading.Event(), threading.Event()
    parent = os.getppid()

    def watch_parent() -> None:
        while not stop.wait(0.1):
            if os.getppid() != parent:
                cancel.set()
                return

    watcher = threading.Thread(target=watch_parent, daemon=True)
    try:
        if len(sys.argv) != 2:
            raise ValueError
        config = configuration(sys.argv[1])
        event = parse(sys.stdin.buffer.read(MAX_JSON_BYTES + 1))
        with CallerSignals() as signals:
            cancel = signals.cancel
            watcher.start()
            decision, _response = collect(event, config, cancel=cancel)
        if os.getppid() != parent or configuration(sys.argv[1]) != config:
            decision = _decision(False)
    except Exception:
        decision = _decision(False)
    finally:
        stop.set()
        if watcher.ident is not None:
            watcher.join(1)
    # Native PermissionRequest ignores exit2. Emit an explicit synchronous decision.
    print(json.dumps(decision, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
