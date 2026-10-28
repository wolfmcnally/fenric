"""Synthetic host/callback proof; no native approval, coordinator or GUI is contacted."""

import copy
import datetime
import io
import json
import os
import socket
import subprocess
import threading
import uuid
from types import SimpleNamespace

import pytest

from fenric import backend, engine, host
from fenric.native_permission import collect, request
from fenric.protocol import canonical

from .support import cancels, expires, submits


def profile():
    return {
        "schema": "fenric.native-permission.v1",
        "host": socket.gethostname(),
        "uid": os.getuid(),
        "project": "example",
        "cwd": "/example",
        "session_id": None,
        "participant": None,
        "principal_policy": "owner-policy-v1",
        "folded_present": False,
        "coordinator": None,
        "expected_remote_login": None,
    }


def event():
    return {
        "hook_event_name": "PermissionRequest",
        "permission_mode": "default",
        "tool_name": "Bash",
        "session_id": "original-native-session",
        "cwd": "/example",
        "tool_input": {"command": "printf synthetic", "description": "Test only"},
        "optional_native_field": None,
    }


def test_local_direct_binding_no_folded_and_replay(monkeypatch):
    monkeypatch.setattr("fenric.remote._identity", lambda *_: pytest.fail("remote lookup"))
    config, original = profile(), event()
    message = request(original, config)
    retained = copy.deepcopy(message)
    assert message["context"]["expires_at_ms"] - message["context"]["issued_at_ms"] == 540000
    assert message["context"]["participant"] is None
    assert all(
        v is None
        for k, v in message["context"]["native"].items()
        if k not in {"harness", "route", "method"}
    )
    caller = host.Host()
    response = caller.collect(message, backend=submits(q0=0))
    assert response["collection"] == "answered" and response["answer"] == "allow"
    assert response["decision_digest"] == message["decision_digest"]
    assert response["transport"] == message["transport"]
    assert response["observation"]["authentication_basis"] == "local_process_uid"
    assert response["observation"]["observed_subject"] == str(os.getuid())
    assert message == retained and message["context"]["original"] == original
    assert caller.collect(message, backend=submits(q0=0)) is None


