"""Private socket/engine integration with synthetic values, never a live GUI."""

from __future__ import annotations

import copy
import datetime
import hashlib
import html
import http.client
import json
import os
import queue
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from fenric import remote
from fenric.backend import CANCELLED, SUBMITTED, Outcome
from fenric.cli import main
from fenric.interaction import run_integrated
from fenric.mappings import prepare_request, project_answer
from fenric.protocol import ProtocolError, canonical, read_frame, write_frame
from fenric.remote import Transport

from .support import ScriptedBackend, cancels, returns
from .test_remote import tailnet as tailnet

VECTORS = Path(__file__).parent / "fixtures/operator-response/golden.json"
SENTINEL = "SYNTHETIC_NOT_A_CREDENTIAL_7b32"


@pytest.fixture(autouse=True)
def isolated_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENRIC_DESKTOP_ONLY", "1")
    monkeypatch.delenv("FENRIC_HEADLESS", raising=False)
    monkeypatch.setattr("fenric.interaction.ACK_SECONDS", 0.2)
    monkeypatch.setattr("fenric.backend.POLL_SECONDS", 0.005)


def _rehash(message: dict) -> dict:
    raw = json.dumps(message["context"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message["decision_digest"] = hashlib.sha256(raw.encode()).hexdigest()
    return message


def _request(secret: Path | None = None, *, ttl_ms: int = 30_000) -> dict:
    vectors = json.loads(VECTORS.read_bytes())
    name = "secret_request" if secret is not None else "request"
    message = copy.deepcopy(next(row["value"] for row in vectors if row["name"] == name))
    context = message["context"]
    now = int(time.time() * 1000)
    context.update(host=socket.gethostname(), issued_at_ms=now - 10, expires_at_ms=now + ttl_ms)
    context["expected_observations"] = [
        {
            "surface": "local",
            "observed_subject": str(os.getuid()),
            "authentication_basis": "local_process_uid",
            "node": socket.gethostname(),
            "session": None,
        }
    ]
    if secret is not None:
        spec = {
            "verb": "secret",
            "message": "Synthetic fixture",
            "fields": [{"name": "TOKEN", "label": "Fixture token"}],
            "into": str(secret),
        }
        context["original"] = {"spec": spec}
        context["presentation"]["spec"] = copy.deepcopy(spec)
        context["destination"] = {
            "kind": "file",
            "host": socket.gethostname(),
            "resolved_path": str(secret.resolve()),
            "format": "dotenv",
            "fields": ["TOKEN"],
        }
    return _rehash(message)


def _acknowledge(
    peer: socket.socket, message: dict, response: dict, *, wrong: bool = False
) -> None:
    common = {
        "interaction_id": response["interaction_id"],
        "decision_digest": response["decision_digest"],
        "transport": copy.deepcopy(response["transport"]),
        "settlement_id": response["settlement_id"],
    }
    if wrong:
        common["transport"]["host_generation"] = "wrong-generation"
    write_frame(peer, {"type": "worker_receipt", **common, "outcome": "received"})
    secret = message["context"]["kind"] == "secret"
    answered = response["collection"] == "answered"
    write_frame(
        peer,
        {
            "type": "delivery_disposition",
            **common,
            "native": message["context"]["native"],
            "outcome": "refused" if secret or not answered else "delivered",
            "evidence": "not-submitted" if secret or not answered else "callback-return",
        },
    )


def _exchange(
    message: dict,
    backend: Any,
    *,
    ack: str = "normal",
    on_available: Any = None,
    on_request: Any = None,
    runner: Any = None,
):
    renderer, peer = socket.socketpair()
    frames = []
    errors: queue.Queue = queue.Queue()

    def host() -> None:
        try:
            write_frame(peer, message)
            if on_request is not None:
                on_request(peer)
            while True:
                frame = read_frame(peer, deadline=time.monotonic() + 3)
                frames.append(frame)
                if frame["type"] == "availability" and on_available is not None:
                    on_available(peer, frame)
                if frame["type"] == "response":
                    if ack == "lost":
                        peer.shutdown(socket.SHUT_RDWR)
                        return
                    if ack != "late":
                        _acknowledge(peer, message, frame["response"], wrong=ack == "wrong")
                if frame["type"] == "terminal":
                    if ack == "late":
                        response = next(
                            item["response"] for item in frames if item["type"] == "response"
                        )
                        _acknowledge(peer, message, response)
                        if on_available is not None:
                            on_available(peer, frame)
                    return
        except ProtocolError:
            # An invalid request or wrong reply closes the one-shot endpoint.
            if not frames and ack != "refused":
                errors.put(AssertionError("Unexpected close before any frame"))
        except BaseException as exc:
            errors.put(exc)
        finally:
            peer.close()

    thread = threading.Thread(target=host, daemon=True)
    thread.start()
    try:
        result = run_integrated(renderer, backend=backend) if runner is None else runner(renderer)
    finally:
        renderer.close()
        thread.join(4)
        if thread.is_alive():
            peer.close()
            thread.join(3)
        assert not thread.is_alive(), "Disposable adapter did not terminate"
    if not errors.empty():
        raise errors.get()
    return result, frames


def _refresh_command(message: dict) -> dict:
    context = message["context"]
    params = context["original"]["params"]
    choices = context["choices"]
    # The static native producer supplies an independent exact presentation oracle.
    spec = context["presentation"]["spec"]
    producers = json.loads(VECTORS.with_name("native-producers.json").read_bytes())
    fixture = next(
        row
        for row in producers
        if row["request"]["context"]["native"]["route"] == "codex.command"
        and (
            row["request"]["context"]["original"]["params"].get("proposedExecpolicyAmendment")
            is not None
        )
        == (
            params.get("proposedExecpolicyAmendment") is not None
            or any(
                type(choice) is dict or choice == "acceptForSession"
                for choice in (params.get("availableDecisions") or [])
            )
        )
    )
    template = fixture["request"]["context"]["presentation"]["spec"]["message"]
    for key, label, fixture_value in (
        ("reason", "Reason supplied by Codex", "Disposable example only"),
        ("environmentId", "Execution environment", "synthetic-environment"),
    ):
        if params.get(key) is None:
            template = template.replace(label + ":\n" + fixture_value + "\n", "")
        else:
            template = template.replace(label + ":\n" + fixture_value, label + ":\n" + params[key])
    spec["message"] = template
    labels = {"accept": "Approve once", "decline": "Deny", "cancel": "Cancel this turn"}
    details = {
        "accept": "Approve this action once.",
        "decline": "Deny this action; continue the turn.",
        "cancel": "Deny this action and interrupt the turn.",
    }
    spec["questions"][0]["options"] = [
        {"id": c, "label": labels[c], "detail": details[c]} for c in choices
    ]
    context["response_schema"] = {"type": "string", "enum": list(choices)}
    return _rehash(message)


def _command_variants(message: dict) -> None:
    minimal = {
        "threadId",
        "turnId",
        "itemId",
        "startedAtMs",
        "command",
        "cwd",
        "availableDecisions",
    }
    actions = [
        {"type": "read", "command": "cat example", "path": "example", "name": "example"},
        {"type": "listFiles", "command": "ls", "path": None},
        {"type": "listFiles", "command": "ls"},
        {"type": "search", "command": "rg example", "path": None, "query": "example"},
        {"type": "search", "command": "rg example"},
        {"type": "unknown", "command": "/usr/bin/true"},
    ]
    for offered in (["cancel", "accept", "decline"], ["decline", "cancel"], ["accept"], ["cancel"]):
        for variant in ("provided", "omitted", "empty", "whitespace"):
            current = copy.deepcopy(message)
            context = current["context"]
            params = context["original"]["params"]
            if variant == "provided":
                context["original"]["jsonrpc"] = "2.0"
                context["original"]["trace"] = {
                    "traceparent": "synthetic-trace",
                    "tracestate": None,
                }
            elif variant == "empty":
                context["original"]["trace"] = None
            elif variant == "whitespace":
                context["original"]["trace"] = {}
            if variant == "omitted":
                for key in set(params) - minimal:
                    del params[key]
            else:
                params["commandActions"] = copy.deepcopy(actions)
                params["proposedNetworkPolicyAmendments"] = []
                if variant in {"empty", "whitespace"}:
                    params["reason"] = params["environmentId"] = "" if variant == "empty" else "  "
            params["availableDecisions"] = list(offered)
            context["choices"] = list(offered)
            _refresh_command(current)
            original = copy.deepcopy(context["original"])
            before = copy.deepcopy(current)
            renderer = returns({"action": "ok", "values": {"q0": 0}})
            result, frames = _exchange(current, renderer)
            assert result.collection == "answered" and result.response["answer"] == offered[0]
            assert result.response["decision_digest"] == before["decision_digest"]
            assert current == before and current["context"]["original"] == original
            page = renderer.pages[0].html
            assert 'class="interaction-technical"' in page
            expiry = datetime.datetime.fromtimestamp(context["expires_at_ms"] / 1000, datetime.UTC)
            expected_expiry = expiry.strftime("%Y-%m-%d %H:%M:%S UTC")
            assert f"Expires:</strong> {expected_expiry}" in page
            assert f"Receiving host:</strong> {html.escape(socket.gethostname())}" in page
            assert page.index("Expires:</strong>") < page.index('class="interaction-technical"')
            assert "Approve allows this exact command once" in page
            assert "Complete original request" in page and "startedAtMs" in page
            encoded = json.dumps(
                original, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            assert html.escape(encoded, quote=True) in page
            alternative = copy.deepcopy(current)
            alternative["context"]["original"]["params"]["reason"] = "Different reason"
            _rehash(alternative)
            assert alternative["decision_digest"] != current["decision_digest"]
            assert result.worker_receipt == "received" and result.native_delivery == "delivered"
            assert [f["type"] for f in frames].count("response") == 1
    # Realistic full native alternatives remain in digest and technical details,
    # but cannot become selected authority through a forged result or schema.

    amendment = {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["true"]}}
    for offered, expected in (
        (["cancel", amendment, "acceptForSession", "accept"], ["cancel", "accept"]),
        (["accept", "acceptForSession"], ["accept"]),
        ([amendment, "decline"], ["decline"]),
    ):
        for proposal in (None, ["true"], ["true", "argument"]):
            current = copy.deepcopy(message)
            context = current["context"]
            params = context["original"]["params"]
            params["availableDecisions"] = copy.deepcopy(offered)
            params["proposedExecpolicyAmendment"] = proposal
            context["choices"] = list(expected)
            _refresh_command(current)
            before = copy.deepcopy(current)
            renderer = returns({"action": "ok", "values": {"q0": 0}})
            result, _ = _exchange(current, renderer)
            assert result.collection == "answered"
            assert result.response["answer"] == context["choices"][0]
            assert current == before
            page = renderer.pages[0].html
            assert "cannot be selected here" in page
            original = json.dumps(context["original"], sort_keys=True, separators=(",", ":"))
            assert html.escape(original, quote=True) in page
            for broad in ("acceptForSession", amendment):
                with pytest.raises(ProtocolError):
                    project_answer(context, {"decision": broad})
            for key, replacement in (
                ("proposedExecpolicyAmendment", ["different-rule"]),
                ("availableDecisions", ["accept", "acceptForSession"]),
            ):
                changed = copy.deepcopy(current)
                changed["context"]["original"]["params"][key] = replacement
                if changed["context"]["original"] != context["original"]:
                    assert _rehash(changed)["decision_digest"] != current["decision_digest"]
    for narrowed in (["accept"], ["accept", "cancel"]):
        current = copy.deepcopy(message)
        params = current["context"]["original"]["params"]
        params["availableDecisions"] = ["cancel", amendment, "acceptForSession", "accept"]
        current["context"]["choices"] = narrowed
        _refresh_command(current)
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(current, renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, narrowed
    for bad in (
        ["accept", amendment, copy.deepcopy(amendment)],
        [amendment, "acceptForSession"],
        ["accept", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": [""]}}],
        ["accept", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": [False]}}],
        ["accept", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["x"], "extra": 1}}],
        ["accept", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["x"]}, "extra": 1}],
        ["accept", {"acceptWithExecpolicyAmendment": None}],
        ["accept", False],
    ):
        current = copy.deepcopy(message)
        current["context"]["original"]["params"]["availableDecisions"] = bad
        current["context"]["choices"] = ["accept"]
        _refresh_command(current)
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(current, renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, bad
    alterations = [
        ("additionalPermissions", {}),
        ("additionalPermissions", False),
        ("networkApprovalContext", {}),
        ("networkApprovalContext", False),
        ("proposedExecpolicyAmendment", []),
        ("proposedExecpolicyAmendment", [""]),
        ("proposedExecpolicyAmendment", [False]),
        ("proposedExecpolicyAmendment", "rule"),
        ("proposedNetworkPolicyAmendments", False),
        ("proposedNetworkPolicyAmendments", ""),
        ("proposedNetworkPolicyAmendments", [{"action": "allow", "host": "example.test"}]),
        ("approvalId", "subcommand"),
        ("kind", None),
        ("kind", "writeStdin"),
        ("availableDecisions", None),
        ("availableDecisions", []),
        ("availableDecisions", ["accept", "accept"]),
        ("availableDecisions", ["acceptForSession"]),
        ("availableDecisions", ["accept", "acceptForSession", "acceptForSession"]),
        (
            "availableDecisions",
            ["accept", {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": []}}],
        ),
        (
            "availableDecisions",
            [
                "accept",
                {
                    "applyNetworkPolicyAmendment": {
                        "network_policy_amendment": {"action": "allow", "host": "example.test"}
                    }
                },
            ],
        ),
        ("availableDecisions", ["accept", "invented"]),
        ("skillMetadata", None),
        ("startedAtMs", True),
        ("commandActions", [{}]),
        ("commandActions", [{"type": "unknown", "command": "true", "extra": None}]),
        ("commandActions", [{"type": "read", "command": "cat x", "path": "x"}]),
        ("commandActions", [{"type": "listFiles", "command": "ls", "path": False}]),
        ("commandActions", [{"type": "search", "command": "rg", "query": 1}]),
    ]
    for field, value in alterations:
        current = copy.deepcopy(message)
        current["context"]["original"]["params"][field] = value
        if field == "availableDecisions":
            # A host must not hide duplicates or unsupported native choices by
            # supplying a smaller, otherwise valid one-action presentation.
            current["context"]["choices"] = ["accept"]
            _refresh_command(current)
        _rehash(current)
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(current, renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, (field, value)
    for field, value in (
        ("jsonrpc", None),
        ("jsonrpc", "1.0"),
        ("jsonrpc", False),
        ("extra", None),
    ):
        current = copy.deepcopy(message)
        current["context"]["original"][field] = value
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(_rehash(current), renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, field
    for trace in (False, "", [], {"traceparent": False}, {"tracestate": 1}, {"extra": None}):
        current = copy.deepcopy(message)
        current["context"]["original"]["trace"] = trace
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(_rehash(current), renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, trace
    for field, value in (("approval_id", "subcommand"), ("started_at_ms", 2000), ("request_id", 2)):
        current = copy.deepcopy(message)
        current["context"]["native"][field] = value
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(_rehash(current), renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, field
    for field in (
        "availableDecisions",
        "threadId",
        "turnId",
        "itemId",
        "startedAtMs",
        "command",
        "cwd",
    ):
        current = copy.deepcopy(message)
        del current["context"]["original"]["params"][field]
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        refused, _ = _exchange(_rehash(current), renderer, ack="refused")
        assert refused.collection == "error" and not renderer.pages, field


def _mcp_variants(message: dict) -> None:

    context = message["context"]
    original_bytes = canonical(context["original"])
    prepared = prepare_request(message, now_ms=context["issued_at_ms"])
    assert canonical(prepared.context["original"]) == original_bytes
    identifier = context["original"]["rpc"]["params"]["questions"][0]["id"]
    for answer in ("Allow for this session", "Allow and don't ask me again", "unknown"):
        with pytest.raises(ProtocolError):
            project_answer(context, {identifier: answer})
    for alteration in (
        "action-join",
        "action-type",
        "action-status",
        "action-unknown",
        "action-result",
        "action-error",
        "action-duration",
        "plugin",
        "readonly",
        "app-unknown",
        "app-required",
        "ui-unknown",
        "ui-mode",
        "rpc-unknown",
        "trace",
        "version",
        "param-unknown",
        "autoresolution",
        "header",
        "question-join",
        "question-unknown",
        "persistent",
        "reorder",
        "duplicate-option",
        "option-description",
        "option-unknown",
        "args-presentation",
        "server-presentation",
        "tool-presentation",
        "offered-schema",
    ):
        changed = copy.deepcopy(message)
        current = changed["context"]
        original = current["original"]
        action, rpc = original["action"], original["rpc"]
        params = rpc["params"]
        question = params["questions"][0]
        if alteration == "action-join":
            action["id"] = "other-item"
            question["id"] = "mcp_tool_call_approval_other-item"
            changed_id = question["id"]
            current["presentation"]["spec"]["questions"][0]["id"] = changed_id
            current["response_schema"]["properties"] = {
                changed_id: {"type": "string", "enum": ["Allow", "Cancel"]}
            }
            current["response_schema"]["required"] = [changed_id]
        elif alteration.startswith("action-"):
            key = alteration.removeprefix("action-")
            action[{"duration": "durationMs"}.get(key, key)] = {
                "type": "commandExecution",
                "status": "completed",
                "unknown": None,
                "result": {},
                "error": {},
                "duration": 0,
            }[key]
        elif alteration == "plugin":
            action["pluginId"] = False
        elif alteration == "readonly":
            action["readOnlyHint"] = 0
        elif alteration.startswith("app-"):
            action["appContext"] = (
                {"connectorId": "sample", "unknown": None} if alteration == "app-unknown" else {}
            )
        elif alteration.startswith("ui-"):
            action["mcpAppUi"] = {"resourceUri": "ui://inert", "preferredModelDisplayMode": "other"}
            if alteration == "ui-unknown":
                action["mcpAppUi"] = {
                    "resourceUri": "ui://inert",
                    "preferredModelDisplayMode": "inline",
                    "unknown": None,
                }
        elif alteration == "rpc-unknown":
            rpc["unknown"] = None
        elif alteration == "trace":
            rpc["trace"] = {"traceparent": 7}
        elif alteration == "version":
            rpc["jsonrpc"] = None
        elif alteration == "param-unknown":
            params["unknown"] = None
        elif alteration == "autoresolution":
            params["autoResolutionMs"] = 0
        elif alteration == "header":
            question["header"] = "Other"
        elif alteration == "question-join":
            question["id"] = "wrong"
        elif alteration == "question-unknown":
            question["unknown"] = None
        elif alteration == "persistent":
            question["options"].insert(
                1,
                {
                    "label": "Allow and don't ask me again",
                    "description": "Run the tool and remember this choice for future tool calls.",
                },
            )
        elif alteration == "reorder":
            question["options"].reverse()
        elif alteration == "duplicate-option":
            question["options"].append(copy.deepcopy(question["options"][0]))
        elif alteration == "option-description":
            question["options"][0]["description"] = "Other"
        elif alteration == "option-unknown":
            question["options"][0]["unknown"] = None
        elif alteration == "offered-schema":
            current["response_schema"]["properties"][identifier]["enum"].append(
                "Allow for this session"
            )
        else:
            action[
                {
                    "args-presentation": "arguments",
                    "server-presentation": "server",
                    "tool-presentation": "tool",
                }[alteration]
            ] = "different"
        _rehash(changed)
        renderer = returns({"action": "ok", "values": {"q0": 0}})
        result, _ = _exchange(changed, renderer, ack="refused")
        assert result.collection == "error" and not renderer.pages, alteration
        assert changed["decision_digest"] != message["decision_digest"]

    for args in (None, {"nested": [None, {"safe": -(2**53 - 1)}]}):
        changed = copy.deepcopy(message)
        current = changed["context"]
        current["original"]["action"]["arguments"] = args
        suffix = ""
        if len(current["original"]["rpc"]["params"]["questions"][0]["options"]) == 3:
            suffix = (
                "\nThe session-wide alternative is retained in the original "
                "but cannot be selected here."
            )
        current["presentation"]["spec"]["message"] = (
            "Allow exactly this pending tool call with unchanged arguments, or cancel it. "
            "This grants no future permissions.\nUntrusted action data:\n"
            + json.dumps(
                {"server": "sample-server", "tool": "sample-tool", "arguments": args},
                sort_keys=True,
                separators=(",", ":"),
            )
            + suffix
        )
        _rehash(changed)
        result, _ = _exchange(changed, returns({"action": "ok", "values": {"q0": 0}}))
        assert result.collection == "answered"
        assert result.response["answer"] == {identifier: "Allow"}
    for bad in (1.0, 2**53):
        changed = copy.deepcopy(message)
        changed["context"]["original"]["action"]["arguments"] = bad
        with pytest.raises(ProtocolError):
            prepare_request(_rehash(changed), now_ms=context["issued_at_ms"])


def test_private_question_exchange(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER", "not-the-observed-principal")
    message = _request()
    backend = returns({"action": "ok", "values": {"q0": 1}})
    result, frames = _exchange(message, backend)
    assert result.collection == "answered"
    assert result.response["answer"] == {"Choose an option": "B"}
    assert result.worker_receipt == "received"
    assert result.native_delivery == "delivered" and result.native_evidence == "callback-return"
    observed = result.response["observation"]
    assert observed["observed_subject"] == str(os.getuid())
    assert observed["node"] == socket.gethostname() and observed["session"] is None
    assert (
        observed["surface"] == "local" and observed["authentication_basis"] == "local_process_uid"
    )
    assert result.response["decision_digest"] == message["decision_digest"]
    assert [frame["type"] for frame in frames].count("response") == 1
    assert frames[-1]["type"] == "terminal"
    assert "Complete original request" in backend.pages[0].html
    assert "Choose an option" in backend.pages[0].html
    assert "data-integrated" in backend.pages[0].html
    producers = json.loads(VECTORS.with_name("native-producers.json").read_bytes())
    for fixture in producers:
        fresh = _request()["context"]
        message = copy.deepcopy(fixture["request"])
        assert message["decision_digest"] == _rehash(copy.deepcopy(message))["decision_digest"]
        context = message["context"]
        for key in ("host", "issued_at_ms", "expires_at_ms", "expected_observations"):
            context[key] = fresh[key]
        if context["native"]["route"] == "codex.command":
            _refresh_command(message)
        _rehash(message)
        options = context["presentation"]["spec"]["questions"][0]["options"]
        for answer in fixture["answers"]:
            selected = next(iter(answer["fenric_value"].values()))
            index = next(i for i, option in enumerate(options) if option["id"] == selected)
            renderer = returns({"action": "ok", "values": {"q0": index}})
            result, frames = _exchange(message, renderer)
            assert result.collection == "answered"
            assert result.response["answer"] == answer["wire_answer"]
            assert result.effect["outcome"] == "not_applicable"
            assert result.worker_receipt == "received"
            assert [frame["type"] for frame in frames].count("response") == 1
            assert "Complete original request" in renderer.pages[0].html
            if context["presentation"]["mapping"] == "fenric.codex-mcp-confirmation.v1":
                assert "&lt;untrusted&gt;" in renderer.pages[0].html
                assert "<untrusted>" not in renderer.pages[0].html
                assert (
                    html.escape(canonical(context["original"]).decode(), quote=True)
                    in renderer.pages[0].html
                )
                for uri in ("https://example.invalid/inert", "ui://inert"):
                    assert not any(
                        attribute + quote + uri in renderer.pages[0].html
                        for attribute in ("href=", "src=")
                        for quote in ("'", '"')
                    )
                if "mcpAppUi" in context["original"]["action"]:
                    assert "ui://inert" in renderer.pages[0].html
                    assert "https://example.invalid/inert" in renderer.pages[0].html
        dismissed, _ = _exchange(message, cancels())
        assert dismissed.collection == "cancelled" and dismissed.response["answer"] is None
        assert dismissed.native_delivery == "refused"

        if context["native"]["route"] == "codex.command":
            _command_variants(message)
        if context["presentation"]["mapping"] == "fenric.codex-mcp-confirmation.v1":
            _mcp_variants(message)
        variants = ["scope", "schema", "native-join", "presentation"]
        if context["native"]["route"] == "codex.command":
            variants += ["session-choice", "write-stdin", "amendment"]
        if context["native"]["route"] == "codex.question":
            variants += ["secret", "other", "nonblocking", "duplicate-question"]
        for alteration in variants:
            changed = copy.deepcopy(message)
            current = changed["context"]
            route = current["native"]["route"]
            if alteration == "scope":
                current["scope"]["category"] = "session"
            elif alteration == "schema":
                current["response_schema"] = {"type": "boolean"}
            elif alteration == "native-join":
                if route == "claude.approval":
                    current["original"]["tool_name"] = "DifferentTool"
                else:
                    rpc = current["original"].get("rpc", current["original"])
                    rpc["params"]["itemId"] = "different-item"
            elif alteration == "presentation":
                current["presentation"]["spec"]["questions"][0]["prompt"] = "Different action"
            elif alteration == "session-choice":
                current["choices"][0] = "acceptForSession"
            elif alteration in {"write-stdin", "amendment"}:
                params = current["original"]["params"]
                if alteration == "write-stdin":
                    params["kind"] = "writeStdin"
                else:
                    params["proposedExecpolicyAmendment"] = [""]
            else:
                original = current["original"]
                if alteration == "nonblocking":
                    original["rpc"]["params"]["isBlocking"] = False
                elif alteration == "duplicate-question":
                    original["rpc"]["params"]["questions"] *= 2
                    if "questions" in original:
                        original["questions"] = original["rpc"]["params"]["questions"]
                else:
                    key = "isSecret" if alteration == "secret" else "isOther"
                    original["rpc"]["params"]["questions"][0][key] = True
                    if "questions" in original:
                        original["questions"] = original["rpc"]["params"]["questions"]
            if route == "claude.approval" and alteration not in {
                "presentation",
                "scope",
                "schema",
                "session-choice",
            }:
                current["presentation"]["spec"]["message"] = (
                    "Untrusted action data below. This grants no future permission.\n"
                    + json.dumps(current["original"], sort_keys=True, separators=(",", ":"))
                )
            renderer = returns({"action": "ok", "values": {"q0": 0}})
            refused, _ = _exchange(_rehash(changed), renderer, ack="refused")
            assert refused.collection == "error" and not renderer.pages, alteration
    assert capsys.readouterr() == ("", "")


def test_admission_and_reservation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for alteration in ("digest", "presentation", "destination"):
        target = tmp_path / (alteration + ".env")
        message = _request(target)
        if alteration == "digest":
            message["decision_digest"] = "0" * 64
        elif alteration == "presentation":
            message["context"]["presentation"]["spec"]["message"] = "Altered question"
            _rehash(message)
        else:
            message["context"]["destination"]["fields"] = ["UNREQUESTED"]
            _rehash(message)
        backend = returns(
            {"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True}
        )
        result, _ = _exchange(message, backend, ack="refused")
        assert result.collection == "error" and not backend.pages and not target.exists()
    target = tmp_path / "once.env"
    sequence = {}

    class InputSequence:
        def show(self, page, session):
            invalid = {"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": False}
            sequence["invalid"] = session.submit(invalid, native=True)
            sequence["invalid_done"] = session.done.is_set()
            sequence["invalid_effect"] = target.exists()
            valid = {**invalid, "destination_confirmed": True}
            sequence["valid"] = session.submit(valid, native=True)
            sequence["first_inode"] = target.stat().st_ino if target.exists() else None
            sequence["duplicate"] = session.submit(valid, native=True)
            sequence["different"] = session.submit(
                {**valid, "values": {"f0": "DIFFERENT_SYNTHETIC"}}, native=True
            )
            sequence["final_inode"] = target.stat().st_ino if target.exists() else None
            return Outcome(SUBMITTED, valid)

    result, _ = _exchange(_request(target), InputSequence())
    assert sequence["invalid"] is False and sequence["invalid_done"] is False
    assert sequence["invalid_effect"] is False and sequence["valid"] is True
    assert sequence["duplicate"] is False and sequence["different"] is False
    assert (
        sequence["first_inode"] is not None and sequence["first_inode"] == sequence["final_inode"]
    )
    assert (
        result.effect["outcome"] == "completed" and target.read_text() == "TOKEN=" + SENTINEL + "\n"
    )
    message = _request(tmp_path / "wrong-actor.env", ttl_ms=50)
    message["context"]["expected_observations"][0]["observed_subject"] = "not-the-process-uid"
    result, _ = _exchange(
        _rehash(message),
        returns({"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True}),
    )
    assert result.collection == "unavailable" and not (tmp_path / "wrong-actor.env").exists()
    assert result.response["observation"] is None
    assert main(["interact", "--channel-fd", "0"]) == 3
    output = capsys.readouterr()
    assert output.out == "" and SENTINEL not in output.err and "invalid invocation" in output.err
    assert run_integrated(object()).collection == "error"

    def cli(channel):
        code = (
            "from fenric.cli import main\nfrom fenric import backend\n"
            "def no_window():\n    raise RuntimeError('native window forbidden in fixture')\n"
            "backend.default_backend=no_window\nraise SystemExit(main())\n"
        )
        return subprocess.run(
            [sys.executable, "-c", code, "interact", "--channel-fd", str(channel.fileno())],
            pass_fds=(channel.fileno(),),
            capture_output=True,
            text=True,
            env=dict(os.environ, FENRIC_HEADLESS="1"),
            timeout=5,
        )

    child, frames = _exchange(_request(), None, runner=cli)
    assert (child.returncode, child.stdout, child.stderr) == (4, "", "")
    response = next(frame["response"] for frame in frames if frame["type"] == "response")
    assert response["collection"] == "unavailable" and response["observation"] is None
    assert not any(
        frame["type"] == "availability" and frame["surface"] == "local" for frame in frames
    )


def test_host_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real destination failure between the two preparation checks must still
    # receive the host's correlated receipt, without ever opening a view.
    from fenric import interaction as integration

    unavailable, _ = _exchange(
        _request(), ScriptedBackend(RuntimeError("synthetic renderer failure"))
    )
    assert unavailable.collection == "unavailable" and unavailable.response["answer"] is None
    assert unavailable.native_delivery == "refused"

    class ProtocolPending:
        def show(self, page, session):
            session.done.wait(1)
            return Outcome(CANCELLED)

    duplicate = _request()
    result, frames = _exchange(
        duplicate, ProtocolPending(), on_request=lambda peer: write_frame(peer, duplicate)
    )
    assert result.collection == "error" and result.response["answer"] is None
    assert [frame["type"] for frame in frames].count("response") == 1

    late_sent = threading.Event()
    original_send = integration.Interaction._send
    terminal_waits = []

    def pause_after_terminal(owner, frame):
        sent = original_send(owner, frame)
        if frame["type"] == "terminal":
            terminal_waits.append(late_sent.wait(1))
            time.sleep(0.03)
        return sent

    with monkeypatch.context() as scoped:
        scoped.setattr(integration.Interaction, "_send", pause_after_terminal)
        result, _ = _exchange(
            _request(),
            returns({"action": "ok", "values": {"q0": 1}}),
            ack="late",
            on_available=lambda peer, frame: (
                late_sent.set() if frame["type"] == "terminal" else None
            ),
        )
    assert terminal_waits == [True]
    assert result.collection == "answered" and result.worker_receipt == "unknown"
    assert result.native_delivery == "unknown"

    target = tmp_path / "changed-before-render.env"
    backend = returns({"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True})
    original_run = integration.run
    preparation = []

    def changed_target(spec, **kwargs):
        target.mkdir()
        preparation.append(target.is_dir())
        return original_run(spec, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(integration, "run", changed_target)
        result, _ = _exchange(_request(target), backend)
    assert preparation == [True] and not backend.pages
    assert result.collection == "error" and result.effect["outcome"] == "not_started"
    assert result.worker_receipt == "received", "Pre-render error lost a real host receipt"

    # Observe the real frame reader while preparation is deliberately pending.
    preparing, read_cancel = threading.Event(), threading.Event()
    observed_during_preparation = []
    message = _request()
    original_read = integration.read_frame

    def observe_read(channel, **kwargs):
        frame = original_read(channel, **kwargs)
        if frame["type"] == "invalidate":
            read_cancel.set()
        return frame

    def prepare_pending(spec, **kwargs):
        preparing.set()
        observed_during_preparation.append(read_cancel.wait(1))
        return original_run(spec, **kwargs)

    def cancel_during_preparation(peer):
        if not preparing.wait(1):
            raise AssertionError("Preparation window was not reached")
        write_frame(
            peer,
            {
                "type": "invalidate",
                "interaction_id": message["context"]["interaction_id"],
                "transport": message["transport"],
                "reason": "cancelled",
            },
        )

    backend = returns({"action": "ok", "values": {"q0": 1}})
    with monkeypatch.context() as scoped:
        scoped.setattr(integration, "run", prepare_pending)
        scoped.setattr(integration, "read_frame", observe_read)
        result, _ = _exchange(message, backend, on_request=cancel_during_preparation)
    assert observed_during_preparation == [True], (
        "Host control reader was inactive during preparation"
    )
    assert not backend.pages and result.collection == "cancelled"

    class Touched:
        def show(self, page, session):
            session.touch()
            assert session.done.wait(1), "Touch extended immutable authority"
            return Outcome(CANCELLED)

    result, _ = _exchange(_request(ttl_ms=50), Touched())
    assert result.collection == "expired" and result.response["answer"] is None
    original_time = time.time

    class ClockMovedBack:
        def show(self, page, session):
            with monkeypatch.context() as scoped:
                scoped.setattr(time, "time", lambda: original_time() - 3600)
                session.touch()
                session.done.wait(1)
            return Outcome(CANCELLED)

    result, _ = _exchange(_request(ttl_ms=50), ClockMovedBack())
    assert result.collection == "expired" and result.response["answer"] is None
    message = _request()
    sent = threading.Event()

    def invalidate(peer, frame):
        if frame["surface"] == "local" and not sent.is_set():
            write_frame(
                peer,
                {
                    "type": "invalidate",
                    "interaction_id": message["context"]["interaction_id"],
                    "transport": message["transport"],
                    "reason": "cancelled",
                },
            )
            sent.set()

    class Pending:
        def show(self, page, session):
            assert session.done.wait(1)
            return Outcome(CANCELLED)

    result, _ = _exchange(message, Pending(), on_available=invalidate)
    assert sent.is_set() and result.collection == "cancelled" and result.response["answer"] is None

    class Reserved:
        def show(self, page, session):
            assert session.submit({"action": "ok", "values": {"q0": 0}}, native=True)
            assert session.invalidate("cancelled") is False
            return Outcome(CANCELLED)

    result, _ = _exchange(_request(), Reserved())
    assert result.collection == "answered" and result.response["answer"] == {
        "Choose an option": "A"
    }
    result, _ = _exchange(_request(), returns({"action": "ok", "values": {"q0": 1}}), ack="wrong")
    assert (
        result.collection == "answered"
        and result.worker_receipt == "unknown"
        and result.native_delivery == "unknown"
    )
    first, second, link = tmp_path / "first.env", tmp_path / "second.env", tmp_path / "link.env"
    first.write_text("KEEP=first\n")
    second.write_text("KEEP=second\n")
    link.symlink_to(first)

    class Retarget:
        def show(self, page, session):
            link.unlink()
            link.symlink_to(second)
            return Outcome(
                SUBMITTED,
                {"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True},
            )

    result, _ = _exchange(_request(link), Retarget())
    assert result.collection == "error" and result.effect["outcome"] == "not_started"
    assert result.effect["detail"] == "validation-refused"
    assert first.read_text() == "KEEP=first\n" and second.read_text() == "KEEP=second\n"


def test_remote_provenance_and_review_only_close(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    message = _request()
    context = message["context"]
    context["allowed_surfaces"] = ["remote"]
    login = "fixture@example.invalid"
    context["expected_observations"] = [
        {
            "surface": "remote",
            "observed_subject": login,
            "authentication_basis": "tailscale_user_login",
            "node": socket.gethostname(),
            "session": None,
        }
    ]
    _rehash(message)
    received, failures = [], []

    class Review:
        def show(self, page, session):
            assert session.review_only and "Review only" in page.html
            assert not session.submit({"action": "ok", "values": {"q0": 0}}, native=True)
            self.transport = Transport(page, session, "fixture.invalid:8443", login)

            def answer():
                try:
                    time.sleep(0.02)
                    headers = {
                        "Host": "fixture.invalid:8443",
                        "X-Forwarded-Host": "fixture.invalid:8443",
                        "X-Forwarded-Proto": "https",
                        "Tailscale-User-Login": login,
                        "Origin": "https://fixture.invalid:8443",
                        "Content-Type": "application/json",
                        "Fenric-Request": str(uuid.uuid4()),
                    }
                    winner = str(uuid.uuid4())
                    loser = str(uuid.uuid4())
                    for attempt in ("forged", "winner", "loser", "retry"):
                        forged = attempt == "forged"
                        headers["Fenric-Request"] = loser if attempt == "loser" else winner
                        payload = {"action": "ok", "values": {"q0": 1}}
                        if forged:
                            payload["observation"] = {"observed_subject": "forged"}
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", self.transport.server.server_port, timeout=1
                        )
                        connection.request(
                            "POST",
                            self.transport.path + "/action",
                            json.dumps({"kind": "submit", "payload": payload}),
                            headers,
                        )
                        response = connection.getresponse()
                        body = response.read()
                        assert response.status == (403 if forged else 200)
                        if not forged:
                            received.append((attempt, json.loads(body)))
                        connection.close()
                except BaseException as exc:
                    failures.append(exc)

            self.thread = threading.Thread(target=answer, daemon=True)
            self.thread.start()
            return Outcome(CANCELLED)

    backend = Review()
    try:
        with monkeypatch.context() as scoped:
            # This proof owns its real authenticated Transport below; the
            # engine's remote provider is deliberately left indeterminate.
            scoped.setattr("fenric.remote.Remote._run", lambda self: None)
            result, frames = _exchange(message, backend)
    finally:
        if hasattr(backend, "thread"):
            backend.thread.join(2)
            assert not backend.thread.is_alive()
        if hasattr(backend, "transport"):
            backend.transport.close()
    assert result.collection == "answered" and result.response["answer"] == {
        "Choose an option": "B"
    }
    assert not failures, failures
    assert [attempt for attempt, _ in received] == ["winner", "loser", "retry"]
    for attempt, shared in received:
        assert shared["status"] == "ok"
        assert shared["settled_elsewhere"] is (attempt == "loser")
        assert "Choose an option" not in json.dumps(shared) and '"B"' not in json.dumps(shared)
    observation = result.response["observation"]
    assert (
        observation["observed_subject"] == login
        and observation["authentication_basis"] == "tailscale_user_login"
    )
    assert observation["surface"] == "remote" and observation["node"] == socket.gethostname()
    assert all(frame.get("protected_capability") is None for frame in frames)
    started = time.monotonic()
    unavailable_message = copy.deepcopy(message)
    fresh_clock = _request(ttl_ms=200)["context"]
    for key in ("issued_at_ms", "expires_at_ms"):
        unavailable_message["context"][key] = fresh_clock[key]
    unavailable, _ = _exchange(_rehash(unavailable_message), cancels())
    assert time.monotonic() - started < 1
    assert unavailable.collection == "unavailable" and unavailable.response["answer"] is None
    assert unavailable.effect["outcome"] == "not_applicable"
    assert capsys.readouterr() == ("", "")


def test_private_capability_availability(
    tailnet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, config = tailnet
    monkeypatch.delenv("FENRIC_DESKTOP_ONLY")
    announcement = tmp_path / "announcement.py"
    leaked = tmp_path / "ordinary-announcement"
    announcement.write_text(
        "import pathlib,sys\npathlib.Path(sys.argv[1]).write_text(sys.stdin.read())\n"
    )
    config["announce"] = [sys.executable, str(announcement), str(leaked)]
    remote._save(config)  # conftest confines this to the disposable configuration directory.
    message = _request()
    message["context"]["allowed_surfaces"] = ["local", "remote"]
    message["context"]["expected_observations"].append(
        {
            "surface": "remote",
            "observed_subject": "operator@example.com",
            "authentication_basis": "tailscale_user_login",
            "node": socket.gethostname(),
            "session": None,
        }
    )
    _rehash(message)
    capabilities = []
    pending = []

    def available(peer, frame):
        if frame["surface"] == "remote" and frame["state"] == "available":
            capabilities.append(frame["protected_capability"])
            # An ordinary announcement would execute during this pending window.
            time.sleep(0.15)
            write_frame(
                peer,
                {
                    "type": "invalidate",
                    "interaction_id": message["context"]["interaction_id"],
                    "transport": message["transport"],
                    "reason": "cancelled",
                },
            )

    class Waiting:
        def show(self, page, session):
            pending.append(session.done.wait(2))
            return Outcome(CANCELLED)

    result, frames = _exchange(message, Waiting(), on_available=available)
    assert pending == [True] and result.collection == "cancelled"
    assert len(capabilities) == 1 and capabilities[0].startswith("https://host.tail123.ts.net:")
    assert capabilities[0] not in repr(result)
    assert not leaked.exists(), "Private capability reached configured ordinary announcement"
    assert not list(directory.glob("8*.json")), "Owned synthetic Serve mapping remained live"
    assert capsys.readouterr() == ("", "")


def test_secret_effect_and_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fenric import engine

    raced = tmp_path / "raced.env"
    message = _request(raced)
    login = "fixture@example.invalid"
    message["context"]["allowed_surfaces"].append("remote")
    message["context"]["expected_observations"].append(
        {
            "surface": "remote",
            "observed_subject": login,
            "authentication_basis": "tailscale_user_login",
            "node": socket.gethostname(),
            "session": None,
        }
    )
    calls, failures, replies = [], [], []
    original_write = engine.write_target

    def count_write(*args, **kwargs):
        calls.append(True)
        return original_write(*args, **kwargs)

    class Race:
        def show(self, page, session):
            transport = Transport(page, session, "fixture.invalid:8443", login)
            barrier = threading.Barrier(2, timeout=1)
            native_payload = {
                "action": "ok",
                "values": {"f0": SENTINEL},
                "destination_confirmed": True,
            }

            def native():
                try:
                    barrier.wait()
                    replies.append(("native", session.submit(native_payload, native=True)))
                except BaseException as exc:
                    failures.append(exc)

            def remote_answer():
                connection = http.client.HTTPConnection(
                    "127.0.0.1", transport.server.server_port, timeout=1
                )
                try:
                    barrier.wait()
                    payload = {**native_payload, "values": {"f0": "OTHER_SYNTHETIC"}}
                    headers = {
                        "Host": "fixture.invalid:8443",
                        "X-Forwarded-Host": "fixture.invalid:8443",
                        "X-Forwarded-Proto": "https",
                        "Tailscale-User-Login": login,
                        "Origin": "https://fixture.invalid:8443",
                        "Content-Type": "application/json",
                        "Fenric-Request": str(uuid.uuid4()),
                    }
                    connection.request(
                        "POST",
                        transport.path + "/action",
                        json.dumps({"kind": "submit", "payload": payload}),
                        headers,
                    )
                    response = connection.getresponse()
                    replies.append(("remote", response.status))
                    response.read()
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    connection.close()

            threads = [
                threading.Thread(target=target, daemon=True) for target in (native, remote_answer)
            ]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(2)
                    if thread.is_alive():
                        failures.append(AssertionError("Disposable race thread did not terminate"))
            finally:
                transport.close()
            return Outcome(CANCELLED)

    with monkeypatch.context() as scoped:
        scoped.setattr(engine, "write_target", count_write)
        result, frames = _exchange(_rehash(message), Race())
    assert not failures and calls == [True]
    assert len(replies) == 2 and ("remote", 200) in replies
    assert result.collection == "answered" and result.effect["outcome"] == "completed"
    surface = result.response["observation"]["surface"]
    assert (
        raced.read_text()
        == "TOKEN=" + (SENTINEL if surface == "local" else "OTHER_SYNTHETIC") + "\n"
    )
    assert [frame["type"] for frame in frames].count("response") == 1
    from fenric import interaction as integration

    failed_return = tmp_path / "completed-before-return-error.env"

    def fail_response(owner, result):
        raise ProtocolError

    with monkeypatch.context() as scoped:
        scoped.setattr(integration.Interaction, "finish", fail_response)
        result, frames = _exchange(
            _request(failed_return),
            returns({"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True}),
        )
    assert failed_return.read_text() == "TOKEN=" + SENTINEL + "\n"
    assert result.effect["outcome"] == "completed" and result.response is None
    assert frames[-1]["type"] == "terminal" and frames[-1]["state"] == "failed"
    assert result.worker_receipt == "unknown" and result.native_delivery == "unknown"
    for ack in ("normal", "lost"):
        target = tmp_path / (ack + ".env")
        message = _request(target)
        backend = returns(
            {"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True}
        )
        result, frames = _exchange(message, backend, ack=ack)
        assert result.collection == "answered" and result.response["answer"] is None
        assert target.read_text() == "TOKEN=" + SENTINEL + "\n"
        assert result.effect["outcome"] == "completed"
        assert result.effect["filled"] == ["TOKEN"] and result.effect["skipped"] == []
        assert result.effect["destination_confirmed"] is True
        assert result.worker_receipt == ("received" if ack == "normal" else "unknown")
        assert result.native_delivery == ("refused" if ack == "normal" else "unknown")
        assert SENTINEL not in repr(result) and SENTINEL not in json.dumps(frames)
        assert SENTINEL not in backend.pages[0].html
    relative = tmp_path / "relative.env"
    message = _request(relative)
    message["context"]["original"]["spec"]["into"] = "relative.env"
    message["context"]["presentation"]["spec"]["into"] = "relative.env"
    with monkeypatch.context() as scoped:
        scoped.chdir(tmp_path)
        result, _ = _exchange(
            _rehash(message),
            returns({"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True}),
        )
    assert (
        result.effect["outcome"] == "completed"
        and relative.read_text() == "TOKEN=" + SENTINEL + "\n"
    )
    for filename, wire_format in (("fixture.json", "flat-json"), ("fixture.token", "single-value")):
        target = tmp_path / filename
        if wire_format == "single-value":
            target.write_text("synthetic-old\n")
        message = _request(target)
        message["context"]["destination"]["format"] = wire_format
        result, _ = _exchange(
            _rehash(message),
            returns({"action": "ok", "values": {"f0": SENTINEL}, "destination_confirmed": True}),
        )
        assert (
            result.effect["outcome"] == "completed"
            and result.effect["destination"]["format"] == wire_format
        )
        assert (
            json.loads(target.read_text())["TOKEN"]
            if wire_format == "flat-json"
            else target.read_text().strip()
        ) == SENTINEL
    target = tmp_path / "blank.env"
    message = _request(target)
    fields = ["TOKEN", "OPTIONAL"]
    message["context"]["original"]["spec"]["fields"] = fields
    message["context"]["presentation"]["spec"]["fields"] = list(fields)
    message["context"]["destination"]["fields"] = list(fields)
    result, _ = _exchange(
        _rehash(message),
        returns(
            {"action": "ok", "values": {"f0": SENTINEL, "f1": ""}, "destination_confirmed": True}
        ),
    )
    assert result.effect["filled"] == ["TOKEN"] and result.effect["skipped"] == ["OPTIONAL"]
    assert target.read_text() == "TOKEN=" + SENTINEL + "\n"
    script = tmp_path / "write_then_fail.py"
    effect_file = tmp_path / "synthetic-effect"
    script.write_text(
        "import pathlib,sys\npathlib.Path(sys.argv[1]).write_text(sys.stdin.read())\nsys.exit(1)\n"
    )
    message = _request(tmp_path / "unused.env")
    spec = {
        "verb": "secret",
        "fields": ["TOKEN"],
        "store": shlex.join([sys.executable, str(script), str(effect_file)]),
    }
    context = message["context"]
    context["original"] = {"spec": spec}
    context["presentation"]["spec"] = copy.deepcopy(spec)
    context["destination"] = {
        "kind": "store-command",
        "host": socket.gethostname(),
        "cwd": os.getcwd(),
        "resolved_executable": str(Path(sys.executable).absolute()),
        "fields": ["TOKEN"],
        "argv_by_field": [
            {
                "field": "TOKEN",
                "argv": [str(Path(sys.executable).absolute()), str(script), str(effect_file)],
            }
        ],
    }
    result, frames = _exchange(
        _rehash(message),
        returns(
            {
                "action": "ok",
                "values": {"f0": SENTINEL},
                "destination_confirmed": True,
            }
        ),
    )
    assert effect_file.read_text() == SENTINEL
    assert result.collection == "error" and result.effect["outcome"] == "unknown"
    assert result.effect["filled"] == [] and result.effect["detail"] == "completion-unproved"
    assert result.worker_receipt == "received" and result.native_delivery == "refused"
    assert SENTINEL not in repr(result) and SENTINEL not in json.dumps(frames)
    for mode, fields in (("partial", ["TOKEN", "SECOND"]), ("timeout", ["TOKEN"])):
        directory = tmp_path / mode
        directory.mkdir()
        script = directory / "synthetic_store.py"
        script.write_text(
            "import pathlib,sys,time\n"
            "directory=pathlib.Path(sys.argv[1])\n"
            "mode,name=sys.argv[2:]\n"
            "(directory/name).write_text(sys.stdin.read())\n"
            "with (directory/'calls').open('a') as calls: calls.write(name+'\\n')\n"
            "if mode=='timeout': time.sleep(2)\n"
            "if name=='SECOND': sys.exit(1)\n"
        )
        message = _request(tmp_path / (mode + ".env"))
        spec = {
            "verb": "secret",
            "fields": fields,
            "store": shlex.join([sys.executable, str(script), str(directory), mode, "{name}"]),
        }
        context = message["context"]
        context["original"] = {"spec": spec}
        context["presentation"]["spec"] = copy.deepcopy(spec)
        context["destination"] = {
            "kind": "store-command",
            "host": socket.gethostname(),
            "cwd": os.getcwd(),
            "resolved_executable": str(Path(sys.executable).absolute()),
            "fields": fields,
            "argv_by_field": [
                {
                    "field": name,
                    "argv": [
                        str(Path(sys.executable).absolute()),
                        str(script),
                        str(directory),
                        mode,
                        name,
                    ],
                }
                for name in fields
            ],
        }
        for altered_key in ("argv", "cwd", "executable"):
            changed = copy.deepcopy(message)
            destination = changed["context"]["destination"]
            if altered_key == "argv":
                destination["argv_by_field"][0]["argv"][-1] = "UNREQUESTED"
            elif altered_key == "cwd":
                destination["cwd"] += "/different"
            else:
                destination["resolved_executable"] += ".different"
            backend = returns(
                {
                    "action": "ok",
                    "values": {f"f{i}": SENTINEL for i in range(len(fields))},
                    "destination_confirmed": True,
                }
            )
            refused, _ = _exchange(_rehash(changed), backend, ack="refused")
            assert (
                refused.collection == "error"
                and not backend.pages
                and not (directory / "calls").exists()
            )
        with monkeypatch.context() as scoped:
            scoped.setattr("fenric.store.STORE_TIMEOUT_SECONDS", 0.15)
            result, frames = _exchange(
                _rehash(message),
                returns(
                    {
                        "action": "ok",
                        "values": {f"f{i}": SENTINEL for i in range(len(fields))},
                        "destination_confirmed": True,
                    }
                ),
            )
        assert (directory / "TOKEN").read_text() == SENTINEL
        assert result.collection == "error" and result.effect["outcome"] == "unknown"
        assert result.effect["filled"] == (["TOKEN"] if mode == "partial" else [])
        assert result.effect["detail"] == (
            "partial-write" if mode == "partial" else "completion-unproved"
        )
        assert (directory / "calls").read_text().splitlines() == fields
        assert SENTINEL not in repr(result) and SENTINEL not in json.dumps(frames)
    assert capsys.readouterr() == ("", "")
