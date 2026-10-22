"""One request-bound renderer on a private, host-admitted inherited endpoint.

This module does not discover or authenticate native harnesses. Its requesting
host owns endpoint admission, current principal policy and native resolution.
"""

from __future__ import annotations

import copy
import datetime
import os
import select
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fenric import backend as backends
from fenric.engine import Destination, Result, prepare_destination, run
from fenric.mappings import (
    PreparedRequest,
    check_observation,
    prepare_request,
    prepared_destination,
    project_answer,
)
from fenric.protocol import (
    FRAME_SECONDS,
    PROTOCOL,
    ProtocolError,
    canonical,
    read_frame,
    validate_answer,
    validate_message,
    write_frame,
)
from fenric.result import validate_result
from fenric.session import Session
from fenric.spec import Spec

ACK_SECONDS = 2.0
INITIAL_SECONDS = 5.0


def _require(condition: bool) -> None:
    if not condition:
        raise ProtocolError


@dataclass(frozen=True)
class IntegratedResult:
    """Separate non-secret collection, adapter receipt and native disposition.

    A delivery disposition is the admitted host's report, not independent
    proof by Fenric that a native action executed. No capability is returned.
    """

    collection: str
    response: dict[str, Any] | None
    worker_receipt: str = "unknown"
    native_delivery: str = "unknown"
    native_evidence: str = "submission-unproved"
    effect: dict[str, Any] | None = None