def test_explicit_native_denials_and_unsupported_requests():
    for fake in (submits(q0=1), cancels(), expires()):
        decision, _ = collect(event(), profile(), backend=fake)
        assert decision["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    for key, value in (
        ("permission_mode", "auto"),
        ("session_id", "wrong"),
        ("tool_name", "Write"),
        ("tool_use_id", "fabricated"),
    ):
        original = event()
        original[key] = value
        config = profile()
        config["session_id"] = "original-native-session"
        decision, response = collect(original, config, backend=submits(q0=0))
        assert response is None and decision["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_wrong_action_principal_session_generation_and_expiry(monkeypatch):
    for field in ("action", "principal", "session", "generation", "expired"):
        message = request(event(), profile(), lifetime_ms=1000)
        if field == "action":
            message["context"]["original"]["tool_input"]["command"] = "changed"
        if field == "principal":
            message["context"]["expected_observations"][0]["observed_subject"] = "wrong"
        if field == "session":
            message["context"]["session"] = "wrong"
        if field == "expired":
            message["context"]["expires_at_ms"] = 0
        with monkeypatch.context() as patch:
            read = host.read_frame

            def altered(peer):
                frame = read(peer)
                if field == "generation" and frame["type"] == "response":
                    frame["response"]["transport"]["host_generation"] = "wrong"
                return frame

            patch.setattr(host, "read_frame", altered)
            assert host.Host().collect(message, backend=submits(q0=0)) is None


def test_verified_coordinator_url_only_direct_response_and_failures(monkeypatch):
    for case in (
        "allow",
        "deny",
        "no-coordinator",
        "wrong-coordinator",
        "timeout",
        "send-failure",
        "assignment-revoked",
        "wrong-account",
        "no-remote",
    ):
        with monkeypatch.context() as patch:
            config = profile()
            config.update(
                folded_present=True,
                session_id="original-native-session",
                participant="a" * 64,
                expected_remote_login="owner@example.test",
            )
            scope = {
                "launcher": "b" * 64,
                "owner": "c" * 64,
                "project": "example",
                "harness": "claude",
                "operations": ["coordinate"],
            }
            config["coordinator"] = {
                "fingerprint": "d" * 64,
                "folded": "/synthetic/folded",
                "designation": "e" * 32,
                "nexus": "f" * 64,
                "scope": scope,
            }
            message = request(event(), config)
            coordinator = host.Coordinator(
                **config["coordinator"],
                binding=(
                    config["host"],
                    config["session_id"],
                    message["transport"]["host_generation"],
                ),
            )
            delivered = threading.Event()
            calls = []
            verifications = []

            def command(argv, **kwargs):
                calls.append((argv, kwargs))
                assert 0 < kwargs["timeout"] <= 5
                if argv[1] == "coordinator":
                    verifications.append(True)
                    if case == "timeout":
                        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                    return SimpleNamespace(
                        returncode=0,
                        stdout=canonical(
                            {
                                "authorized": not (
                                    case == "assignment-revoked" and len(verifications) > 2
                                ),
                                "actor": "x" * 64 if case == "wrong-coordinator" else "d" * 64,
                                "designation": "e" * 32,
                                "nexus": "f" * 64,
                                "scope": scope,
                                "expires": (
                                    datetime.datetime.now(datetime.UTC)
                                    + datetime.timedelta(hours=1)
                                ).isoformat(),
                            }
                        ),
                    )
                assert argv[1] == "send" and argv[argv.index("--to") + 1] == "d" * 64
                assert argv[argv.index("--current-session") + 1] == "original-native-session"
                value = json.loads(kwargs["input"])
                assert value["text"] == "https://fixture.test/private"
                assert "command" not in value["data"]
                assert kwargs["stdout"] == subprocess.DEVNULL
                delivered.set()
                return SimpleNamespace(
                    returncode=1 if case == "send-failure" else 0, stdout=b'{"answer":"allow"}'
                )

            patch.setattr(host.subprocess, "run", command)

            class Remote:
                def __init__(self, page, session, callback=None, **kw):
                    self.callback = callback

                def start(self):
                    self.callback(
                        {"status": "unavailable", "reason": "missing", "url": None}
                        if case == "no-remote"
                        else {"status": "available", "url": "https://fixture.test/private"}
                    )

                def close(self):
                    pass

            patch.setattr(engine, "Remote", Remote)

            class Renderer:
                def show(self, page, session):
                    delivered.wait(0.2)
                    if case in {"send-failure", "no-remote"}:
                        session.invalidate("error")
                    else:
                        accepted = session.submit(
                            {"action": "ok", "values": {"q0": 1 if case == "deny" else 0}},
                            marker=str(uuid.uuid4()),
                            surface="remote",
                            subject="foreign" if case == "wrong-account" else "owner@example.test",
                        )
                        if not accepted:
                            session.invalidate("error")
                    return session.outcome

            response = host.Host().collect(
                message,
                coordinator=None if case == "no-coordinator" else coordinator,
                folded_present=True,
                backend=Renderer(),
            )
            if case in {"allow", "deny"}:
                assert response["answer"] == case
                assert response["observation"]["authentication_basis"] == "tailscale_user_login"
                assert response["observation"]["observed_subject"] == "owner@example.test"
            else:
                assert response is None or response["collection"] != "answered"


def test_synchronous_entrypoint_explicit_json_and_policy_drift(tmp_path, monkeypatch, capsys):
    import sys

    from fenric import native_permission as native

    filename = tmp_path / "owner.json"
    for case in ("allow", "deny", "cancel", "bad-config", "policy-drift", "auto"):
        settings = profile()
        filename.write_text(json.dumps(settings))
        filename.chmod(0o644 if case == "bad-config" else 0o600)
        original = event()
        if case == "auto":
            original["permission_mode"] = "auto"
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", ["native_permission", str(filename)])
            patch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(canonical(original))))
            patch.setattr(
                backend,
                "default_backend",
                lambda: cancels() if case == "cancel" else submits(q0=1 if case == "deny" else 0),
            )
            if case == "policy-drift":

                def change_policy(*args, **kwargs):
                    settings["principal_policy"] = "revoked"
                    filename.write_text(json.dumps(settings))
                    return native._decision(True), None

                patch.setattr(native, "collect", change_policy)
            assert native.main() == 0
            streams = capsys.readouterr()
            assert streams.err == ""
            output = json.loads(streams.out)
            assert output["hookSpecificOutput"]["decision"]["behavior"] == (
                "allow" if case == "allow" else "deny"
            )
            assert "updatedInput" not in streams.out and "permissionUpdates" not in streams.out


def test_callback_deadline_cancel_and_supersession_are_nonapprovals():
    import time

    pending = request(event(), profile(), lifetime_ms=1)
    time.sleep(0.005)
    assert host.Host().collect(pending, backend=submits(q0=0)) is None
    cancelled = threading.Event()
    cancelled.set()
    assert (
        host.Host().collect(request(event(), profile()), backend=submits(q0=0), cancel=cancelled)
        is None
    )
    message = request(event(), profile())

    class Superseded:
        def show(self, page, session):
            session.invalidate("superseded")
            assert not session.submit({"action": "ok", "values": {"q0": 0}})
            return session.outcome

    response = host.Host().collect(message, backend=Superseded())
    assert response is None or response["collection"] != "answered"
