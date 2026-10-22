"""The opt-in native example is source, not an installed hook or live approval."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fenric import backend, engine, remote

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/claude_permission_hook.py"


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("claude_permission_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def request_case(hook):
    with tempfile.TemporaryDirectory(prefix="fenric-native-bash-test-") as temporary:
        root = Path(temporary).resolve()
        scope = {
            "session_id": "synthetic-session",
            "cwd": str(root),
            "permission_mode": "default",
            "tool_input": {"command": hook.TEST_COMMAND},
            "expires_at_ms": int(time.time() * 1000) + 30_000,
            "relay_approved": True,
            "host_label": "Synthetic host",
            "relay_command": [sys.executable],
            "expected_remote_login": "synthetic-owner",
        }
        event = {
            "hook_event_name": "PermissionRequest",
            "session_id": scope["session_id"],
            "cwd": str(root),
            "permission_mode": scope["permission_mode"],
            "tool_name": "Bash",
            "tool_input": copy.deepcopy(scope["tool_input"]),
        }
        yield root, scope, event


def behavior(output):
    return output["hookSpecificOutput"]["decision"]["behavior"]


def test_callback_scope_and_once(hook):
    for status, choice, expected in [
        ("ok", "allow", "allow"),
        ("ok", "deny", "deny"),
        ("cancelled", None, "deny"),
        ("unavailable", None, "deny"),
        ("error", "allow", "deny"),
        ("ok", "allowForSession", "deny"),
    ]:
        with request_case(hook) as (root, scope, event):
            event["permission_suggestions"] = [{"type": "addRules", "rules": []}]
            event["optional"] = None
            original = copy.deepcopy(event)

            def collect(retained):
                assert retained["original"] == original
                assert retained["tool_input"] == original["tool_input"]
                return {"authenticated_remote": True, "status": status, "choice": choice}

            output, receipt = hook.evaluate(event, scope, collect)
            assert behavior(output) == expected
            assert receipt["response_status"] == status
            assert receipt["native_delivery"] == "hook-stdout-prepared-not-proved-consumed"
            assert event == original
            assert (root / "phone-test-hook.claim").stat().st_mode & 0o777 == 0o600
            if expected == "allow":
                assert output["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
            calls = []
            assert behavior(hook.evaluate(event, scope, lambda r: calls.append(r))[0]) == "deny"
            assert calls == []

    for field, changed in [
        ("session_id", "foreign"),
        ("cwd", "/not-the-approved-directory"),
        ("tool_name", "Write"),
        ("hook_event_name", "PermissionDenied"),
        ("permission_mode", "bypassPermissions"),
        ("tool_use_id", "unexpected-native-shape"),
        ("tool_input", {"command": "echo unexpected"}),
        ("tool_input", {"command": hook.TEST_COMMAND, "timeout": None}),
    ]:
        with request_case(hook) as (_, scope, event):
            event[field] = changed
            calls = []
            assert behavior(hook.evaluate(event, scope, lambda r: calls.append(r))[0]) == "deny"
            assert calls == []
    with request_case(hook) as (_, scope, event):
        event["hook_event_name"] = "PreToolUse"
        assert (
            hook.guard_future_bash(event, scope)["hookSpecificOutput"]["permissionDecision"]
            == "ask"
        )
        event["tool_input"]["command"] = "echo unexpected"
        guarded = hook.guard_future_bash(event, scope)["hookSpecificOutput"]
        assert guarded["permissionDecision"] == "deny"
        assert "updatedInput" not in guarded

    for missing in ["host_label", "relay_command", "expected_remote_login"]:
        with request_case(hook) as (root, scope, event):
            scope.pop(missing)
            output, receipt = hook.evaluate(event, scope, lambda _: pytest.fail("must not collect"))
            assert behavior(output) == "deny"
            assert receipt["refusal"] == "scope"
            assert not (root / "phone-test-hook.claim").exists()
    with request_case(hook) as (root, scope, event):
        scope["tool_input"]["dangerouslyDisableSandbox"] = True
        event["tool_input"] = copy.deepcopy(scope["tool_input"])
        output, receipt = hook.evaluate(event, scope, lambda _: pytest.fail("must not collect"))
        assert behavior(output) == "deny"
        assert receipt["refusal"] == "input"
        assert not (root / "phone-test-hook.claim").exists()
    with request_case(hook) as (_, scope, event):

        def broken(_):
            raise RuntimeError("SYNTHETIC-PRIVATE")

        output, receipt = hook.evaluate(event, scope, broken)
        assert behavior(output) == "deny"
        assert receipt["refusal"] == "collection-error"
        assert "SYNTHETIC-PRIVATE" not in json.dumps(receipt)


def test_deadlines_original_and_presentation(hook, monkeypatch):
    for elapsed, wall, expected in [
        (20, 1020, "allow"),
        (30, 1030, "deny"),
        (20, 1030, "deny"),
        (30, 1020, "deny"),
    ]:
        with request_case(hook) as (_, scope, event), monkeypatch.context() as patch:
            scope["expires_at_ms"] = 1_030_000
            monotonic = iter([50, 50 + elapsed])
            wallclock = iter([1000, wall])
            patch.setattr(hook.time, "time", lambda: next(wallclock))
            patch.setattr(hook.time, "monotonic", lambda: next(monotonic))
            seen = []

            def collect(retained):
                seen.append(retained)
                return {"authenticated_remote": True, "status": "ok", "choice": "allow"}

            output, _ = hook.evaluate(event, scope, collect)
            assert len(seen) == 1  # The intended deadline guard is reached after collection.
            assert behavior(output) == expected
    for amendment in [
        {"expires_at_ms": 0},
        {"expires_at_ms": int(time.time() * 1000) + 910_000},
        {"relay_approved": False},
    ]:
        with request_case(hook) as (_, scope, event):
            scope.update(amendment)
            calls = []
            assert behavior(hook.evaluate(event, scope, lambda r: calls.append(r))[0]) == "deny"
            assert calls == []
    with request_case(hook) as (_, scope, _):
        view = hook.presentation(scope)
        assert hook.TEST_COMMAND in view["message"]
        assert scope["cwd"] in view["message"]
        assert "No future permissions" in view["message"]
        assert "Expires:" in view["message"]
        assert scope["session_id"] not in view["message"]
    for raw in [b'{"a":1,"a":2}', b'{"overflow":1e999}', b'{"nan":NaN}']:
        with pytest.raises(ValueError):
            hook.strict_json(raw)


def test_hook_process_boundary(hook, monkeypatch, capsys):
    with request_case(hook) as (root, scope, event):
        filename = root / "scope.json"
        filename.write_text(json.dumps(scope))
        for name in ["PermissionDenied", "Notification"]:
            event["hook_event_name"] = name
            run = subprocess.run(
                [sys.executable, "-B", str(EXAMPLE), str(filename)],
                input=json.dumps(event),
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert run.returncode == 4
            assert run.stdout == run.stderr == ""
            assert not (root / "phone-test-hook.claim").exists()
        failed = subprocess.run(
            [sys.executable, "-B", str(EXAMPLE), str(filename)],
            input='{"secret":"SYNTHETIC-PRIVATE",',
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert failed.returncode == 4
        assert failed.stdout == ""
        assert "SYNTHETIC-PRIVATE" not in failed.stdout + failed.stderr

    with request_case(hook) as (root, scope, event):
        filename = root / "scope.json"
        filename.write_text(json.dumps(scope))
        monkeypatch.setattr(hook.sys, "argv", [str(EXAMPLE), str(filename)])
        collected = []

        def collect(retained, configuration):
            collected.append(retained)
            return {"authenticated_remote": True, "status": "ok", "choice": "allow"}

        monkeypatch.setattr(hook, "collect_phone", collect)

        def invoke():
            monkeypatch.setattr(
                hook.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(event).encode()))
            )
            assert hook.main() == 0
            return capsys.readouterr()

        first = invoke()
        lines = first.out.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
        receipt = root / "phone-test-hook-receipt.json"
        original_receipt = receipt.read_bytes()
        assert receipt.stat().st_mode & 0o777 == 0o600
        assert json.loads(original_receipt)["outcome"] == "allow-returned"
        second = invoke()
        assert behavior(json.loads(second.out)) == "deny"
        assert receipt.read_bytes() == original_receipt
        assert len(collected) == 1
        # Bad scope still returns the correct event-specific refusal shape.
        filename.write_text("invalid")
        event["hook_event_name"] = "PreToolUse"
        rejected = invoke()
        value = json.loads(rejected.out)["hookSpecificOutput"]
        assert value["hookEventName"] == "PreToolUse"
        assert value["permissionDecision"] == "deny"
        monkeypatch.setattr(hook.sys, "argv", [str(EXAMPLE)])
        assert hook.main() == 4
        assert capsys.readouterr().out == ""


def test_phone_collection_uses_real_session(hook, monkeypatch):
    identity = ("node", "synthetic-owner")
    for case in [
        "allow",
        "deny",
        "cancel",
        "desktop",
        "host",
        "bad-marker",
        "identity-drift",
        "relay-failure",
        "relay-timeout",
        "foreign-account",
        "unavailable",
    ]:
        with request_case(hook) as (_, scope, event), monkeypatch.context() as patch:
            scope.update(relay_command=[sys.executable], expected_remote_login="synthetic-owner")
            identities = iter(
                [identity, ("node", "changed-owner") if case == "identity-drift" else identity]
            )
            patch.setattr(remote, "read_config", lambda: {"enabled": True, "tailscale": "unused"})
            patch.setattr(remote, "_identity", lambda *a: next(identities))
            if case == "foreign-account":
                scope["expected_remote_login"] = "foreign"
            marker = (
                "desktop"
                if case == "desktop"
                else "host"
                if case == "host"
                else "invalid"
                if case == "bad-marker"
                else str(uuid.uuid4())
            )
            sessions = []
            relays = []
            cancellations = []
            submissions = []

            def relay(argv, **kwargs):
                relays.append((argv, kwargs))
                if case == "relay-timeout":
                    raise subprocess.TimeoutExpired(argv, 10)
                return SimpleNamespace(returncode=1 if case == "relay-failure" else 0)

            patch.setattr(hook.subprocess, "run", relay)

            class FakeRemote:
                def __init__(self, page, session, *, callback=None, **kwargs):
                    self.callback = callback
                    self.session = session

                def start(self):
                    self.callback(
                        {"status": "unavailable", "url": None, "reason": "not configured"}
                        if case == "unavailable"
                        else {"status": "available", "url": "https://synthetic.invalid/capability"}
                    )

                def close(self):
                    pass

            class SyntheticBackend:
                def show(self, page, session):
                    sessions.append(session)
                    cancellations.append(session._cancel.is_set())
                    if case.startswith("relay-") or case == "unavailable":
                        session.settle(backend.Outcome(backend.CANCELLED))
                    elif case == "cancel":
                        session.settle(backend.Outcome(backend.CANCELLED), marker=marker)
                    else:
                        # Real Session owns validation, source recording and settlement.
                        submissions.append(
                            session.submit(
                                {"action": "ok", "values": {"q0": 1 if case == "deny" else 0}},
                                marker=marker,
                            )
                        )
                    return session.outcome

            patch.setattr(engine, "Remote", FakeRemote)
            patch.setattr(backend, "default_backend", SyntheticBackend)
            output, receipt = hook.evaluate(
                event, scope, lambda retained: hook.collect_phone(retained, scope)
            )
            assert behavior(output) == ("allow" if case == "allow" else "deny")
            if case == "foreign-account":
                assert sessions == relays == []
            else:
                assert len(sessions) == 1
                assert len(relays) == (0 if case == "unavailable" else 1)
                assert sessions[0].done.is_set()
                if relays:
                    assert relays[0][1]["input"] == b"https://synthetic.invalid/capability"
                    assert relays[0][1]["stdout"] == subprocess.DEVNULL
                    assert relays[0][1]["stderr"] == subprocess.DEVNULL
                if case.startswith("relay-") or case == "unavailable":
                    assert cancellations == [True]
                    assert receipt["refusal"] == (
                        "remote-unavailable" if case == "unavailable" else "relay"
                    )
                elif case != "cancel":
                    assert submissions == [True]
                if case == "deny":
                    assert receipt["response_status"] == "ok"
                    assert receipt["response_choice"] == "deny"
                if case == "cancel":
                    assert receipt["response_status"] == "cancelled"
            if case == "allow":
                assert receipt["authenticated_remote"] is True
                assert receipt["response_choice"] == "allow"
