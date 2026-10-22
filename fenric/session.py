"""One dialog's clock and first settlement, independent of either renderer."""

from __future__ import annotations

import os
import select
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from fenric import backend
from fenric.result import ResultError


class Session:
    """Reserve one settlement before invoking a sink; retain no submitted payload."""

    def __init__(
        self,
        timeout: float | None,
        validate: Callable[[Any], Any],
        finish: Callable[[Any], str],
        cancel: threading.Event | None = None,
        *,
        authorize: Callable[[str, str | None], dict[str, Any] | None] | None = None,
        authority_deadline: float | None = None,
        authority_expires_at_ms: int | None = None,
        review_only: bool = False,
    ) -> None:
        self.timeout = timeout
        self.started = time.monotonic()
        self._validate = validate
        self._finish = finish
        self._cancel = cancel
        self._authorize = authorize
        self._authority_deadline = authority_deadline
        self._authority_expires_at_ms = authority_expires_at_ms
        self.observation: dict[str, Any] | None = None
        self.review_only = review_only
        self.collection: str | None = None
        self.invalidated_reason: str | None = None
        self._lock = threading.Lock()
        self._touched = False
        self._reserved = False
        self._source: str | None = None
        self.outcome: backend.Outcome | None = None
        self.status: str | None = None
        self.done = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._watch, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        self._validate = lambda value: None
        self._finish = lambda value: "error"

    def touch(self) -> None:
        with self._lock:
            if not self._reserved:
                self._touched = True

    def state(self, marker: str | None = "desktop") -> dict[str, Any]:
        with self._lock:
            elapsed = time.monotonic() - self.started
            return {
                "touched": self._touched,
                "remaining": max(0, self.timeout - elapsed) if self.timeout is not None else None,
                "status": self.status,
                "settled_elsewhere": self._reserved and self._source != marker,
            }

    def submit(
        self,
        payload: Any,
        *,
        native: bool = False,
        marker: str = "desktop",
        surface: str = "local",
        subject: str | None = None,
        acknowledge_settled: bool = False,
    ) -> bool:
        try:
            value = self._validate(payload)
        except ValueError as exc:
            if self._authorize is not None:
                return False
            if not isinstance(exc, ResultError):
                raise
            if native:
                self.settle(backend.Outcome(backend.SUBMITTED, reason=f"{exc.path} {exc.problem}"))
            return False
        with self._lock:
            if self._reserved:
                # Remote retries acknowledge terminal custody, never run a sink.
                return acknowledge_settled or self._authorize is None
            if self._authorize is not None and not self._admit(surface, subject):
                return False
            self._reserved = True
            self._source = marker
        # Do not hold the lock across a subprocess or file write. The reservation
        # already owns settlement; a late cancel cannot run a second action.
        try:
            status = self._finish(value)
        except Exception:
            status = "error"
        finally:
            value = payload = None
        with self._lock:
            self.status = status
            self.collection = "answered" if status == "ok" else "error"
            self.outcome = backend.Outcome(backend.SUBMITTED)
            self.done.set()
        return True

    def settle(
        self,
        outcome: backend.Outcome,
        *,
        untouched: bool = False,
        deadline: bool = False,
        marker: str = "desktop",
        surface: str = "local",
        subject: str | None = None,
        systemic: bool = False,
    ) -> bool:
        with self._lock:
            if self._reserved:
                return False
            elapsed = time.monotonic() - self.started
            if untouched and (self._touched or self.timeout is None or elapsed < self.timeout):
                return False
            if deadline and elapsed < backend.hard_deadline(self.timeout, self._touched):
                if self._authority_deadline is None or not self.authority_expired():
                    return False
            if self._authorize is not None and not systemic and not self._admit(surface, subject):
                return False
            self._reserved = True
            self._source = marker
            self.outcome = backend.Outcome(outcome.kind, reason=outcome.reason)
            self.status = (
                "error"
                if outcome.kind == backend.SUBMITTED
                else "cancelled"
                if outcome.kind == backend.CANCELLED
                else "unavailable"
            )
            self.collection = (
                "error"
                if outcome.kind == backend.SUBMITTED
                else "cancelled"
                if outcome.kind == backend.CANCELLED
                else "expired"
                if outcome.kind == backend.EXPIRED
                else "unavailable"
            )
            self.done.set()
            return True

    def _admit(self, surface: str, subject: str | None) -> bool:
        if self.authority_expired() or (self._cancel is not None and self._cancel.is_set()):
            return False
        assert self._authorize is not None
        observation = self._authorize(surface, subject)
        if observation is None:
            return False
        self.observation = observation
        return True

    def surface_eligible(self, surface: str, subject: str | None = None) -> bool:
        """Preview qualification without recording a winning observation."""
        if self._authorize is None:
            return True
        if self.authority_expired():
            return False
        return self._authorize(surface, subject) is not None

    def authority_expired(self) -> bool:
        """An integrated authority deadline also closes a busy renderer view."""
        return (
            self._authority_deadline is not None and time.monotonic() >= self._authority_deadline
        ) or (
            self._authority_expires_at_ms is not None
            and int(time.time() * 1000) >= self._authority_expires_at_ms
        )

    def invalidate(self, collection: str) -> bool:
        """A host event is distinct from a page action; reservation already wins."""
        if collection not in {"cancelled", "expired", "superseded", "unavailable", "error"}:
            raise ValueError("invalid integrated terminal state")
        with self._lock:
            self.invalidated_reason = collection
            if self._reserved:
                return False
            self._reserved = True
            self._source = "host"
            self.collection = collection
            self.status = "cancelled" if collection == "cancelled" else "unavailable"
            self.outcome = backend.Outcome(
                backend.CANCELLED
                if collection == "cancelled"
                else backend.EXPIRED
                if collection == "expired"
                else backend.UNAVAILABLE
            )
            self.done.set()
            return True

    def expire(self, *, surface: str = "local", subject: str | None = None) -> None:
        self.settle(
            backend.Outcome(backend.EXPIRED), untouched=True, surface=surface, subject=subject
        )

    def _watch(self) -> None:
        while not self.done.is_set() and not self._stop.is_set():
            if self._cancel is not None and self._cancel.is_set():
                self.settle(backend.Outcome(backend.CANCELLED), systemic=True)
            if self._authority_deadline is not None:
                if self.authority_expired():
                    self.settle(backend.Outcome(backend.EXPIRED), systemic=True)
            else:
                self.settle(backend.Outcome(backend.EXPIRED), deadline=True, systemic=True)
            self._stop.wait(backend.POLL_SECONDS)


