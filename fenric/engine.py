"""The one request flow behind both the ``fenric`` command and the library.

Validity outranks availability. An invalid spec, a secret with no
destination, a file target that cannot be updated, or a chart that cannot be
compiled is refused before anything asks whether a window can open. Only
then does the headless switch, and after it the render backend, decide
between an answer, a cancel, and "unavailable".
"""

from __future__ import annotations

import os
import secrets
import shlex
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from fenric import backend as backends
from fenric.charts import ChartError, compile_chart
from fenric.chrome import build_page
from fenric.html_bundle import HtmlError, load_bundle
from fenric.protocol import ProtocolError
from fenric.remote import Remote, Transport
from fenric.result import validate_result
from fenric.session import Session
from fenric.spec import Spec, SpecError, validate_spec
from fenric.store import StoreError, prepare_store, run_store
from fenric.target import TargetError, TargetInfo, check_target, write_target

if TYPE_CHECKING:
    from fenric.interaction import Interaction

HEADLESS_ENV = "FENRIC_HEADLESS"
EXIT_OK, EXIT_CANCELLED, EXIT_INVALID, EXIT_UNAVAILABLE = 0, 2, 3, 4


@dataclass(frozen=True)
class Result:
    """The outcome of one request.

    ``status`` is ``ok``, ``cancelled``, ``error`` or ``unavailable``.
    ``value`` is the validated answer of a collecting verb and ``None``
    otherwise. ``detail`` is a value-free diagnostic or the reason a window
    was unavailable.
    """

    status: str
    exit_code: int
    value: Any = None
    detail: str | None = None


@dataclass(frozen=True)
class Destination:
    """Prepared non-secret metadata and the exact receiving sink."""

    metadata: dict[str, Any] | None
    names: list[str]
    target: TargetInfo | None
    arguments: tuple[tuple[str, ...], ...]


def prepare_destination(request: Spec) -> Destination:
    """Resolve once for the live engine and the command's visual preview."""
    destination = None
    arguments: tuple[tuple[str, ...], ...] = ()
    target = None
    names = (
        [entry["name"] for entry in request.fields["fields"]] if request.verb == "secret" else []
    )
    if request.into:
        target = check_target(request.into, names)
        destination = {
            "kind": "FILE",
            "host": socket.gethostname(),
            "display": str(target.path),
            "details": [
                socket.gethostname(),
                str(target.path),
                "Existing file" if target.exists else "New file",
                target.kind,
                *names,
            ],
            "generated": False,
        }
    elif request.store:
        arguments = prepare_store(request.store, names)
        destination = {
            "kind": "COMMAND",
            "host": socket.gethostname(),
            "display": request.fields.get("store_label") or request.store,
            "details": [
                socket.gethostname(),
                *(
                    f"{name}: {shlex.join(argv)}"
                    for name, argv in zip(names, arguments, strict=True)
                ),
            ],
            "generated": bool(request.fields.get("store_label")),
        }
    return Destination(destination, names, target, arguments)