class Interaction:
    """Private retained request, renderer lifecycle and status-only sink effect."""

    def __init__(self, channel: socket.socket, prepared: PreparedRequest) -> None:
        self.channel = channel
        self.request = prepared
        self.context = prepared.context
        self.transport = copy.deepcopy(prepared.message["transport"])
        self.settlement_id = str(uuid.uuid4())
        self.deadline = time.monotonic() + self.remaining_seconds
        self.session: Session | None = None
        self.response: dict[str, Any] | None = None
        self.receipt: dict[str, Any] | None = None
        self.delivery: dict[str, Any] | None = None
        self.channel_failed = False
        self._pending_invalidation: str | None = None
        self._state = threading.Lock()
        self.effect = {
            "outcome": "not_started" if self.context["kind"] == "secret" else "not_applicable",
            "destination": copy.deepcopy(self.context["destination"]),
            "filled": [],
            "skipped": [],
            "detail": "none",
            "destination_confirmed": False,
        }
        self._stop = threading.Event()
        self._acknowledged = threading.Event()
        self._response_started = threading.Event()
        self._writer = threading.Lock()
        self._reader = threading.Thread(target=self._receive, daemon=True)
        self._review_only = self.observe("local", None) is None
        self._reader.start()

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, (self.context["expires_at_ms"] - int(time.time() * 1000)) / 1000)

    @property
    def review_only(self) -> bool:
        return self._review_only

    @property
    def page_context(self) -> dict[str, Any]:
        expiry = datetime.datetime.fromtimestamp(self.context["expires_at_ms"] / 1000, datetime.UTC)
        command = self.context["native"]["route"] == "codex.command"
        return {
            "technical_details": command,
            **{key: self.context[key] for key in ("host", "environment", "project", "session")},
            "scope": canonical(self.context["scope"]).decode(),
            "expires": expiry.strftime("%Y-%m-%d %H:%M:%S UTC") if command else expiry.isoformat(),
            "original": canonical(self.context["original"]).decode(),
            "destination": canonical(self.context["destination"]).decode()
            if self.context["destination"] is not None
            else None,
        }

    @property
    def session_options(self) -> dict[str, Any]:
        return {
            "authorize": self.observe,
            "authority_deadline": self.deadline,
            "authority_expires_at_ms": self.context["expires_at_ms"],
            "review_only": self.review_only,
        }

    def observe(self, surface: str, subject: str | None) -> dict[str, Any] | None:
        observation = {
            "surface": surface,
            "observed_subject": str(os.getuid()) if surface == "local" else subject,
            "authentication_basis": "local_process_uid"
            if surface == "local"
            else "tailscale_user_login",
            "node": socket.gethostname(),
            "session": None,
            "observation_id": str(uuid.uuid4()),
            "observed_at_ms": int(time.time() * 1000),
            "policy_ref": self.context["principal_policy"],
        }
        try:
            check_observation(self.context, observation)
            return observation
        except ProtocolError:
            return None

    def prepare(self, request: Spec, destination: Destination) -> None:
        _require(request == self.request.spec)
        if request.verb == "secret":
            _require(prepared_destination(destination) == self.context["destination"])

    def validate(self, request: Spec, payload: Any) -> Any:
        if request.verb == "secret":
            _require(
                type(payload) is dict
                and set(payload) == {"action", "values", "destination_confirmed"}
            )
            _require(payload["destination_confirmed"] is True)
            payload = {key: payload[key] for key in ("action", "values")}
        value = validate_result(request, payload)
        if request.verb != "secret":
            value = project_answer(self.context, value)
        return value

    def before_sink(self, request: Spec, destination: Destination) -> None:
        self.effect["destination_confirmed"] = True
        try:
            current = prepare_destination(request)
            _require(prepared_destination(current) == self.context["destination"])
            _require(prepared_destination(destination) == self.context["destination"])
        except (ValueError, RuntimeError, OSError):
            self.effect["detail"] = "validation-refused"
            raise ProtocolError from None

    def begin_secret(self, skipped: list[str]) -> None:
        self.effect.update(outcome="unknown", detail="completion-unproved", skipped=list(skipped))

    def record_effect(self, effect: dict[str, Any]) -> None:
        self.effect.update(copy.deepcopy(effect))

    def attach(self, session: Session) -> None:
        with self._state:
            self.session = session
            if self._pending_invalidation is not None:
                session.invalidate(self._pending_invalidation)

    def _invalidate(self, collection: str) -> None:
        with self._state:
            if self.session is not None:
                self.session.invalidate(collection)
            elif self._pending_invalidation is None:
                self._pending_invalidation = collection

    def _send(self, message: dict[str, Any]) -> bool:
        try:
            validate_message(message)
            with self._writer:
                write_frame(self.channel, message)
            return True
        except (ProtocolError, OSError):
            self.channel_failed = True
            self._invalidate("error")
            return False

    def _availability(self, surface: str, available: bool, capability: str | None = None) -> None:
        self._send(
            {
                "type": "availability",
                "interaction_id": self.context["interaction_id"],
                "transport": self.transport,
                "surface": surface,
                "state": "available" if available else "unavailable",
                "protected_capability": capability if available else None,
            }
        )

    def local_availability(self) -> None:
        self._availability(
            "local",
            not self.review_only and self.session is not None and not self.session.done.is_set(),
        )

    def availability(self, status: dict[str, Any]) -> None:
        # This callback is reached only from Remote's private mode. It never
        # forwards to a bus/model/logger or uses configured announcements.
        available = status["status"] == "available"
        self._availability("remote", available, status["url"] if available else None)
        if not available and self.review_only:
            self._invalidate("unavailable")

    def _bound(self, message: dict[str, Any], *, settlement: bool = False) -> None:
        _require(message["interaction_id"] == self.context["interaction_id"])
        _require(message["transport"] == self.transport)
        if settlement:
            _require(self._response_started.is_set())
            _require(message["settlement_id"] == self.settlement_id)
            _require(message["decision_digest"] == self.request.message["decision_digest"])

    def _receive(self) -> None:
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([self.channel], [], [], 0.05)
                if not ready:
                    continue
                message = read_frame(self.channel)
                if self._stop.is_set():
                    return
                validate_message(message)
                kind = message["type"]
                _require(kind in {"invalidate", "worker_receipt", "delivery_disposition"})
                self._bound(message, settlement=kind != "invalidate")
                if kind == "invalidate":
                    reason = message["reason"]
                    collection = {
                        "host-resolved": "superseded",
                        "revoked": "error",
                    }.get(reason, reason)
                    self._invalidate(collection)
                elif kind == "worker_receipt":
                    _require(self.receipt is None)
                    self.receipt = copy.deepcopy(message)
                else:
                    _require(self.delivery is None)
                    _require(message["native"] == self.context["native"])
                    if self.context["native"]["route"] == "host.secret":
                        _require(
                            message["outcome"] == "refused"
                            and message["evidence"] == "not-submitted"
                        )
                    elif message["outcome"] == "delivered":
                        _require(
                            self.response is not None and self.response["collection"] == "answered"
                        )
                        _require(message["evidence"] in {"native-resolution", "callback-return"})
                    self.delivery = copy.deepcopy(message)
                if self.receipt is not None and self.delivery is not None:
                    self._acknowledged.set()
        except (ProtocolError, OSError, ValueError):
            if not self._stop.is_set():
                self.channel_failed = True
                self._invalidate("error")
                self._acknowledged.set()

    def finish(self, result: Result) -> IntegratedResult:
        session = self.session
        collection = (
            session.collection
            if session and session.collection
            else {
                "ok": "answered",
                "cancelled": "cancelled",
                "unavailable": "unavailable",
                "error": "error",
            }[result.status]
        )
        observation = copy.deepcopy(session.observation) if session else None
        if observation is not None:
            check_observation(self.context, observation)
        answer = (
            result.value if collection == "answered" and self.context["kind"] != "secret" else None
        )
        if collection == "answered":
            _require(observation is not None)
            validate_answer(answer, self.context["response_schema"])
        response = {
            "protocol": PROTOCOL,
            "interaction_id": self.context["interaction_id"],
            "decision_digest": self.request.message["decision_digest"],
            "transport": self.transport,
            "expires_at_ms": self.context["expires_at_ms"],
            "settlement_id": self.settlement_id,
            "collection": collection,
            "answer": answer,
            "observation": observation,
            "effect": copy.deepcopy(self.effect),
        }
        validate_message({"type": "response", "response": response})
        self.response = response
        self._response_started.set()
        if self._send({"type": "response", "response": response}):
            self._acknowledged.wait(ACK_SECONDS)
        # Terminal closes acknowledgement admission, not only rendering.
        self._stop.set()
        self._send(
            {
                "type": "terminal",
                "interaction_id": self.context["interaction_id"],
                "transport": self.transport,
                "state": "failed" if self.channel_failed else "closed",
            }
        )
        return IntegratedResult(
            collection,
            copy.deepcopy(response),
            self.receipt["outcome"] if self.receipt else "unknown",
            self.delivery["outcome"] if self.delivery else "unknown",
            self.delivery["evidence"] if self.delivery else "submission-unproved",
            copy.deepcopy(self.effect),
        )

    def close(self) -> None:
        self._stop.set()
        try:
            self.channel.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.channel.close()
        if self._reader.ident is not None:
            self._reader.join(FRAME_SECONDS + 1)
        _require(not self._reader.is_alive())


