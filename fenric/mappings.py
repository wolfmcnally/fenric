"""Derive an integrated presentation from the retained complete original.

Wire shape is broader than qualified renderer mappings. Unsupported mappings
refuse rather than displaying a caller-selected summary as an exact action.
"""

from __future__ import annotations

import copy
import os
import socket
from dataclasses import dataclass
from typing import Any

from fenric.engine import Destination, prepare_destination
from fenric.protocol import (
    ProtocolError,
    canonical,
    decision_digest,
    validate_answer,
    validate_message,
    validate_response_schema,
)
from fenric.spec import Spec, SpecError, validate_spec
from fenric.store import StoreError
from fenric.target import DOTENV, FLAT_JSON, SINGLE_VALUE, TargetError

_NATIVE = {
    "claude.question": ("claude", "AskUserQuestion", {"tool_use_id"}),
    "claude.approval": ("claude", None, {"tool_use_id"}),
    "codex.question": (
        "codex",
        "item/tool/requestUserInput",
        {"request_id", "thread_id", "turn_id", "item_id"},
    ),
    "codex.command": (
        "codex",
        "item/commandExecution/requestApproval",
        {"request_id", "thread_id", "turn_id", "item_id", "started_at_ms"},
    ),
    "codex.file": (
        "codex",
        "item/fileChange/requestApproval",
        {"request_id", "thread_id", "turn_id", "item_id", "started_at_ms"},
    ),
    "codex.elicitation": ("codex", "mcpServer/elicitation/request", {"request_id", "thread_id"}),
}


def _require(condition: bool) -> None:
    if not condition:
        raise ProtocolError


def validate_native(native: dict[str, Any]) -> None:
    """Enforce actual applicable native IDs; no fabricated vendor secret IDs."""
    route = native["route"]
    if route == "host.secret":
        _require(
            all(value is None for key, value in native.items() if key not in {"harness", "route"})
        )
        return
    harness, method, required = _NATIVE[route]
    _require(native["harness"] == harness)
    _require(native["method"] == method if method is not None else bool(native["method"]))
    optional = {"turn_id"} if route == "codex.elicitation" else set()
    ids = set(native) - {"harness", "route", "method"}
    _require(all(native[key] is not None for key in required))
    _require(all(native[key] is None for key in ids - required - optional))