def run(
    spec: object,
    *,
    backend: backends.Backend | None = None,
    fallback_file: str | os.PathLike[str] | None = None,
    desktop_only: bool = False,
    remote_status: Callable[[dict[str, Any]], None] | None = None,
    status_file: str | os.PathLike[str] | None = None,
    cancel: threading.Event | None = None,
    _interaction: Interaction | None = None,
) -> Result:
    """Show the dialog ``spec`` describes and return what the person answered.

    ``backend`` replaces the native window, for callers that render elsewhere.
    ``fallback_file``, when given, receives a plain-text rendering of the
    request whenever the result is ``unavailable``.
    """
    try:
        request = validate_spec(spec)
        chart_script = compile_chart(request.fields["code"]) if request.verb == "chart" else None
        bundle = (
            load_bundle(request.fields["root"], request.fields["entry"], request.fields["files"])
            if request.verb == "html"
            else None
        )
        prepared = prepare_destination(request)
        destination, names = prepared.metadata, prepared.names
        if _interaction is not None:
            if fallback_file is not None or status_file is not None or remote_status is not None:
                raise ProtocolError
            _interaction.prepare(request, prepared)
    except SpecError as exc:
        return _error(f"{exc.path} {exc.problem}")
    except (ChartError, TargetError, StoreError, HtmlError) as exc:
        return _error(str(exc))
    except ProtocolError:
        return _error("integrated interaction refused")
    shown = replace(request, timeout=_interaction.remaining_seconds) if _interaction else request
    context = _interaction.page_context if _interaction else None
    file_token = secrets.token_urlsafe(32) if bundle else None
    remote_file_token = secrets.token_urlsafe(32) if bundle else None
    page = build_page(
        shown,
        chart_script,
        destination=destination,
        interaction=context,
        review_only=_interaction.review_only if _interaction else False,
        bundle=bundle,
        file_token=file_token,
    )
    final: list[Result] = []

    def finish(value: Any) -> str:
        result = Result("ok", EXIT_OK, value=value if request.verb != "secret" else None)
        if request.verb == "secret":
            try:
                if _interaction is not None:
                    _interaction.before_sink(request, prepared)
                    _interaction.begin_secret(
                        [names[index] for index, secret in value if not secret]
                    )
                result, effect = _write_secret(
                    request, prepared, value, integrated=_interaction is not None
                )
                if _interaction is not None:
                    _interaction.record_effect(effect)
            except ProtocolError:
                result = _error("integrated interaction refused")
        final.append(result)
        return result.status

    session = Session(
        shown.timeout,
        (lambda payload: _interaction.validate(request, payload))
        if _interaction
        else (lambda payload: validate_result(request, payload)),
        finish,
        cancel,
        **(_interaction.session_options if _interaction else {}),
    )
    if _interaction is not None:
        _interaction.attach(session)
    remote_page = build_page(
        shown,
        chart_script,
        destination=destination,
        remote=True,
        interaction=context,
        bundle=bundle,
        file_token=remote_file_token,
        frame_url=f"/{remote_file_token}/files/{quote(bundle.entry)}" if bundle else None,
    )
    remote = Remote(
        remote_page,
        session,
        desktop_only=desktop_only,
        callback=_interaction.availability if _interaction else remote_status,
        status_file=status_file,
        private=_interaction is not None,
    )
    if os.environ.get(HEADLESS_ENV) == "1":
        if _interaction is not None:
            session.settle(backends.Outcome(backends.UNAVAILABLE, reason="headless"), systemic=True)
        remote._report("headless")
        session.close()
        return _unavailable(request, "headless", fallback_file)
    if _interaction is not None:
        if cancel is not None and cancel.is_set():
            session.invalidate("cancelled")
        _interaction.local_availability()
        if session.done.is_set():
            session.close()
            return _error("integrated interaction ended")
    session.start()
    local_transport = None
    remote_started = False
    try:
        try:
            if bundle:
                local_transport = Transport(page, session, "", "", local=True)
                page = build_page(
                    shown,
                    destination=destination,
                    interaction=context,
                    review_only=_interaction.review_only if _interaction else False,
                    bundle=bundle,
                    file_token=file_token,
                    frame_url=local_transport.origin
                    + local_transport.files_path
                    + quote(bundle.entry),
                )
            remote.start()
            remote_started = True
        except Exception:
            session.settle(
                backends.Outcome(backends.UNAVAILABLE, reason="setup failed"),
                systemic=True,
            )
            return _unavailable(request, "setup failed", fallback_file)
        try:
            outcome = (
                session.outcome or backends.Outcome(backends.UNAVAILABLE)
                if session.done.is_set()
                else (backend or backends.default_backend()).show(page, session)
            )
        except Exception:
            outcome = backends.Outcome(backends.UNAVAILABLE, reason="render failure")
        if not session.done.is_set():
            if outcome.kind == backends.SUBMITTED:
                session.submit(outcome.payload, native=True)
            else:
                session.settle(outcome, systemic=outcome.kind == backends.UNAVAILABLE)
        session.done.wait()
        outcome = session.outcome or backends.Outcome(backends.UNAVAILABLE)
        if final:
            return final[0]
        if outcome.kind == backends.CANCELLED:
            return Result("cancelled", EXIT_CANCELLED)
        if outcome.kind == backends.EXPIRED:
            return _unavailable(request, "timed out", fallback_file)
        if outcome.kind == backends.SUBMITTED:
            return _error(outcome.reason or "request could not be completed")
        return _unavailable(request, outcome.reason or "render failure", fallback_file)
    finally:
        if remote_started:
            remote.close()
        if local_transport is not None:
            local_transport.close()
        session.close()