def run_integrated(
    channel: socket.socket,
    *,
    backend: backends.Backend | None = None,
    cancel: threading.Event | None = None,
) -> IntegratedResult:
    """Consume one admitted Unix stream connection, never ordinary stdout.

    The requesting host must bootstrap and retain this endpoint privately.
    A caller-created endpoint is not an existing harness approval callback.
    The duplicate is closed at return; the one-shot connection is shut down.
    """
    owned: socket.socket | None = None
    interaction: Interaction | None = None
    try:
        _require(isinstance(channel, socket.socket))
        _require(hasattr(os, "getuid") and channel.family == socket.AF_UNIX)
        _require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM)
        channel.getpeername()
        owned = channel.dup()
        owned.set_inheritable(False)
        message = read_frame(owned, deadline=time.monotonic() + INITIAL_SECONDS)
        prepared = prepare_request(message, now_ms=int(time.time() * 1000))
        interaction = Interaction(owned, prepared)
        result = run(
            prepared.message["context"]["presentation"]["spec"],
            backend=backend,
            cancel=cancel,
            _interaction=interaction,
        )
        return interaction.finish(result)
    except (ProtocolError, OSError, ValueError, RuntimeError):
        if interaction is not None:
            interaction._stop.set()
            interaction._send(
                {
                    "type": "terminal",
                    "interaction_id": interaction.context["interaction_id"],
                    "transport": interaction.transport,
                    "state": "failed",
                }
            )
            collection = (
                interaction.session.collection if interaction.session else None
            ) or "error"
            return IntegratedResult(
                collection,
                copy.deepcopy(interaction.response),
                interaction.receipt["outcome"] if interaction.receipt else "unknown",
                interaction.delivery["outcome"] if interaction.delivery else "unknown",
                interaction.delivery["evidence"] if interaction.delivery else "submission-unproved",
                copy.deepcopy(interaction.effect),
            )
        return IntegratedResult("error", None)
    finally:
        if interaction is not None:
            interaction.close()
        elif owned is not None:
            try:
                owned.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            owned.close()
