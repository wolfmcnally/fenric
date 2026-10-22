"""Native rendering reads validated state from the trusted main frame.

Chart pages use ordinary browser networking in an opaque generated frame.
The macOS bridge/print receiver guard remains; no native permissions are
added. A separate watcher enforces the shared deadline independently of JS.
Tests use a scripted web view double and never open a real window.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from fenric.chrome import Page

if TYPE_CHECKING:
    from fenric.session import Session

HARD_LIMIT_SECONDS = 300.0
SAFETY_NET_SECONDS = 12.0
_MIN_HEIGHT, _MAX_HEIGHT, _INITIAL_HEIGHT = 120, 900, 260
_TITLE_BAR_ALLOWANCE = 30
POLL_SECONDS = 0.1
_STATE_SCRIPT = "window.fenric"

SUBMITTED, CANCELLED, EXPIRED, UNAVAILABLE = "submitted", "cancelled", "expired", "unavailable"
RENDER_FAILURE = "render failure"


@dataclass(frozen=True)
class Outcome:
    """What happened to one window."""

    kind: str
    payload: Any = None
    reason: str | None = None


class Backend(Protocol):
    """Shows one page and blocks until the window is done."""

    def show(self, page: Page, session: Session) -> Outcome: ...


def hard_deadline(timeout: float | None, touched: bool) -> float:
    """Return how many seconds after opening a window must be closed.

    Untouched with a timeout, the page's ring expires the dialog at
    ``timeout``; this limit is the safety net behind it. Once the person has
    interacted, or when no timeout was given, the silent hard limit applies.
    """
    if timeout is None:
        return HARD_LIMIT_SECONDS
    if not touched:
        return timeout + SAFETY_NET_SECONDS
    return max(HARD_LIMIT_SECONDS, timeout + SAFETY_NET_SECONDS)


def guard_main_frame(receive: Callable[[Any, Any, Any], None]) -> Callable[[Any, Any, Any], None]:
    """Wrap a script-message receiver so that only the main frame is heard.

    The web view hands every frame the same message handler. The trusted
    chrome is the main frame; generated content is always a sub-frame, and a
    message from one is dropped before anything dispatches it.
    """

    def guarded(receiver: Any, controller: Any, message: Any) -> None:
        if message.frameInfo().isMainFrame():
            receive(receiver, controller, message)

    return guarded


_guarded_receivers: set[int] = set()


def _resize_window(window: Any, width: int, content_height: int) -> None:
    """Fit content inside the current macOS work area, without recentering."""
    if sys.platform != "darwin":
        window.resize(width, content_height + _TITLE_BAR_ALLOWANCE)
        return
    from webview.platforms import cocoa

    def fit() -> None:
        native = window.native
        screen = native.screen() or cocoa.AppKit.NSScreen.mainScreen()
        if screen is None:
            return
        visible = screen.visibleFrame()
        current = native.frame()
        frame = native.frameRectForContentRect_(
            cocoa.AppKit.NSMakeRect(0, 0, width, content_height)
        )
        frame.size.width = min(frame.size.width, visible.size.width)
        frame.size.height = min(frame.size.height, visible.size.height)
        # Keep the existing top edge until the new outer frame needs a move.
        frame.origin.x = min(
            max(current.origin.x, visible.origin.x),
            visible.origin.x + visible.size.width - frame.size.width,
        )
        frame.origin.y = min(
            max(current.origin.y + current.size.height - frame.size.height, visible.origin.y),
            visible.origin.y + visible.size.height - frame.size.height,
        )
        native.setFrame_display_(frame, True)

    # All frame/screen queries and the combined size/move happen on Cocoa's UI thread.
    cocoa.AppHelper.callAfter(fit)


def _guard_bridge() -> None:
    """Install the main-frame guard on every macOS message receiver, once.

    The web view library registers two script-message handlers: its bridge,
    and its browser delegate, which opens the native print panel on request.
    """
    from webview.platforms import cocoa

    for receiver in (cocoa.BrowserView.JSBridge, cocoa.BrowserView.BrowserDelegate):
        if id(receiver) not in _guarded_receivers:
            receiver.userContentController_didReceiveScriptMessage_ = guard_main_frame(
                receiver.userContentController_didReceiveScriptMessage_
            )
            _guarded_receivers.add(id(receiver))


def _start_without_activation(webview: Any, window: Any) -> None:
    """Show our Cocoa window without taking the active application's keys.

    pywebview's focus=False also prevents later deliberate keyboard use. Its
    Cocoa first_show instead needs just the initial key/activation calls
    omitted. Keep the normal key-capable window and event loop, and scope
    this replacement to our instance and this start call only.
    """
    from webview.platforms import cocoa

    view_class = cocoa.BrowserView
    original = view_class.first_show

    def first_show(view: Any) -> None:
        if view.pywebview_window is not window:
            original(view)
            return
        if not view.hidden:
            view.window.orderFrontRegardless()
        view.shown.set()
        if not view_class.app.isRunning():
            view_class.app.setMainMenu_(view._recreate_menus(view.menu))
            cocoa.AppHelper.installMachInterrupt()
            view_class.app.run()

    view_class.first_show = first_show
    try:
        webview.start()
    finally:
        view_class.first_show = original


class _Session:
    """One window's life: read the page's state, enforce the limits, close it.

    The page never calls into Python. Its script keeps one state object on
    the trusted main frame, and this session reads it from outside, so
    nothing of Fenric's is callable from the page. Reading and enforcing the
    limits run on separate daemon threads: a read blocks for as long as the
    page's script is busy, so a page that never yields (a chart that loops
    forever) stalls only the reader, and the deadline still closes the
    window. A window that vanishes mid-read cannot keep the process alive.
    """

    def __init__(self, page: Page, window: Any, session: Session) -> None:
        self._page = page
        self._window = window
        self._session = session
        self._closed = threading.Event()
        self._height = 0

    def closed(self) -> None:
        """The window is gone, by an outcome here or by the person's hand."""
        self._closed.set()

    def read(self) -> None:
        """Poll the page's state until an outcome is recorded or the window is gone."""
        while not self._closed.is_set() and not self._session.done.is_set():
            self._read()
            self._closed.wait(POLL_SECONDS)

    def watch(self) -> None:
        """Enforce the deadline and close the window; never waits on the page."""
        while not self._closed.is_set():
            if self._session.done.is_set() or self._session.authority_expired():
                self._window.destroy()
                return
            self._closed.wait(POLL_SECONDS)

    def release(self) -> Outcome | None:
        """Release the native window without retaining its payload."""
        self._window = None
        return self._session.outcome

    def _read(self) -> None:
        try:
            state = self._window.evaluate_js(_STATE_SCRIPT)
        except Exception:
            return
        if not isinstance(state, dict):
            return
        if state.get("touched") is True:
            self._session.touch()
        height = state.get("height")
        if isinstance(height, int) and not isinstance(height, bool) and height != self._height:
            self._height = height
            wanted = min(_MAX_HEIGHT, max(_MIN_HEIGHT, height))
            _resize_window(self._window, self._page.width, wanted)
        done = state.get("done")
        kind = done.get("kind") if isinstance(done, dict) else None
        rejected = False
        if kind == "submit":
            rejected = not self._session.submit(done.get("payload"), native=True)
        elif kind == "cancel":
            if self._session.review_only:
                self._window.destroy()
            else:
                self._settle(Outcome(CANCELLED))
        elif kind == "expire":
            self._session.expire()

        try:
            shared_state = self._session.state()
            if (
                rejected
                and not self._session.done.is_set()
                and not shared_state["settled_elsewhere"]
            ):
                shared_state["rejected"] = True
            shared = json.dumps(shared_state, allow_nan=False)
            self._window.evaluate_js(f"window.fenricSync?.({shared})")
        except Exception:
            pass

    def _settle(self, outcome: Outcome) -> None:
        self._session.settle(outcome)


class PywebviewBackend:
    """Shows a page in a native window."""

    def show(self, page: Page, session: Session) -> Outcome:
        macos = sys.platform == "darwin"
        try:
            import webview

            if macos:
                _guard_bridge()
            window = webview.create_window(
                page.title,
                html=page.html,
                width=page.width,
                height=_INITIAL_HEIGHT,
                resizable=False,
                on_top=False,
            )
            native = _Session(page, window, session)
            window.events.closed += native.closed
            for task in (native.read, native.watch):
                threading.Thread(target=task, daemon=True).start()
            if macos:
                _start_without_activation(webview, window)
            else:
                webview.start()
        except Exception:
            return Outcome(UNAVAILABLE, reason=RENDER_FAILURE)
        # The window is gone. With no recorded outcome, the person closed it.
        return native.release() or Outcome(CANCELLED)


def default_backend() -> Backend:
    """Return the backend a real request uses."""
    return PywebviewBackend()
