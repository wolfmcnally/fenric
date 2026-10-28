"""Synchronous caller-owned collection; coordinators receive availability only.

An embedding must already own its native callback and principal admission.
This module neither discovers Folded assignments nor acquires native custody.
"""

from __future__ import annotations

import copy
import datetime
import os
import select
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from fenric.interaction import run_integrated
from fenric.mappings import check_observation, prepare_request
from fenric.protocol import (
    ProtocolError,
    canonical,
    parse,
    read_frame,
    validate_answer,
    validate_message,
    write_frame,
)


@dataclass(frozen=True)
class Coordinator:
    """URL-only Folded v1 designation plus an independent host-owned binding.

    Folded verifies its actual launcher/owner/project/harness designation;
    native host/session/generation are retained here, not claimed from v1.
    No designation or native caller authority is created by this object.
    """

    fingerprint: str
    folded: str
    designation: str
    nexus: str
    scope: dict
    binding: tuple[str, str, str]

    def verify(self, request: dict, remaining: float) -> None:
        context = request["context"]
        if (
            len(self.fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.fingerprint)
            or not os.path.isabs(self.folded)
            or self.binding
            != (context["host"], context["session"], request["transport"]["host_generation"])
            or self.scope.get("project") != context["project"]
            or self.scope.get("harness") != context["native"]["harness"]
            or self.scope.get("operations") != ["coordinate"]
        ):
            raise ProtocolError
        result = subprocess.run(
            [
                self.folded,
                "coordinator",
                "verify",
                self.fingerprint,
                "--designation",
                self.designation,
                "--launcher",
                self.scope["launcher"],
                "--owner",
                self.scope["owner"],
                "--project",
                self.scope["project"],
                "--harness",
                self.scope["harness"],
                "--operation",
                "coordinate",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=max(0.01, min(5, remaining)),
            check=False,
        )
        value = parse(result.stdout)
        if (
            result.returncode
            or value.get("authorized") is not True
            or value.get("actor") != self.fingerprint
            or value.get("designation") != self.designation
            or value.get("nexus") != self.nexus
            or value.get("scope") != self.scope
            or datetime.datetime.fromisoformat(value["expires"]).timestamp() <= time.time()
        ):
            raise ProtocolError

    def deliver(self, url: str, remaining: float, request: dict) -> None:
        # Exact native ancestry is rechecked by Folded, not a peer response.
        payload = {
            "text": url,
            "data": {
                "schema": "fenric.host-availability.v1",
                "expires_at_ms": request["context"]["expires_at_ms"],
                "interaction_id": request["context"]["interaction_id"],
            },
        }
        result = subprocess.run(
            [
                self.folded,
                "send",
                "--to",
                self.fingerprint,
                "--current-session",
                request["context"]["session"],
                "--topic",
                "approvals/fenric",
                "--kind",
                "note",
                "--urgent",
            ],
            input=canonical(payload),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(0.01, min(5, remaining)),
            check=False,
        )
        if result.returncode:
            raise ProtocolError


class Host:
    """Retain and consume one exact response directly in its calling process.

    Reuse an instance to detect replay. Each invocation gets a private socket
    pair, never an inbound coordinator-response endpoint. No callbacks, grants
    or concrete coordinator assignment are created by constructing this host.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumed: set[str] = set()

    def collect(
        self,
        message: dict,
        *,
        coordinator: Coordinator | None = None,
        folded_present: bool = False,
        backend: Any = None,
        cancel: threading.Event | None = None,
    ) -> dict | None:
        if not self._lock.acquire(blocking=False):
            return None
        renderer = peer = None
        worker = None
        cancelled = cancel if cancel is not None else threading.Event()
        response = []
        terminal = []
        try:
            coordinator = copy.deepcopy(coordinator)
            prepared = prepare_request(message, now_ms=int(time.time() * 1000))
            request = prepared.message
            context, transport = request["context"], request["transport"]
            identifier = context["interaction_id"]
            if identifier in self._consumed or context["kind"] == "secret":
                return None
            self._consumed.add(identifier)
            seconds = (context["expires_at_ms"] - int(time.time() * 1000)) / 1000
            deadline = time.monotonic() + seconds

            def remaining() -> float:
                return min(
                    deadline - time.monotonic(),
                    (context["expires_at_ms"] - int(time.time() * 1000)) / 1000,
                )

            if folded_present != (coordinator is not None):
                return None
            if coordinator:
                coordinator.verify(request, remaining())
                if context["allowed_surfaces"] != ["remote"]:
                    return None
            elif context["allowed_surfaces"] != ["local"]:
                return None
            renderer, peer = socket.socketpair()

            def owner() -> None:
                delivered = False
                try:
                    write_frame(peer, request)
                    while remaining() > 0 and not cancelled.is_set():
                        if not select.select([peer], [], [], 0.05)[0]:
                            continue
                        frame = read_frame(peer)
                        validate_message(frame)
                        if frame["type"] == "response":
                            value = frame["response"]
                            if (
                                response
                                or value["interaction_id"] != identifier
                                or value["decision_digest"] != request["decision_digest"]
                                or value["transport"] != transport
                                or value["expires_at_ms"] != context["expires_at_ms"]
                                or remaining() <= 0
                            ):
                                raise ProtocolError
                            if value["collection"] == "answered":
                                check_observation(context, value["observation"])
                                validate_answer(value["answer"], context["response_schema"])
                                if coordinator:
                                    if not delivered:
                                        raise ProtocolError
                                    coordinator.verify(request, remaining())
                            response.append(copy.deepcopy(value))
                            common = {
                                "interaction_id": identifier,
                                "decision_digest": request["decision_digest"],
                                "transport": transport,
                                "settlement_id": value["settlement_id"],
                            }
                            write_frame(
                                peer, {"type": "worker_receipt", **common, "outcome": "received"}
                            )
                            # Collection is not native delivery or action completion.
                            write_frame(
                                peer,
                                {
                                    "type": "delivery_disposition",
                                    **common,
                                    "native": context["native"],
                                    "outcome": "unknown",
                                    "evidence": "submission-unproved",
                                },
                            )
                        else:
                            if (
                                frame["interaction_id"] != identifier
                                or frame["transport"] != transport
                            ):
                                raise ProtocolError
                            if frame["type"] == "terminal":
                                if frame["state"] != "closed":
                                    raise ProtocolError
                                terminal.append(True)
                                return
                            if frame["type"] != "availability":
                                raise ProtocolError
                            if coordinator and frame["surface"] == "remote":
                                if delivered or frame["state"] != "available":
                                    raise ProtocolError
                                coordinator.verify(request, remaining())
                                coordinator.deliver(
                                    frame["protected_capability"], remaining(), request
                                )
                                delivered = True
                    raise ProtocolError
                except Exception:
                    cancelled.set()

            worker = threading.Thread(target=owner, daemon=True)
            worker.start()
            run_integrated(
                renderer, backend=backend, cancel=cancelled, desktop_only=not folded_present
            )
            worker.join(12)
            if worker.is_alive() or cancelled.is_set() or not terminal or remaining() <= 0:
                return None
            return response[0] if len(response) == 1 else None
        except Exception:
            return None
        finally:
            cancelled.set()
            for channel in (renderer, peer):
                if channel is not None:
                    channel.close()
            if worker is not None:
                worker.join(12)
            self._lock.release()