def _write_secret(
    request: Spec,
    prepared: Destination,
    values: list[tuple[int, str]],
    *,
    integrated: bool,
) -> tuple[Result, dict[str, Any]]:
    """One sink implementation; only proved completed field writes are listed."""
    names, target, arguments = prepared.names, prepared.target, prepared.arguments
    selected = [(index, value) for index, value in values if value]
    skipped = [names[index] for index, value in values if not value]
    filled: list[str] = []
    try:
        if request.into:
            assert target is not None
            write_target(
                request.into,
                names,
                selected,
                expected_path=target.path,
                expected_kind=target.kind if integrated else None,
            )
            filled.extend(names[index] for index, _ in selected)
        else:
            for index, secret in selected:
                try:
                    run_store(arguments[index], secret)
                except StoreError as exc:
                    raise StoreError(f"fields[{index}]: {exc}") from None
                filled.append(names[index])
    except (TargetError, StoreError) as exc:
        # A custom command can write and then fail. Never infer no effect from
        # its exit status, nor retry a possibly completed target replacement.
        return _error(str(exc)), {
            "outcome": "unknown",
            "filled": filled,
            "skipped": skipped,
            "detail": "partial-write" if filled else "completion-unproved",
        }
    finally:
        selected = values = []
    return Result("ok", EXIT_OK), {
        "outcome": "completed",
        "filled": filled,
        "skipped": skipped,
        "detail": "none",
    }


def render_text(spec: Spec) -> str:
    """Render a request as plain text, for a caller's own queue."""
    fields = spec.fields
    lines = [f"{spec.app} asks ({spec.verb})"]
    for key in ("title", "message", "detail"):
        if fields.get(key):
            lines.append(str(fields[key]))
    if spec.verb == "confirm":
        lines.append(f"Answer: {fields['yes_label']} / {fields['no_label']}")
    elif spec.verb == "pick":
        lines.append("Choose several:" if fields["multiple"] else "Choose one:")
        lines.extend(_option_lines(fields["options"]))
    elif spec.verb == "ask":
        for number, question in enumerate(fields["questions"], 1):
            lines.append(f"{number}. {question['prompt']}")
            if "image" in question:
                lines.append("   (an image is attached; it cannot be shown as text)")
            condition = question.get("when")
            if condition is not None:
                source = fields["questions"][condition["question"]]
                option = source["options"][condition["option"]]["label"]
                lines.append(
                    f"   Only when question {condition['question'] + 1} is active"
                    f" and its offered option {option!r} is selected."
                )
            if question["detail"]:
                lines.append(f"   {question['detail']}")
            lines.extend(_option_lines(question["options"]))
            if question["other"]:
                lines.append("   - Something else")
    elif spec.verb == "secret":
        lines.extend(f"Secret needed: {entry['label']}" for entry in fields["fields"])
    elif spec.verb == "form":
        lines.extend(_form_lines(fields["components"]))
    elif spec.verb == "custom":
        lines.append(_plain_text(fields["html"]))
    elif spec.verb == "chart":
        lines.append("A chart was requested; it cannot be shown as text.")
    elif spec.verb == "html":
        lines.append("Selected HTML was requested; it cannot be shown as text.")
    rendered = "\n".join(lines) + "\n"
    if spec.verb == "notify" and "copy_text" in fields:
        rendered += "Text to copy:\n" + fields["copy_text"]
    return rendered


def _error(detail: str) -> Result:
    return Result("error", EXIT_INVALID, detail=detail)


def _unavailable(spec: Spec, reason: str, fallback_file: str | os.PathLike[str] | None) -> Result:
    if fallback_file is not None:
        try:
            Path(fallback_file).write_text(render_text(spec), encoding="utf-8")
        except OSError:
            reason = f"{reason}; the fallback file could not be written"
    return Result("unavailable", EXIT_UNAVAILABLE, detail=reason)


def _option_lines(options: list[dict[str, Any]]) -> list[str]:
    lines = []
    for option in options:
        suffix = f" ({option['detail']})" if option["detail"] else ""
        lines.append(f"   - {option['label']}{suffix}")
    return lines


def _form_lines(components: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for component in components:
        kind = component["type"]
        if kind in ("heading", "text"):
            lines.append(component["text"])
        elif kind == "list":
            lines.extend(f"   - {item}" for item in component["items"])
        elif kind == "card":
            if component["title"]:
                lines.append(component["title"])
            lines.extend(_form_lines(component["children"]))
        else:
            lines.append(f"[{kind}] {component['label']}")
            lines.extend(_option_lines(component.get("options", [])))
    return lines


class _TextOnly(HTMLParser):
    _SKIPPED = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED:
            self.skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self.skipping:
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def _plain_text(markup: str) -> str:
    parser = _TextOnly()
    parser.feed(markup)
    parser.close()
    return " ".join("".join(parser.parts).split())
