"""Native callback ownership, not a live Claude or GUI qualification."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from fenric import backend, engine, remote
from fenric import claude_permission as hook


def event(cwd="/synthetic/project"):
    return {
        "hook_event_name": "PermissionRequest",
        "session_id": "native-session",
        "cwd": cwd,
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "printf 'original'", "description": "literal input"},
        "unknown_native_metadata": None,
    }


def config(tmp_path):
    value = {
        "schema": "fenric.claude-permission.v1",
        "host_label": "Synthetic host",
        "expected_remote_login": "synthetic-owner",
        "relay_command": [str(Path(sys.executable).resolve())],
    }
    filename = tmp_path / "config.json"
    filename.write_text(json.dumps(value))
    filename.chmod(0o600)
    return filename, value


def request(value):
    return hook._request(value, (int(time.time() * 1000), time.monotonic()))


def test_native_scope_original_and_global_reach(tmp_path):
    filename, settings = config(tmp_path)
    assert hook._configuration(str(filename)) == settings
    ids = set()
    for cwd in ["/first/project", "/second/project", "/symlink-spelling"]:
        original = event(cwd)
        original["permission_suggestions"] = [{"type": "addRules", "rules": []}]
        retained = request(original)
        ids.add(retained.request_id)
        assert json.loads(retained.original_json) == original
        assert retained.digest == hook.hashlib.sha256(retained.original_json.encode()).hexdigest()
        assert 899 < retained.remaining() <= 900
        assert hook._decision(True)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
    assert len(ids) == 3
    for field, bad in [
        ("tool_name", "Write"),
        ("permission_mode", "auto"),
        ("hook_event_name", "PermissionDenied"),
        ("session_id", ""),
        ("cwd", "relative"),
        ("tool_use_id", "not-supplied-by-native-event"),
        ("tool_input", {"command": "original", "run_in_background": True}),
        ("tool_input", {"command": "original", "dangerouslyDisableSandbox": True}),
        ("tool_input", {"command": "original", "timeout": True}),
    ]:
        value = event()
        value[field] = bad
        with pytest.raises(ValueError):
            request(value)
    for raw in [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}']:
        with pytest.raises(ValueError):
            hook._json(raw)
    filename.chmod(0o644)
    with pytest.raises(ValueError):
        hook._configuration(str(filename))


def collect_case(
    monkeypatch, settings, retained, *, case="allow", previous=None, advance=None, observations=None
):
    observations = (
        observations if observations is not None else {"sessions": [], "relays": [], "submits": []}
    )
    identity = iter(
        [("node", "synthetic-owner"), ("changed" if case == "drift" else "node", "synthetic-owner")]
    )
    monkeypatch.setattr(
        remote, "read_config", lambda: {"enabled": True, "announce": [], "tailscale": "synthetic"}
    )
    monkeypatch.setattr(remote, "_identity", lambda *args: next(identity))
    marker = "desktop" if case == "desktop" else str(uuid.uuid4())

    def relay(argv, **kwargs):
        observations["relays"].append((argv, kwargs))
        if case == "relay-timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return SimpleNamespace(returncode=1 if case == "relay-failed" else 0)

    monkeypatch.setattr(hook.subprocess, "run", relay)

    class SyntheticRemote:
        def __init__(self, page, session, *, callback=None, **kwargs):
            self.callback = callback

        def start(self):
            self.callback(
                {"status": "unavailable", "url": None}
                if case == "remote-unavailable"
                else {"status": "available", "url": "https://synthetic.invalid/private"}
            )

        def close(self):
            pass

    class SyntheticBackend:
        def show(self, page, session):
            if case == "render-unavailable":
                raise RuntimeError("synthetic render unavailable")
            observations["sessions"].append(session)
            session.touch()
            if advance is not None:
                advance()
            if previous is not None:
                before = session.done.is_set()
                previous.submit({"action": "ok", "values": {"q0": 0}}, marker=marker)
                observations["old_proof_left_pending"] = not before and not session.done.is_set()
            if session._cancel.is_set():
                session.settle(backend.Outcome(backend.CANCELLED))
            elif case in {"cancel", "relay-failed", "remote-unavailable"}:
                session.settle(backend.Outcome(backend.CANCELLED), marker=marker)
            else:
                observations["submits"].append(
                    session.submit(
                        {"action": "ok", "values": {"q0": 1 if case == "deny" else 0}},
                        marker=marker,
                    )
                )
            return session.outcome

    monkeypatch.setattr(engine, "Remote", SyntheticRemote)
    monkeypatch.setattr(backend, "default_backend", SyntheticBackend)
    result = hook._collect(retained, settings)
    return result, observations


def test_real_sequential_sessions_and_minimal_relay(tmp_path, monkeypatch):
    _, settings = config(tmp_path)
    previous = None
    for cwd in ["/first/project", "/other/project"]:
        retained = request(event(cwd))
        with monkeypatch.context() as patch:
            allowed, seen = collect_case(patch, settings, retained, previous=previous)
        assert allowed is True
        assert seen["submits"] == [True]
        assert len(seen["sessions"]) == len(seen["relays"]) == 1
        if previous:
            assert seen["old_proof_left_pending"] is True
            assert previous is not seen["sessions"][0]
        previous = seen["sessions"][0]
        argv, kwargs = seen["relays"][0]
        sent = json.loads(kwargs["input"])
        assert sent == {
            "schema": "fenric.permission-availability.v1",
            "status": "available",
            "request_id": retained.request_id,
            "native_session_id": "native-session",
            "host_label": "Synthetic host",
            "expires_at_ms": retained.expires_at_ms,
            "url": "https://synthetic.invalid/private",
        }
        assert argv == settings["relay_command"]
        assert kwargs["timeout"] == 10
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    for unavailable in ["desktop", "remote-unavailable", "render-unavailable"]:
        with monkeypatch.context() as patch:
            with pytest.raises(ValueError):
                collect_case(patch, settings, request(event()), case=unavailable)
    for case in ["deny", "cancel", "drift", "relay-failed", "relay-timeout"]:
        with monkeypatch.context() as patch:
            allowed, seen = collect_case(patch, settings, request(event()), case=case)
        assert allowed is False
        assert len(seen["relays"]) == 1


def test_immutable_deadlines_and_precollection_unavailability(tmp_path, monkeypatch):
    _, settings = config(tmp_path)
    retained = hook._request(event(), (1_000_000, 50.0))
    for wall, monotonic, positive in [(1001, 51, True), (1900, 51, False), (1001, 950, False)]:
        with monkeypatch.context() as patch:
            patch.setattr(hook.time, "time", lambda: wall)
            patch.setattr(hook.time, "monotonic", lambda: monotonic)
            assert (retained.remaining() > 0) is positive
    for elapsed, expected in [(650, True), (900, False), (901, False)]:
        with monkeypatch.context() as patch:
            clock = [1000.0, 50.0]
            patch.setattr(hook.time, "time", lambda: clock[0])
            patch.setattr(hook.time, "monotonic", lambda: clock[1])

            def advance():
                clock[:] = [1000.0 + elapsed, 50.0 + elapsed]

            allowed, seen = collect_case(patch, settings, retained, advance=advance)
            if expected:
                assert seen["submits"] == [True]
            assert allowed is expected
    with monkeypatch.context() as patch:
        parent = [111]
        patch.setattr(hook.os, "getppid", lambda: parent[0])
        allowed, _ = collect_case(
            patch, settings, request(event()), advance=lambda: parent.__setitem__(0, 222)
        )
        assert allowed is False
    with monkeypatch.context() as patch:
        short = request(event())
        patch.setattr(hook._Request, "remaining", lambda self: 599.0)
        seen = {"sessions": [], "relays": [], "submits": []}
        with pytest.raises(ValueError):
            collect_case(patch, settings, short, observations=seen)
        assert seen["relays"] == []
    with monkeypatch.context() as patch:
        patch.setattr(
            remote, "read_config", lambda: {"enabled": True, "announce": ["already-configured"]}
        )
        with pytest.raises(ValueError):
            hook._collect(request(event()), settings)
    for config_value in [None, {"enabled": False}]:
        with monkeypatch.context() as patch:
            patch.setattr(remote, "read_config", lambda: config_value)
            with pytest.raises(ValueError):
                hook._collect(request(event()), settings)
    with monkeypatch.context() as patch:

        def broken_config():
            raise remote.RemoteError("synthetic private detail")

        patch.setattr(remote, "read_config", broken_config)
        with pytest.raises(ValueError):
            hook._collect(request(event()), settings)


def test_native_process_boundary_and_output(tmp_path, monkeypatch, capsys):
    filename, _ = config(tmp_path)
    helper = Path(hook.__file__).resolve()
    for invocation in [[str(helper)], ["-m", "fenric.claude_permission"]]:
        run = subprocess.run(
            [sys.executable, "-I", *invocation, "--help"], capture_output=True, text=True, timeout=5
        )
        assert run.returncode == 0 and run.stdout.startswith("usage:") and run.stderr == ""
    for raw in [
        '{"hook_event_name":"Notification"}',
        "{broken",
        '{"hook_event_name":"PermissionRequest","tool_name":"Write"}',
        json.dumps(
            {"hook_event_name": "PermissionRequest", "tool_input": {"command": "x" * 65536}}
        ),
    ]:
        run = subprocess.run(
            [sys.executable, "-I", str(helper), str(filename)],
            input=raw,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert run.returncode == (4 if raw == "{broken" else 0)
        assert run.stdout == ""
        assert "original" not in run.stderr
    for allow in [True, False]:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", [str(helper), str(filename)])
            patch.setattr(
                sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(event()).encode()))
            )
            patch.setattr(hook, "_collect", lambda *args: allow)
            assert hook.main() == 0
            captured = capsys.readouterr()
            assert captured.err == ""
            decision = json.loads(captured.out)
            assert decision == {
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
            assert captured.out.count("\n") == 1
    for error in [hook._Unavailable, RuntimeError]:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", [str(helper), str(filename)])
            patch.setattr(
                sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(event()).encode()))
            )

            def broken(*args):
                raise error("synthetic private details")

            patch.setattr(hook, "_collect", broken)
            assert hook.main() == 0
            captured = capsys.readouterr()
            if error is hook._Unavailable:
                assert captured.out == ""
                assert (
                    captured.err
                    == "Fenric approval unavailable; native permission controls apply.\n"
                )
            else:
                assert json.loads(captured.out)["hookSpecificOutput"]["decision"] == {
                    "behavior": "deny",
                    "message": "Permission declined, cancelled or unavailable",
                    "interrupt": True,
                }
                assert captured.err == ""
            assert "synthetic private details" not in captured.out + captured.err
