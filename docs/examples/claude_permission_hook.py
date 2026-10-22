"""Opt-in one-command Claude PermissionRequest example; no SDK or bus dependency.

This module launches nothing on import. The per-test scope and relay must be
explicitly approved before main is configured as a future daemon-session hook.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

TEST_COMMAND = "/usr/bin/printf 'FENRIC CLAUDE BASH TEST\\n'"


class Refused(ValueError):
    pass


def require(value: bool, reason: str = "scope") -> None:
    if not value:
        raise Refused(reason)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def strict_json(raw: bytes) -> dict:
    require(0 < len(raw) <= 65_536)

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result

    value = json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(Refused("invalid JSON")),
    )
    require(type(value) is dict)
    canonical(value)  # Also refuses nonfinite numeric overflow.
    return value


def native_decision(allow: bool) -> dict:
    decision = (
        {"behavior": "allow"}
        if allow
        else {
            "behavior": "deny",
            "message": "Disposable phone test denied, cancelled or unavailable",
            "interrupt": True,
        }
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }


def guard_future_bash(event: dict, scope: dict) -> dict:
    """Force an exact future scratch Bash to ask; never auto-allow or change rules."""
    exact = (
        event.get("hook_event_name") == "PreToolUse"
        and event.get("session_id") == scope.get("session_id")
        and event.get("cwd") == scope.get("cwd")
        and type(scope.get("permission_mode")) is str
        and event.get("permission_mode") == scope["permission_mode"]
        and event.get("tool_name") == "Bash"
        and type(scope.get("tool_input")) is dict
        and scope["tool_input"].get("command") == TEST_COMMAND
        and canonical(event.get("tool_input")) == canonical(scope.get("tool_input"))
        and scope.get("relay_approved") is True
        and type(scope.get("expires_at_ms")) is int
        and int(time.time() * 1000) < scope["expires_at_ms"]
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask" if exact else "deny",
            "permissionDecisionReason": "Exact disposable test requires real operator decision",
        }
    }


def evaluate(event: dict, scope: dict, collect: Callable[[dict], dict]) -> tuple[dict, dict]:
    """Bind consent to this live hook invocation; no fabricated tool_use_id."""
    receipt = {"native_hook": "PermissionRequest", "outcome": "refused"}
    try:
        require(
            set(scope)
            == {
                "session_id",
                "cwd",
                "permission_mode",
                "tool_input",
                "expires_at_ms",
                "relay_approved",
                "host_label",
                "relay_command",
                "expected_remote_login",
            },
            "scope",
        )
        require(
            all(
                type(scope[k]) is str and bool(scope[k])
                for k in ["session_id", "cwd", "permission_mode"]
            ),
            "scope",
        )
        require(event.get("hook_event_name") == "PermissionRequest", "event")
        require("tool_use_id" not in event, "native-shape")
        require(event.get("session_id") == scope["session_id"], "session")
        require(event.get("cwd") == scope["cwd"], "cwd")
        require(type(scope.get("permission_mode")) is str)
        require(event.get("permission_mode") == scope["permission_mode"], "mode")
        require(event.get("tool_name") == "Bash", "tool")
        require(canonical(event.get("tool_input")) == canonical(scope["tool_input"]), "input")
        root = Path(scope["cwd"])
        require(type(scope["tool_input"]) is dict)
        require(scope["tool_input"].get("command") == TEST_COMMAND, "command")
        require(set(scope["tool_input"]) <= {"command", "description"}, "input")
        require(
            "description" not in scope["tool_input"]
            or type(scope["tool_input"]["description"]) is str,
            "input",
        )
        require(
            root.is_absolute() and root.resolve().parent == Path(tempfile.gettempdir()).resolve()
        )
        require(
            root.name.startswith("fenric-native-bash-test-")
            and root.is_dir()
            and not root.is_symlink()
        )
        require(type(scope["expires_at_ms"]) is int)
        now = int(time.time() * 1000)
        require(now < scope["expires_at_ms"] <= now + 900_000, "expiry")
        require(scope.get("relay_approved") is True)
        require(type(scope.get("host_label")) is str and bool(scope["host_label"]), "host-label")
        relay = scope.get("relay_command")
        require(
            type(relay) is list and bool(relay) and all(type(v) is str for v in relay),
            "relay-scope",
        )
        require(Path(relay[0]).is_absolute() and Path(relay[0]).is_file(), "relay-scope")
        require(
            type(scope.get("expected_remote_login")) is str
            and bool(scope["expected_remote_login"]),
            "identity-scope",
        )
        nonce = str(uuid.uuid4())
        retained = {
            "session_id": event["session_id"],
            "cwd": event["cwd"],
            "tool_name": "Bash",
            "tool_input": json.loads(canonical(event["tool_input"])),
            "original": json.loads(canonical(event)),
            "nonce": nonce,
            "expires_at_ms": scope["expires_at_ms"],
            "host_label": scope["host_label"],
        }
        digest = hashlib.sha256(canonical(retained)).hexdigest()
        claim = root / "phone-test-hook.claim"
        fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(
                {
                    "nonce": nonce,
                    "context_digest": digest,
                    "expires_at_ms": scope["expires_at_ms"],
                },
                stream,
            )
        receipt.update(
            nonce=nonce,
            context_digest=digest,
            session_id=event["session_id"],
            expected_effect="stdout-only demonstration",
        )
        deadline = time.monotonic() + (scope["expires_at_ms"] - now) / 1000
        answer = collect(json.loads(canonical({**retained, "context_digest": digest})))
        require(
            time.monotonic() < deadline and int(time.time() * 1000) < scope["expires_at_ms"],
            "clock",
        )
        receipt["response_status"] = answer.get("status")
        receipt["response_choice"] = answer.get("choice")
        receipt["authenticated_remote"] = answer.get("authenticated_remote") is True
        if answer.get("refusal") in {"relay", "remote-unavailable"}:
            raise Refused(answer["refusal"])
        require(answer.get("authenticated_remote") is True, "surface")
        allow = answer.get("status") == "ok" and answer.get("choice") == "allow"
        receipt["outcome"] = "allow-returned" if allow else "deny-returned"
        receipt["native_delivery"] = "hook-stdout-prepared-not-proved-consumed"
        return native_decision(allow), receipt
    except FileExistsError:
        receipt["refusal"] = "claim-exists"
        return native_decision(False), receipt
    except Refused as exc:
        receipt["refusal"] = str(exc)  # Internal fixed labels only; never parser/input text.
        return native_decision(False), receipt
    except Exception:  # noqa: BLE001 -- Any collector failure must deny, never grant.
        receipt["refusal"] = "collection-error"
        return native_decision(False), receipt


def presentation(retained: dict) -> dict:
    expiry = (
        datetime.datetime.fromtimestamp(retained["expires_at_ms"] / 1000, datetime.UTC)
        .astimezone()
        .strftime("%A, %B %d, %Y at %I:%M:%S %p %Z")
    )
    return {
        "title": retained["host_label"] + " / Claude / TEST",
        "message": (
            "Source: " + retained["host_label"] + " / Claude standard daemon\n\n"
            "Allow once would run this exact Bash input in:\n"
            + retained["cwd"]
            + "\n\n"
            + retained["tool_input"]["command"]
            + "\n\nComplete input (JSON):\n"
            + json.dumps(retained["tool_input"], ensure_ascii=False, sort_keys=True)
            + "\n\nOnly this one stdout demonstration is in scope. "
            "No future permissions are granted.\n"
            "Deny or Cancel runs nothing.\n\nExpires: " + expiry
        ),
    }


def collect_phone(retained: dict, scope: dict) -> dict:
    """Use current installed Fenric, with URLs confined to the approved relay child."""
    from fenric import backend
    from fenric.engine import run
    from fenric.remote import _identity, read_config

    require(scope.get("relay_approved") is True)
    relay = scope["relay_command"]
    require(type(relay) is list and relay and all(type(part) is str for part in relay))
    require(Path(relay[0]).is_absolute() and Path(relay[0]).is_file())
    config = read_config()
    require(config is not None and config["enabled"])
    before_identity = _identity(config["tailscale"], 5)
    require(before_identity[1] == scope["expected_remote_login"], "identity")
    cancel = threading.Event()
    seconds = max(0.01, (retained["expires_at_ms"] - int(time.time() * 1000)) / 1000)
    timer = threading.Timer(seconds, cancel.set)
    timer.daemon = True
    parent_pid = os.getppid()
    stop = threading.Event()

    def watch_parent():
        while not stop.wait(0.1):
            if os.getppid() != parent_pid:
                cancel.set()
                return

    watcher = threading.Thread(target=watch_parent, daemon=True)
    session_holder = []
    failures = []

    class ObservingBackend:
        def show(self, page, session):
            session_holder.append(session)
            return backend.default_backend().show(page, session)

    def availability(status):
        if status["status"] != "available":
            failures.append("remote-unavailable")
            cancel.set()
            return
        try:
            # Relay command is per-test owner configuration, not a product dependency.
            result = subprocess.run(
                relay,
                input=status["url"].encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode:
                failures.append("relay")
                cancel.set()
        except Exception:  # noqa: BLE001 -- Relay failures cancel the pending interaction.
            failures.append("relay")
            cancel.set()

    spec = {
        "verb": "ask",
        **presentation(retained),
        "timeout": seconds,
        "questions": [
            {
                "id": "decision",
                "kind": "choice",
                "prompt": "Allow this exact scratch Bash once?",
                "options": [
                    {"id": "allow", "label": "Allow once"},
                    {"id": "deny", "label": "Deny"},
                ],
            }
        ],
    }
    timer.start()
    watcher.start()
    try:
        # Ordinary Fenric stderr contains availability URLs. Never propagate/store it.
        with contextlib.redirect_stderr(io.StringIO()):
            result = run(
                spec,
                backend=ObservingBackend(),
                remote_status=availability,
                cancel=cancel,
            )
        after_identity = _identity(config["tailscale"], 5)
        require(before_identity == after_identity, "identity")
        require(session_holder)
        # Version-bound test observation: installed Session records desktop or validated
        # remote request marker. This is not a new public Fenric provenance API.
        marker = getattr(session_holder[0], "_source", None)
        remote_won = type(marker) is str and marker != "desktop"
        if remote_won:
            parsed = uuid.UUID(marker)
            require(parsed.version == 4 and str(parsed) == marker)
        return {
            "nonce": retained["nonce"],
            "context_digest": retained["context_digest"],
            "session_id": retained["session_id"],
            "status": result.status,
            "choice": result.value.get("decision")
            if result.status == "ok" and type(result.value) is dict
            else None,
            "authenticated_remote": remote_won,
            "refusal": failures[0] if failures else None,
        }
    finally:
        timer.cancel()
        stop.set()
        watcher.join(timeout=1)


def main() -> int:
    # No scope is provided or configured by preparation. Accidental use refuses.
    if len(sys.argv) != 2:
        return 4
    receipt = {"outcome": "refused-before-collection"}
    decision = native_decision(False)
    event = {}
    try:
        event = strict_json(sys.stdin.buffer.read(65_537))
        if event.get("hook_event_name") not in {"PreToolUse", "PermissionRequest"}:
            return 4  # No callback exists here; no native answer or retry is emitted.
        scope = strict_json(Path(sys.argv[1]).read_bytes())
        if event.get("hook_event_name") == "PreToolUse":
            print(
                json.dumps(guard_future_bash(event, scope), separators=(",", ":")),
                flush=True,
            )
            return 0
        decision, receipt = evaluate(event, scope, lambda retained: collect_phone(retained, scope))
        receipt_root = Path(scope["cwd"])
        require(receipt_root.name.startswith("fenric-native-bash-test-"))
        require(receipt_root.resolve().parent == Path(tempfile.gettempdir()).resolve())
        require(not receipt_root.is_symlink())
        receipt_path = receipt_root / "phone-test-hook-receipt.json"
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(receipt, stream, indent=2)
    except Exception:  # noqa: BLE001 -- Hook boundary always fails closed.
        if event.get("hook_event_name") == "PreToolUse":
            print(json.dumps(guard_future_bash(event, {}), separators=(",", ":")), flush=True)
            return 0
        if event.get("hook_event_name") != "PermissionRequest":
            return 4
        decision = native_decision(False)
    print(json.dumps(decision, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
