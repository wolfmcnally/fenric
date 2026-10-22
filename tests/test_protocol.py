"""Independent wire bytes and refusal boundaries, without a GUI or native host."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import struct
import time
from pathlib import Path

import pytest

from fenric.protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    canonical,
    decision_digest,
    parse,
    read_frame,
    validate_answer,
    validate_message,
    write_frame,
)

PROJECT = Path(__file__).resolve().parents[1]
VECTORS = PROJECT / "tests/fixtures/operator-response/golden.json"
SCHEMA = PROJECT / "fenric/_protocol/interaction.schema.json"


def _vectors() -> list[dict]:
    return json.loads(VECTORS.read_bytes())


def test_canonical_wire_vectors() -> None:
    assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == (
        "36760678e37a257bdd87768a4bbdd2901dd210856429565c0ef3fa1200c2a82a"
    )
    assert hashlib.sha256(VECTORS.read_bytes()).hexdigest() == (
        "22a2ee8c992dd4e644aa22ea0017527288b0adaff661d10ba0956f85c7501213"
    )
    for vector in _vectors():
        expected = bytes.fromhex(vector["canonical_utf8_hex"])
        assert canonical(vector["value"]) == expected, vector["name"]
        assert decision_digest(vector["value"]) == vector["sha256"]
        assert parse(expected) == vector["value"]
        sender, receiver = socket.socketpair()
        try:
            write_frame(sender, vector["value"])
            receiver.settimeout(1)
            expected_frame = bytes.fromhex(vector["frame_hex"])
            actual = bytearray()
            while len(actual) < len(expected_frame):
                actual.extend(receiver.recv(len(expected_frame) - len(actual)))
            assert bytes(actual) == expected_frame
        finally:
            sender.close()
            receiver.close()


def test_strict_json_domain() -> None:
    refused = [
        b'{"x":1,"x":2}',
        b'{"x":{"y":1,"y":2}}',
        b"1.0",
        b"1e0",
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"9007199254740992",
        b"-9007199254740992",
        b'"\\ud800"',
        b'"\\udfff"',
        b'"\xff"',
        b"{} trailing",
        b"",
        b" " * (MAX_FRAME_BYTES + 1),
        b"[" * 65 + b"null" + b"]" * 65,
    ]
    for raw in refused:
        with pytest.raises(ProtocolError, match="^integrated interaction refused$"):
            parse(raw)
    for value in (1.0, float("nan"), 1 << 53, {1: "key"}, {"x": object()}, "\ud800"):
        with pytest.raises(ProtocolError):
            canonical(value)
    cycle: list = []
    cycle.append(cycle)
    with pytest.raises(ProtocolError):
        canonical(cycle)
    assert parse(b"-9007199254740991") == -9007199254740991
    assert parse(b"9007199254740991") == 9007199254740991
    assert canonical({"x": False, "y": 0}) == b'{"x":false,"y":0}'


def test_closed_schema_and_answer_validation() -> None:
    messages = {
        v["name"]: v["value"]
        for v in _vectors()
        if v["name"] in ("request", "response", "secret_request", "secret_response")
    }
    assert len(messages) == 4
    for message in messages.values():
        validate_message(message)
        altered = copy.deepcopy(message)
        altered["extra"] = "not a structural field"
        with pytest.raises(ProtocolError):
            validate_message(altered)
    altered = copy.deepcopy(messages["request"])
    altered["context"]["participant"] += "\n"
    with pytest.raises(ProtocolError):
        validate_message(altered)
    altered = copy.deepcopy(messages["request"])
    altered["context"]["native"]["request_id"] = True
    with pytest.raises(ProtocolError):
        validate_message(altered)
    dynamic = {"type": "object", "additionalProperties": {"type": "string"}}
    validate_answer({"ordinary": "answer", "empty": ""}, dynamic)
    with pytest.raises(ProtocolError):
        validate_answer({"ordinary": 3}, dynamic)
    validate_answer(False, {"type": "boolean"})
    validate_answer([], {"type": "array", "items": {"type": "string"}})
    for value, schema in (
        (False, {"enum": [0]}),
        (0, {"enum": [False]}),
        ("value", {"type": "string", "pattern": ".*"}),
        ("value", {"$ref": "https://unavailable.invalid/schema"}),
        ({}, {"type": "object", "required": ["missing"]}),
        ("ab", {"type": "string", "maxLength": 1}),
        (["a", "a"], {"type": "array", "uniqueItems": True}),
        (True, {"type": "integer"}),
    ):
        with pytest.raises(ProtocolError):
            validate_answer(value, schema)


def test_bounded_frame_io() -> None:
    for raw in (
        b"\x00",
        struct.pack(">I", 0),
        struct.pack(">I", MAX_FRAME_BYTES + 1),
        struct.pack(">I", 5) + b"{}",
        b"",
    ):
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(raw)
            sender.shutdown(socket.SHUT_WR)
            with pytest.raises(ProtocolError):
                read_frame(receiver, deadline=time.monotonic() + 0.1)
        finally:
            sender.close()
            receiver.close()
    sender, receiver = socket.socketpair()
    try:
        with pytest.raises(ProtocolError):
            read_frame(receiver, deadline=time.monotonic() + 0.02)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        with pytest.raises(ProtocolError):
            write_frame(sender, "x" * (MAX_FRAME_BYTES - 2), deadline=time.monotonic() + 0.02)
    finally:
        sender.close()
        receiver.close()
    sender, receiver = socket.socketpair()
    try:
        sender.sendall(b"\x00\x00\x00\x05false")
        assert read_frame(receiver) is False
    finally:
        sender.close()
        receiver.close()
