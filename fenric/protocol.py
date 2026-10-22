"""Bounded, value-free wire primitives for an admitted operator-response channel.

Encoding and digest equality do not authenticate a host. Admission and native
request custody belong to the process that supplied the private endpoint.
"""

from __future__ import annotations

import hashlib
import json
import re
import select
import socket
import struct
import time
from functools import cache
from importlib import resources
from typing import Any

PROTOCOL = "fenric.operator-response.v1"
MAX_FRAME_BYTES = 1 << 20
MAX_DEPTH = 64
MAX_NODES = 100_000
SAFE_INTEGER = (1 << 53) - 1
FRAME_SECONDS = 2.0


class ProtocolError(ValueError):
    """A fixed refusal that never includes supplied data or parser diagnostics."""

    def __init__(self) -> None:
        super().__init__("integrated interaction refused")


def _require(condition: bool) -> None:
    if not condition:
        raise ProtocolError


def _domain(value: Any) -> None:
    pending = [(value, 0)]
    remaining = MAX_NODES
    while pending:
        entry, depth = pending.pop()
        remaining -= 1
        _require(remaining >= 0 and depth <= MAX_DEPTH)
        kind = type(entry)
        if entry is None or kind is bool:
            continue
        if kind is int:
            _require(abs(entry) <= SAFE_INTEGER)
        elif kind is str:
            _require(not any(0xD800 <= ord(character) <= 0xDFFF for character in entry))
        elif kind is list:
            _require(len(entry) <= remaining)
            pending.extend((item, depth + 1) for item in entry)
        elif kind is dict:
            _require(2 * len(entry) <= remaining)
            for key, item in entry.items():
                _require(type(key) is str)
                pending.extend(((key, depth + 1), (item, depth + 1)))
        else:
            raise ProtocolError


def canonical(value: Any) -> bytes:
    """Encode the agreed safe-integer JSON domain, sorting Unicode scalar keys."""
    _domain(value)
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError from None
    _require(0 < len(encoded) <= MAX_FRAME_BYTES)
    return encoded


def decision_digest(context: dict[str, Any]) -> str:
    """Hash the complete context, without renderer transport or capabilities."""
    return hashlib.sha256(canonical(context)).hexdigest()


def parse(raw: bytes) -> Any:
    """Parse strict bounded JSON; duplicate keys and all float syntax refuse."""
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME_BYTES)

    def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in entries:
            _require(key not in result)
            result[key] = value
        return result

    def integer(text: str) -> int:
        _require(len(text) <= 17)
        number = int(text)
        _require(abs(number) <= SAFE_INTEGER)
        return number

    def invalid(_: str) -> Any:
        raise ProtocolError

    try:
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_int=integer,
            parse_float=invalid,
            parse_constant=invalid,
        )
        _domain(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError from None


@cache
def _wire_schema() -> dict[str, Any]:
    path = resources.files("fenric").joinpath("_protocol/interaction.schema.json")
    return parse(path.read_bytes())


_KEYWORDS = frozenset(
    {
        "type",
        "const",
        "enum",
        "oneOf",
        "anyOf",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    }
)
_TRUSTED_KEYWORDS = _KEYWORDS | {"$ref", "$defs", "$schema", "$id", "pattern", "title"}
_TYPES = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    str: "string",
    list: "array",
    dict: "object",
}


def _schema_shape(schema: Any, *, trusted: bool, depth: int = 0) -> None:
    _require(type(schema) is dict and depth <= MAX_DEPTH)
    _require(set(schema) <= (_TRUSTED_KEYWORDS if trusted else _KEYWORDS))
    if "type" in schema:
        names = schema["type"] if type(schema["type"]) is list else [schema["type"]]
        _require(
            bool(names) and all(type(name) is str and name in _TYPES.values() for name in names)
        )
    for name in ("oneOf", "anyOf"):
        if name in schema:
            _require(type(schema[name]) is list and bool(schema[name]))
            for alternative in schema[name]:
                _schema_shape(alternative, trusted=trusted, depth=depth + 1)
    for name in ("properties", "$defs"):
        if name in schema:
            _require(type(schema[name]) is dict)
            for subschema in schema[name].values():
                _schema_shape(subschema, trusted=trusted, depth=depth + 1)
    if "required" in schema:
        required = schema["required"]
        _require(type(required) is list and all(type(key) is str for key in required))
        _require(len(required) == len(set(required)))
    if "additionalProperties" in schema:
        extra = schema["additionalProperties"]
        if type(extra) is not bool:
            _schema_shape(extra, trusted=trusted, depth=depth + 1)
    if "items" in schema:
        _schema_shape(schema["items"], trusted=trusted, depth=depth + 1)
    if "enum" in schema:
        _require(type(schema["enum"]) is list and bool(schema["enum"]))
    for name in ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"):
        if name in schema:
            _require(type(schema[name]) is int)
            if name not in ("minimum", "maximum"):
                _require(schema[name] >= 0)
    if "uniqueItems" in schema:
        _require(type(schema["uniqueItems"]) is bool)
    if "$ref" in schema:
        _require(type(schema["$ref"]) is str and schema["$ref"].startswith("#/$defs/"))
    if "pattern" in schema:
        _require(type(schema["pattern"]) is str)