def _sdk_question(original: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(set(original) == {"questions"})
    questions = original["questions"]
    _require(type(questions) is list and 0 < len(questions) <= 32)
    derived = []
    properties = {}
    for question in questions:
        _require(
            type(question) is dict
            and set(question) == {"question", "header", "options", "multiSelect"}
        )
        _require(type(question["multiSelect"]) is bool and question["multiSelect"] is False)
        prompt = question["question"]
        _require(type(prompt) is str and bool(prompt.strip()) and prompt not in properties)
        _require(type(question["header"]) is str and bool(question["header"].strip()))
        options = question["options"]
        _require(type(options) is list and 0 < len(options) <= 100)
        labels = []
        mapped_options = []
        for option in options:
            _require(type(option) is dict and set(option) == {"label", "description"})
            label, description = option["label"], option["description"]
            _require(type(label) is str and bool(label.strip()) and label not in labels)
            _require(type(description) is str and bool(description.strip()))
            labels.append(label)
            mapped_options.append({"id": label, "label": label, "detail": description})
        properties[prompt] = {"type": "string", "enum": labels}
        derived.append(
            {"id": prompt, "prompt": prompt, "kind": "choice", "options": mapped_options}
        )
    spec = {"verb": "ask", "questions": derived}
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return spec, schema


def _rpc(context: dict[str, Any], original: Any, *, unversioned: bool = False) -> dict[str, Any]:
    native = context["native"]
    required = {"id", "method", "params"}
    optional = {"jsonrpc"}
    if not unversioned:
        required.add("jsonrpc")
    else:
        optional.add("trace")
    _require(type(original) is dict and required <= set(original) <= required | optional)
    # Current command and MCP confirmation ServerRequests omit the version marker.
    # Validate it when supplied without inserting it into the retained original.
    if "jsonrpc" in original:
        _require(original["jsonrpc"] == "2.0")
    trace = original.get("trace")
    if trace is not None:
        _require(type(trace) is dict and set(trace) <= {"traceparent", "tracestate"})
        _require(all(value is None or type(value) is str for value in trace.values()))
    _require(original["method"] == native["method"])
    _require(canonical(original["id"]) == canonical(native["request_id"]))
    params = original["params"]
    _require(type(params) is dict)
    for actual, retained in (
        ("threadId", "thread_id"),
        ("turnId", "turn_id"),
        ("itemId", "item_id"),
    ):
        _require(params.get(actual) == native[retained])
    return params


def _codex_question(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    original = context["original"]
    _require(set(original) == {"questions", "rpc"})
    params = _rpc(context, original["rpc"])
    _require(set(params) == {"threadId", "turnId", "itemId", "isBlocking", "questions"})
    _require(params["isBlocking"] is True)
    questions = original["questions"]
    _require(canonical(questions) == canonical(params["questions"]))
    _require(type(questions) is list and len(questions) == 1)
    question = questions[0]
    _require(
        type(question) is dict
        and set(question) == {"id", "header", "question", "options", "isOther", "isSecret"}
    )
    _require(question["isOther"] is False and question["isSecret"] is False)
    for key in ("id", "header", "question"):
        _require(type(question[key]) is str and bool(question[key].strip()))
    options = question["options"]
    _require(type(options) is list and 0 < len(options) <= 100)
    labels = []
    mapped = []
    for option in options:
        _require(type(option) is dict and set(option) == {"label", "description"})
        label, description = option["label"], option["description"]
        _require(type(label) is str and bool(label.strip()) and label not in labels)
        _require(type(description) is str and bool(description.strip()))
        labels.append(label)
        mapped.append({"id": label, "label": label, "detail": description})
    identifier = question["id"]
    return (
        {
            "verb": "ask",
            "questions": [
                {
                    "id": identifier,
                    "prompt": question["question"],
                    "kind": "choice",
                    "options": mapped,
                }
            ],
        },
        {
            "type": "object",
            "properties": {identifier: {"type": "string", "enum": labels}},
            "required": [identifier],
            "additionalProperties": False,
        },
    )


def _codex_mcp_confirmation(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    original = context["original"]
    _require(set(original) == {"rpc", "action"})
    params = _rpc(context, original["rpc"], unversioned=True)
    required = {"threadId", "turnId", "itemId", "isBlocking", "questions"}
    _require(required <= set(params) <= required | {"autoResolutionMs"})
    _require(params["isBlocking"] is True and params.get("autoResolutionMs") is None)
    action = original["action"]
    mandatory = {"type", "id", "server", "tool", "arguments", "status"}
    optional = {
        "appContext",
        "durationMs",
        "error",
        "mcpAppResourceUri",
        "mcpAppUi",
        "pluginId",
        "readOnlyHint",
        "result",
    }
    _require(type(action) is dict and mandatory <= set(action) <= mandatory | optional)
    _require(action["type"] == "mcpToolCall" and action["status"] == "inProgress")
    for key in ("id", "server", "tool"):
        _require(type(action[key]) is str and bool(action[key].strip()))
    _require(action["id"] == params["itemId"])
    for key in ("durationMs", "error", "result"):
        _require(action.get(key) is None)
    for key in ("pluginId", "mcpAppResourceUri"):
        _require(action.get(key) is None or type(action[key]) is str)
    _require(action.get("readOnlyHint") is None or type(action["readOnlyHint"]) is bool)
    app = action.get("appContext")
    if app is not None:
        _require(
            type(app) is dict
            and {"connectorId"}
            <= set(app)
            <= {
                "connectorId",
                "actionName",
                "appName",
                "linkId",
                "resourceUri",
            }
        )
        _require(type(app["connectorId"]) is str)
        _require(all(value is None or type(value) is str for value in app.values()))
    ui = action.get("mcpAppUi")
    if ui is not None:
        _require(type(ui) is dict and set(ui) == {"resourceUri", "preferredModelDisplayMode"})
        _require(type(ui["resourceUri"]) is str)
        _require(ui["preferredModelDisplayMode"] in ("inline", "fullscreen"))
    questions = params["questions"]
    _require(type(questions) is list and len(questions) == 1)
    question = questions[0]
    _require(
        type(question) is dict
        and set(question)
        == {
            "id",
            "header",
            "question",
            "options",
            "isOther",
            "isSecret",
        }
    )
    identifier = "mcp_tool_call_approval_" + action["id"]
    _require(question["id"] == identifier and question["header"] == "Approve app tool call?")
    _require(type(question["question"]) is str and bool(question["question"].strip()))
    _require(question["isOther"] is False and question["isSecret"] is False)
    allow = {"label": "Allow", "description": "Run the tool and continue."}
    cancel = {"label": "Cancel", "description": "Cancel this tool call."}
    session = {
        "label": "Allow for this session",
        "description": "Run the tool and remember this choice for this session.",
    }
    options = question["options"]
    _require(options == [allow, cancel] or options == [allow, session, cancel])
    message = (
        "Allow exactly this pending tool call with unchanged arguments, or cancel it. "
        "This grants no future permissions.\nUntrusted action data:\n"
        + canonical({key: action[key] for key in ("server", "tool", "arguments")}).decode()
    )
    if len(options) == 3:
        message += (
            "\nThe session-wide alternative is retained in the original "
            "but cannot be selected here."
        )
    return (
        {
            "verb": "ask",
            "title": "Approve exactly one pending tool call",
            "message": message,
            "questions": [
                {
                    "id": identifier,
                    "prompt": "Allow this exact tool call once, or cancel it?",
                    "kind": "choice",
                    "options": [
                        {
                            "id": option["label"],
                            "label": option["label"],
                            "detail": option["description"],
                        }
                        for option in (allow, cancel)
                    ],
                }
            ],
        },
        {
            "type": "object",
            "properties": {identifier: {"type": "string", "enum": ["Allow", "Cancel"]}},
            "required": [identifier],
            "additionalProperties": False,
        },
    )


def _execpolicy_proposal(value: Any) -> None:
    _require(type(value) is list and bool(value))
    _require(all(type(part) is str and bool(part) for part in value))


def _codex_command(context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    native = context["native"]
    params = _rpc(context, context["original"], unversioned=True)
    required = {"threadId", "turnId", "itemId", "startedAtMs"}
    allowed = required | {
        "approvalId",
        "kind",
        "command",
        "cwd",
        "environmentId",
        "reason",
        "commandActions",
        "availableDecisions",
        "additionalPermissions",
        "networkApprovalContext",
        "proposedExecpolicyAmendment",
        "proposedNetworkPolicyAmendments",
    }
    _require(required <= set(params) <= allowed)
    for key in ("threadId", "turnId", "itemId", "command", "cwd"):
        _require(type(params.get(key)) is str and bool(params[key].strip()))
    _require(os.path.isabs(params["cwd"]))
    started = params["startedAtMs"]
    _require(type(started) is int and -(2**53 - 1) <= started <= 2**53 - 1)
    _require(canonical(started) == canonical(native["started_at_ms"]))
    _require(params.get("approvalId") is None)
    _require(params.get("kind", "command") == "command")
    for key in ("environmentId", "reason"):
        _require(params.get(key) is None or type(params[key]) is str)
    for key in ("additionalPermissions", "networkApprovalContext"):
        _require(params.get(key) is None)
    if params.get("proposedExecpolicyAmendment") is not None:
        _execpolicy_proposal(params["proposedExecpolicyAmendment"])
    amendments = params.get("proposedNetworkPolicyAmendments")
    _require(amendments is None or (type(amendments) is list and not amendments))
    actions = params.get("commandActions")
    _require(actions is None or type(actions) is list)
    if actions is not None:
        for action in actions:
            _require(type(action) is dict and type(action.get("type")) is str)
            kind = action["type"]
            shapes = {
                "read": ({"type", "command", "name", "path"}, set()),
                "listFiles": ({"type", "command"}, {"path"}),
                "search": ({"type", "command"}, {"path", "query"}),
                "unknown": ({"type", "command"}, set()),
            }
            _require(kind in shapes)
            mandatory, optional = shapes[kind]
            _require(mandatory <= set(action) <= mandatory | optional)
            _require(all(type(action[key]) is str for key in mandatory))
            _require(
                all(
                    action[key] is None or type(action[key]) is str
                    for key in optional & set(action)
                )
            )
    offered = params.get("availableDecisions")
    _require(type(offered) is list and bool(offered))
    choices = []
    seen = set()
    for choice in offered:
        if type(choice) is str:
            _require(choice in {"accept", "decline", "cancel", "acceptForSession"})
            if choice != "acceptForSession":
                choices.append(choice)
        else:
            _require(type(choice) is dict and set(choice) == {"acceptWithExecpolicyAmendment"})
            amendment = choice["acceptWithExecpolicyAmendment"]
            _require(type(amendment) is dict and set(amendment) == {"execpolicy_amendment"})
            _execpolicy_proposal(amendment["execpolicy_amendment"])
        encoded = canonical(choice)
        _require(encoded not in seen)
        seen.add(encoded)
    _require(bool(choices))
    return params, choices


def _command_message(params: dict[str, Any]) -> str:
    lines = [
        "Codex requests a decision for exactly one pending command.",
        "Command (untrusted action text):",
        params["command"],
        "Working directory:",
        params["cwd"],
    ]
    for key, label in (
        ("reason", "Reason supplied by Codex"),
        ("environmentId", "Execution environment"),
    ):
        if params.get(key) is not None:
            lines.extend([label + ":", params[key]])
    lines.extend(
        [
            "Approve allows this exact command once, with unchanged input. "
            "No future permissions are granted.",
            "The complete original request remains available in Technical details below.",
        ]
    )
    if params.get("proposedExecpolicyAmendment") is not None or any(
        type(choice) is dict or choice == "acceptForSession"
        for choice in params["availableDecisions"]
    ):
        lines.append(
            "Proposed rules and session-wide alternatives are information only; "
            "they cannot be selected here."
        )
    return "\n".join(lines)


def _approval(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    native, original = context["native"], context["original"]
    if native["route"] == "claude.approval":
        _require(set(original) == {"tool_name", "input"})
        _require(original["tool_name"] == native["method"] and type(original["input"]) is dict)
        choices = ["allow", "deny"]
        details = [
            "Allow this tool call with unchanged input.",
            "Deny this tool call; do not interrupt the session.",
        ]
        labels = choices
        message = (
            "Untrusted action data below. This grants no future permission.\n"
            + canonical(original).decode()
        )
        prompt = "Choose the exact native decision"
    else:
        params, choices = _codex_command(context)
        descriptions = {
            "accept": ("Approve once", "Approve this action once."),
            "decline": ("Deny", "Deny this action; continue the turn."),
            "cancel": ("Cancel this turn", "Deny this action and interrupt the turn."),
        }
        labels = [descriptions[choice][0] for choice in choices]
        details = [descriptions[choice][1] for choice in choices]
        message = _command_message(params)
        prompt = "Choose what happens to this command"
    _require(context["choices"] == choices)
    spec = {
        "verb": "ask",
        "title": "Approve exactly one pending action",
        "message": message,
        "questions": [
            {
                "id": "decision",
                "prompt": prompt,
                "kind": "choice",
                "options": [
                    {"id": choice, "label": label, "detail": detail}
                    for choice, label, detail in zip(choices, labels, details, strict=True)
                ],
            }
        ],
    }
    return spec, {"type": "string", "enum": choices}


def project_answer(context: dict[str, Any], value: Any) -> Any:
    """Convert only admitted approval choice objects to their scalar wire token."""
    if context["native"]["route"] in {"claude.approval", "codex.command"}:
        _require(type(value) is dict and set(value) == {"decision"})
        value = value["decision"]
    validate_answer(value, context["response_schema"])
    return value


def prepared_destination(destination: Destination) -> dict[str, Any]:
    """Normalize the real prepared sink, preserving exact invocation semantics."""
    host = socket.gethostname()
    if destination.target is not None:
        return {
            "kind": "file",
            "host": host,
            "resolved_path": str(destination.target.path),
            "format": {DOTENV: "dotenv", FLAT_JSON: "flat-json", SINGLE_VALUE: "single-value"}[
                destination.target.kind
            ],
            "fields": list(destination.names),
        }
    arguments = destination.arguments
    _require(bool(arguments) and all(row and row[0] == arguments[0][0] for row in arguments))
    return {
        "kind": "store-command",
        "host": host,
        "cwd": os.getcwd(),
        "resolved_executable": arguments[0][0],
        "argv_by_field": [
            {"field": name, "argv": list(argv)}
            for name, argv in zip(destination.names, arguments, strict=True)
        ],
        "fields": list(destination.names),
    }


@dataclass(frozen=True)
class PreparedRequest:
    """Private retained context and independently prepared render/sink inputs."""

    message: dict[str, Any]
    spec: Spec
    destination: Destination | None

    @property
    def context(self) -> dict[str, Any]:
        return self.message["context"]


def prepare_request(message: dict[str, Any], *, now_ms: int) -> PreparedRequest:
    """Validate before displaying; unsupported complete mappings still refuse."""
    validate_message(message)
    _require(message["type"] == "request")
    retained = copy.deepcopy(message)
    context = retained["context"]
    _require(retained["decision_digest"] == decision_digest(context))
    issued, expiry = context["issued_at_ms"], context["expires_at_ms"]
    _require(0 < expiry - issued <= 3_600_000 and issued <= now_ms < expiry)
    _require(context["host"] == socket.gethostname())
    validate_native(context["native"])
    surfaces = context["allowed_surfaces"]
    _require(bool(surfaces) and len(surfaces) == len(set(surfaces)))
    observations = context["expected_observations"]
    _require({entry["surface"] for entry in observations} == set(surfaces))
    for entry in observations:
        _require(entry["node"] == context["host"])
        _require(
            entry["authentication_basis"]
            == ("local_process_uid" if entry["surface"] == "local" else "tailscale_user_login")
        )
    validate_response_schema(context["response_schema"])
    route = context["native"]["route"]
    destination = None
    if route in {"claude.question", "codex.question"}:
        _require(context["kind"] == "question" and context["destination"] is None)
        _require(
            context["scope"]
            == {"category": "structured-answer", "tool": context["native"]["method"]}
        )
        _require(context["choices"] == [])
        if route == "claude.question":
            raw, schema = _sdk_question(context["original"])
            mapping = "fenric.sdk-question.v1"
        elif set(context["original"]) == {"rpc", "action"}:
            raw, schema = _codex_mcp_confirmation(context)
            mapping = "fenric.codex-mcp-confirmation.v1"
        else:
            raw, schema = _codex_question(context)
            mapping = "fenric.codex-question.v1"
        _require(context["response_schema"] == schema)
        _require(context["presentation"] == {"mapping": mapping, "spec": raw})
    elif route in {"claude.approval", "codex.command"}:
        _require(context["kind"] == "approval" and context["destination"] is None)
        _require(
            context["scope"] == {"category": "one-call-tool", "tool": context["native"]["method"]}
        )
        raw, schema = _approval(context)
        _require(context["response_schema"] == schema)
        mapping = (
            "fenric.claude-approval.v1"
            if route == "claude.approval"
            else "fenric.codex-command-approval.v1"
        )
        _require(context["presentation"] == {"mapping": mapping, "spec": raw})
    elif route == "host.secret":
        _require(context["kind"] == "secret" and context["destination"] is not None)
        _require(context["scope"] == {"category": "secret-destination", "tool": None})
        _require(context["response_schema"] == {"type": "null"} and context["choices"] == [])
        _require(set(context["original"]) == {"spec"})
        raw = context["original"]["spec"]
        _require(context["presentation"] == {"mapping": "fenric.secret.v1", "spec": raw})
        _require(type(raw) is dict and raw.get("verb") == "secret")
    else:
        # Structural support is not a qualified rendering/custody route.
        raise ProtocolError
    try:
        spec = validate_spec(raw)
        if route == "host.secret":
            _require(len(spec.fields["fields"]) <= 32)
            destination = prepare_destination(spec)
            _require(prepared_destination(destination) == context["destination"])
    except (SpecError, TargetError, StoreError, OSError):
        raise ProtocolError from None
    return PreparedRequest(retained, spec, destination)


def check_observation(context: dict[str, Any], observation: dict[str, Any]) -> None:
    """Check every supplied observation, even after a non-answer collection."""
    keys = ("surface", "observed_subject", "authentication_basis", "node", "session")
    _require({key: observation[key] for key in keys} in context["expected_observations"])
    _require(observation["surface"] in context["allowed_surfaces"])
    _require(observation["policy_ref"] == context["principal_policy"])
    _require(context["issued_at_ms"] <= observation["observed_at_ms"] < context["expires_at_ms"])