class CallerSignals:
    """Make SIGINT/SIGTERM observable while Cocoa owns the main thread."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self._stop = threading.Event()
        self._reader, self._writer = os.pipe()
        os.set_blocking(self._reader, False)
        os.set_blocking(self._writer, False)
        self._handlers: dict[int, Any] = {}
        self._previous_fd = -1
        self._thread: threading.Thread | None = None

    def __enter__(self) -> CallerSignals:
        self._previous_fd = signal.set_wakeup_fd(self._writer)
        for number in (signal.SIGINT, signal.SIGTERM):
            self._handlers[number] = signal.getsignal(number)
            signal.signal(number, lambda *_: None)
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def _watch(self) -> None:
        while not self._stop.is_set():
            readable, _, _ = select.select([self._reader], [], [], 0.05)
            if readable:
                for number in os.read(self._reader, 4096):
                    if number in (signal.SIGINT, signal.SIGTERM):
                        self.cancel.set()

    def __exit__(self, *args: Any) -> None:
        signal.set_wakeup_fd(self._previous_fd)
        for number, handler in self._handlers.items():
            signal.signal(number, handler)
        self._stop.set()
        try:
            os.write(self._writer, b"\0")
        except BlockingIOError:
            # A full pipe is already readable and wakes the same select.
            pass
        if self._thread:
            self._thread.join(0.2)
        os.close(self._reader)
        os.close(self._writer)