def _matches(value: Any, schema: dict[str, Any], root: dict[str, Any], budget: list[int]) -> None:
    budget[0] -= 1
    _require(budget[0] >= 0)
    if "$ref" in schema:
        target = schema["$ref"][len("#/$defs/") :]
        _require(target in root.get("$defs", {}))
        _matches(value, root["$defs"][target], root, budget)
    for name in ("oneOf", "anyOf"):
        if name in schema:
            matches = 0
            for alternative in schema[name]:
                try:
                    _matches(value, alternative, root, budget)
                    matches += 1
                except ProtocolError:
                    _require(budget[0] >= 0)
            _require(matches == 1 if name == "oneOf" else matches > 0)
    if "type" in schema:
        names = schema["type"] if type(schema["type"]) is list else [schema["type"]]
        _require(_TYPES[type(value)] in names)
    if "const" in schema:
        _require(canonical(value) == canonical(schema["const"]))
    if "enum" in schema:
        encoded = canonical(value)
        _require(any(encoded == canonical(choice) for choice in schema["enum"]))
    if type(value) is dict:
        _require(all(key in value for key in schema.get("required", [])))
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, entry in value.items():
            if key in properties:
                _matches(entry, properties[key], root, budget)
            elif extra is False:
                raise ProtocolError
            elif type(extra) is dict:
                _matches(entry, extra, root, budget)
    if type(value) is list:
        _require(schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", MAX_NODES))
        if schema.get("uniqueItems"):
            _require(len({canonical(entry) for entry in value}) == len(value))
        if "items" in schema:
            for entry in value:
                _matches(entry, schema["items"], root, budget)
    if type(value) is str:
        _require(
            schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_FRAME_BYTES)
        )
        if "pattern" in schema:
            _require(re.fullmatch(schema["pattern"], value) is not None)
    if type(value) is int:
        _require(
            schema.get("minimum", -SAFE_INTEGER) <= value <= schema.get("maximum", SAFE_INTEGER)
        )


def validate_message(message: Any) -> None:
    """Validate the closed wire structure; runtime semantics remain separate."""
    _domain(message)
    schema = _wire_schema()
    _schema_shape(schema, trusted=True)
    _matches(message, schema, schema, [MAX_NODES])


def validate_response_schema(schema: dict[str, Any]) -> None:
    """Refuse unsupported response-schema features before collecting an answer."""
    _domain(schema)
    _schema_shape(schema, trusted=False)


def validate_answer(value: Any, schema: dict[str, Any]) -> None:
    """Validate an answer against the bounded supported response-schema subset."""
    _domain(value)
    validate_response_schema(schema)
    _matches(value, schema, schema, [MAX_NODES])


def _transfer(channel: socket.socket, data: bytes | int, deadline: float) -> bytes:
    reading = type(data) is int
    wanted = data if reading else len(data)
    result = bytearray()
    offset = 0
    try:
        while offset < wanted:
            remaining = deadline - time.monotonic()
            _require(remaining > 0)
            ready = select.select(
                [channel] if reading else [], [] if reading else [channel], [], remaining
            )
            _require(bool(ready[0] if reading else ready[1]))
            try:
                if reading:
                    chunk = channel.recv(wanted - offset, socket.MSG_DONTWAIT)
                    _require(bool(chunk))
                    result.extend(chunk)
                    offset += len(chunk)
                else:
                    sent = channel.send(memoryview(data)[offset:], socket.MSG_DONTWAIT)
                    _require(sent > 0)
                    offset += sent
            except (BlockingIOError, InterruptedError):
                continue
    except (OSError, ValueError):
        raise ProtocolError from None
    return bytes(result)


def read_frame(channel: socket.socket, *, deadline: float | None = None) -> Any:
    """Read one frame within an absolute monotonic deadline; EOF is a refusal."""
    limit = time.monotonic() + FRAME_SECONDS if deadline is None else deadline
    length = struct.unpack(">I", _transfer(channel, 4, limit))[0]
    _require(0 < length <= MAX_FRAME_BYTES)
    return parse(_transfer(channel, length, limit))


def write_frame(channel: socket.socket, message: Any, *, deadline: float | None = None) -> None:
    """Write one frame; callers serialize writers on their admitted endpoint."""
    body = canonical(message)
    limit = time.monotonic() + FRAME_SECONDS if deadline is None else deadline
    _transfer(channel, struct.pack(">I", len(body)) + body, limit)
