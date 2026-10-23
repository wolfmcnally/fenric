"""The real backend, driven without a window through a stand-in web view module."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fenric import backend
from fenric.backend import (
    CANCELLED,
    EXPIRED,
    SUBMITTED,
    UNAVAILABLE,
    PywebviewBackend,
    guard_main_frame,
    hard_deadline,
)
from fenric.chrome import Page, build_page
from fenric.session import Session
from fenric.spec import validate_spec

observed: list[Any] = []


def _show(rendered: Page) -> backend.Outcome:
    observed.clear()
    shared = Session(
        rendered.timeout, lambda payload: payload, lambda value: observed.append(value) or "ok"
    )
    shared.start()
    try:
        return PywebviewBackend().show(rendered, shared)
    finally:
        shared.close()


PROJECT = Path(__file__).resolve().parents[1]
IDLE = {"touched": False, "height": 0, "done": None}


def rect(x: float, y: float, width: float, height: float) -> Any:
    return types.SimpleNamespace(
        origin=types.SimpleNamespace(x=x, y=y),
        size=types.SimpleNamespace(width=width, height=height),
    )


class Hook:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> Hook:
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in self.handlers:
            handler()


class Preferences:
    def __init__(self) -> None:
        self.changes: list[tuple[Any, str]] = []

    def setValue_forKey_(self, value: Any, key: str) -> None:  # noqa: N802
        self.changes.append((value, key))


class Window:
    """A stand-in native window that replays what the page's state would be."""

    uid = "window-1"

    def __init__(self, states: list[Any], stalled: bool = False) -> None:
        self.states = states
        self.stalled = stalled
        self.reads = 0
        self.events = types.SimpleNamespace(closed=Hook())
        self.gone = threading.Event()
        self.loaded = threading.Event()
        self.resized: list[tuple[int, int]] = []
        self.html: str | None = None
        self.syncs: list[dict[str, Any]] = []
        self.native = self
        self.native_frame = rect(100, 590, 520, 260)
        self.visible_frame = rect(0, 47, 5120, 1363)
        self.title_height = 30
        self.in_ui = False

    def screen(self) -> Any:
        assert self.in_ui
        return types.SimpleNamespace(
            visibleFrame=lambda: self.visible_frame,
            frame=lambda: rect(
                self.visible_frame.origin.x,
                self.visible_frame.origin.y - 47,
                self.visible_frame.size.width,
                self.visible_frame.size.height + 77,
            ),
        )

    def frame(self) -> Any:
        assert self.in_ui
        return self.native_frame

    def frameRectForContentRect_(self, content: Any) -> Any:  # noqa: N802
        assert self.in_ui
        return rect(0, 0, content.size.width, content.size.height + self.title_height)

    def setFrame_display_(self, frame: Any, display: bool) -> None:  # noqa: N802
        assert self.in_ui
        assert display
        self.native_frame = frame
        self.resized.append((frame.size.width, frame.size.height))

    def evaluate_js(self, script: str) -> Any:
        # Like the real library, a read waits until the page has loaded.
        assert self.loaded.wait(5)
        if script.startswith("window.fenricSync?.("):
            self.syncs.append(json.loads(script[len("window.fenricSync?.(") : -1]))
            return None
        assert script == "window.fenric"
        if self.stalled:
            # A page whose script never yields: the read does not return
            # until the window is gone.
            self.gone.wait(5)
            return None
        state = self.states[min(self.reads, len(self.states) - 1)]
        self.reads += 1
        return state

    def resize(self, width: int, height: int) -> None:
        self.resized.append((width, height))

    def destroy(self) -> None:
        self.gone.set()
        self.events.closed.fire()


class WebView:
    """Installs stand-ins for ``webview`` and its macOS platform module."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        states: list[Any],
        *,
        stalled: bool = False,
        platform: str = "darwin",
        person_closes: bool = False,
        start_fails: bool = False,
    ) -> None:
        self.window = Window(states, stalled=stalled)
        self.created: dict[str, Any] = {}
        self.preferences = Preferences()
        self.dispatched: list[Any] = []
        self.activation: list[str] = []
        fake = self

        class JSBridge:
            def userContentController_didReceiveScriptMessage_(  # noqa: N802
                self, controller: Any, message: Any
            ) -> None:
                fake.dispatched.append(message)

        class BrowserDelegate:
            def userContentController_didReceiveScriptMessage_(  # noqa: N802
                self, controller: Any, message: Any
            ) -> None:
                fake.dispatched.append(message)

        self.receiver_classes = (JSBridge, BrowserDelegate)
        view = types.SimpleNamespace(
            configuration=lambda: types.SimpleNamespace(preferences=lambda: self.preferences)
        )
        cocoa = types.ModuleType("webview.platforms.cocoa")
        cocoa.AppKit = types.SimpleNamespace(  # type: ignore[attr-defined]
            NSMakeRect=rect,
            NSScreen=types.SimpleNamespace(
                mainScreen=lambda: types.SimpleNamespace(
                    visibleFrame=lambda: rect(0, 47, 5120, 1363)
                )
            ),
        )

        def call_after(fn: Any) -> None:
            self.window.in_ui = True
            try:
                fn()
            finally:
                self.window.in_ui = False

        cocoa.AppHelper = types.SimpleNamespace(callAfter=call_after)  # type: ignore[attr-defined]

        def first_show(browser: Any) -> None:
            fake.activation.extend(["key", "activate"])

        browser_class = type(
            "BrowserView",
            (),
            {
                "JSBridge": JSBridge,
                "BrowserDelegate": BrowserDelegate,
                "instances": {Window.uid: types.SimpleNamespace(webview=view)},
                "first_show": first_show,
                "app": types.SimpleNamespace(isRunning=lambda: True),
            },
        )
        self.browser_class = browser_class
        self.original_first_show = first_show
        self.window.orderFrontRegardless = lambda: self.activation.append("order")
        self.browser = browser_class()
        self.browser.pywebview_window = self.window
        self.browser.window = self.window
        self.browser.hidden = False
        self.browser.shown = threading.Event()
        self.other_browsers: list[Any] = []
        cocoa.BrowserView = browser_class  # type: ignore[attr-defined]
        platforms = types.ModuleType("webview.platforms")
        platforms.cocoa = cocoa  # type: ignore[attr-defined]
        module = types.ModuleType("webview")
        module.platforms = platforms  # type: ignore[attr-defined]

        def create_window(title: str, **options: Any) -> Window:
            self.created = {"title": title, **options}
            return self.window

        def start() -> None:
            if platform == "darwin":
                self.browser.first_show()
                for browser in self.other_browsers:
                    browser.first_show()
            if start_fails:
                raise RuntimeError("no display")
            self.window.loaded.set()
            if person_closes:
                self.window.events.closed.fire()
            elif not self.window.gone.wait(5):
                raise AssertionError("the backend never closed the window")

        module.create_window = create_window  # type: ignore[attr-defined]
        module.start = start  # type: ignore[attr-defined]
        for name, value in (
            ("webview", module),
            ("webview.platforms", platforms),
            ("webview.platforms.cocoa", cocoa),
        ):
            monkeypatch.setitem(sys.modules, name, value)
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(backend, "POLL_SECONDS", 0.005)
        monkeypatch.setattr(backend, "_guarded_receivers", set())


def page(timeout: float | None = None) -> Page:
    return Page(title="Asker", html="<p>page</p>", timeout=timeout, width=520)


def test_opening_does_not_activate_or_disable_later_keyboard_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for start_fails in (False, True):
        fake = WebView(
            monkeypatch,
            [{**IDLE, "done": {"kind": "cancel"}}],
            start_fails=start_fails,
        )
        _show(page())
        assert fake.activation == ["order"]
        assert fake.created["on_top"] is False
        assert fake.created["resizable"] is False and "min_size" not in fake.created
        assert fake.created.get("focus", True) is True
        assert fake.browser.shown.is_set()
        assert fake.browser_class.first_show is fake.original_first_show
    for hidden in (False, True):
        fake = WebView(monkeypatch, [{**IDLE, "done": {"kind": "cancel"}}])
        fake.browser.hidden = hidden
        foreign = fake.browser_class()
        foreign.pywebview_window = object()
        foreign.window = types.SimpleNamespace(
            orderFrontRegardless=lambda: fake.activation.append("foreign order")
        )
        foreign.hidden = False
        foreign.shown = threading.Event()
        fake.other_browsers.append(foreign)
        _show(page())
        assert fake.activation == ([] if hidden else ["order"]) + ["key", "activate"]
        assert fake.browser.shown.is_set()
        assert fake.browser_class.first_show is fake.original_first_show
    fake = WebView(monkeypatch, [{**IDLE, "done": {"kind": "cancel"}}])
    fake.browser.menu = []
    fake.browser._recreate_menus = lambda menu: "fixture menu"
    fake.browser_class.app = types.SimpleNamespace(
        isRunning=lambda: False,
        setMainMenu_=lambda menu: fake.activation.append(menu),
        run=lambda: fake.activation.append("run"),
    )
    sys.modules["webview.platforms.cocoa"].AppHelper.installMachInterrupt = lambda: (
        fake.activation.append("interrupt")
    )
    _show(page())
    assert fake.activation == ["order", "fixture menu", "interrupt", "run"]
    assert fake.browser_class.first_show is fake.original_first_show


def message(main_frame: bool) -> Any:
    info = types.SimpleNamespace(isMainFrame=lambda: main_frame)
    return types.SimpleNamespace(frameInfo=lambda: info)


def test_hard_deadline_rule() -> None:
    assert hard_deadline(None, touched=False) == 300.0
    assert hard_deadline(None, touched=True) == 300.0
    assert hard_deadline(5, touched=False) == 17.0
    assert hard_deadline(5, touched=True) == 300.0
    assert hard_deadline(600, touched=True) == 612.0


def test_selected_html_window_preserves_person_chosen_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fenric.html_bundle import HtmlBundle

    rendered = replace(page(), bundle=HtmlBundle("index.html", {}))
    for platform in ("darwin", "linux"):
        states = [{**IDLE, "height": height} for height in (40, 500, 700)]
        states.append({**states[-1], "done": {"kind": "cancel"}})
        fake = WebView(monkeypatch, states, platform=platform)
        evaluate = fake.window.evaluate_js

        def person_sized(script: str) -> Any:
            if script == "window.fenric" and fake.window.reads == 1:
                fake.window.native_frame = rect(150, 200, 1000, 650)
            return evaluate(script)

        fake.window.evaluate_js = person_sized
        assert _show(rendered).kind == CANCELLED
        assert fake.created["resizable"] is True
        assert fake.created["min_size"] == (360, 320)
        assert fake.window.resized == [(520, 350)]
        if platform == "darwin":
            current = fake.window.native_frame
            assert (
                current.origin.x,
                current.origin.y,
                current.size.width,
                current.size.height,
            ) == (
                150,
                200,
                1000,
                650,
            )
        # On the portable branch, the single resize-call assertion owns persistence.
        assert observed == []


def test_show_returns_what_the_page_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"action": "ok", "values": {"q0": "answer"}}
    sized = {**IDLE, "height": 300}
    fake = WebView(
        monkeypatch,
        [None, sized, {**sized, "done": {"kind": "submit", "payload": payload}}],
    )
    outcome = _show(page())
    assert (outcome.kind, outcome.payload) == (SUBMITTED, None)
    assert observed == [payload]
    assert fake.created["title"] == "Asker" and fake.created["html"] == "<p>page</p>"
    assert "js_api" not in fake.created
    assert fake.window.resized == [(520, 330)]
    # Nothing the window still references holds the payload or the window.
    sessions = {handler.__self__ for handler in fake.window.events.closed.handlers}
    assert len(sessions) == 1
    assert all(
        session._session.outcome.payload is None and session._window is None for session in sessions
    )
    accepted = []

    def validate(value):
        if value != payload:
            raise ValueError("not accepted")
        return value

    shared = Session(
        None,
        validate,
        lambda value: accepted.append(value) or "ok",
        authorize=lambda surface, subject: {"surface": surface},
    )
    fake = WebView(
        monkeypatch,
        [
            {**IDLE, "done": {"kind": "submit", "payload": {}}},
            {**IDLE, "done": {"kind": "submit", "payload": payload}},
        ],
    )
    fake.window.loaded.set()
    native = backend._Session(page(), fake.window, shared)
    native._read()
    assert not shared.done.is_set() and not accepted
    assert fake.window.syncs[-1].get("rejected") is True
    native._read()
    assert shared.done.is_set() and accepted == [payload]
    assert fake.window.syncs[-1]["status"] == "ok"
    entered, release = threading.Event(), threading.Event()

    def pending_sink(value):
        entered.set()
        release.wait(1)
        return "ok"

    shared = Session(
        None, validate, pending_sink, authorize=lambda surface, subject: {"surface": surface}
    )
    worker = threading.Thread(
        target=lambda: shared.submit(payload, marker="remote-marker", surface="remote"), daemon=True
    )
    worker.start()
    try:
        assert entered.wait(1)
        fake = WebView(monkeypatch, [{**IDLE, "done": {"kind": "submit", "payload": payload}}])
        fake.window.loaded.set()
        native = backend._Session(page(), fake.window, shared)
        native._read()
        waiting = fake.window.syncs[-1]
    finally:
        release.set()
        worker.join(1)
    assert not worker.is_alive()
    assert waiting["status"] is None and waiting["settled_elsewhere"] is True
    assert "rejected" not in waiting, "A losing view was told to retry a reserved answer"


def test_closing_the_window_is_a_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    WebView(monkeypatch, [IDLE], person_closes=True)
    assert _show(page()).kind == CANCELLED
    shared = Session(
        None,
        lambda payload: payload,
        lambda value: "ok",
        authorize=lambda surface, subject: None,
        review_only=True,
    )
    fake = WebView(
        monkeypatch,
        [{**IDLE, "done": {"kind": "submit", "payload": {}}}, {**IDLE, "done": {"kind": "cancel"}}],
    )
    fake.window.loaded.set()
    native = backend._Session(page(), fake.window, shared)
    native._read()
    assert not shared.done.is_set() and not fake.window.gone.is_set()
    native._read()
    assert fake.window.gone.is_set() and not shared.done.is_set()
    WebView(monkeypatch, [{**IDLE, "done": {"kind": "cancel"}}])
    assert _show(page()).kind == CANCELLED


def test_a_window_that_cannot_start_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    WebView(monkeypatch, [IDLE], start_fails=True)
    outcome = _show(page())
    assert (outcome.kind, outcome.reason) == (UNAVAILABLE, "render failure")


def test_the_safety_net_expires_an_unanswered_window(monkeypatch: pytest.MonkeyPatch) -> None:
    WebView(monkeypatch, [IDLE])
    monkeypatch.setattr(backend, "SAFETY_NET_SECONDS", 0.03)
    started = time.monotonic()
    assert _show(page(timeout=0.02)).kind == EXPIRED
    assert time.monotonic() - started < 2
    WebView(monkeypatch, [{**IDLE, "done": {"kind": "expire"}}])
    monkeypatch.setattr(backend, "SAFETY_NET_SECONDS", 60)
    assert _show(page(timeout=0.001)).kind == EXPIRED


def test_a_stalled_page_cannot_outlive_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = WebView(monkeypatch, [IDLE], stalled=True)
    monkeypatch.setattr(backend, "SAFETY_NET_SECONDS", 0.05)
    started = time.monotonic()
    assert _show(page(timeout=0.05)).kind == EXPIRED
    assert time.monotonic() - started < 2
    assert fake.window.reads == 0
    shared = Session(
        None,
        lambda payload: payload,
        lambda value: "ok",
        authorize=lambda surface, subject: None,
        authority_deadline=time.monotonic() - 1,
    )
    fake = WebView(monkeypatch, [IDLE], stalled=True)
    native = backend._Session(page(), fake.window, shared)
    watcher = threading.Thread(target=native.watch, daemon=True)
    watcher.start()
    closed = fake.window.gone.wait(0.2)
    native.closed()
    watcher.join(0.2)
    assert not watcher.is_alive()
    assert closed and not shared.done.is_set()


def test_a_touched_window_outlives_the_ring(monkeypatch: pytest.MonkeyPatch) -> None:
    touched = {"touched": True, "height": 0, "done": {"kind": "expire"}}
    WebView(monkeypatch, [touched])
    monkeypatch.setattr(backend, "SAFETY_NET_SECONDS", 0.01)
    monkeypatch.setattr(backend, "HARD_LIMIT_SECONDS", 0.4)
    started = time.monotonic()
    assert _show(page(timeout=0.01)).kind == EXPIRED
    assert time.monotonic() - started >= 0.4


def test_bridge_messages_are_heard_from_the_main_frame_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heard: list[Any] = []
    guarded = guard_main_frame(lambda receiver, controller, msg: heard.append(msg))
    inner, outer = message(main_frame=False), message(main_frame=True)
    guarded(None, None, inner)
    guarded(None, None, outer)
    assert heard == [outer]

    fake = WebView(monkeypatch, [{**IDLE, "done": {"kind": "cancel"}}])
    _show(page())
    for receiver_class in fake.receiver_classes:
        receiver = receiver_class()
        receiver.userContentController_didReceiveScriptMessage_(None, inner)
        receiver.userContentController_didReceiveScriptMessage_(None, outer)
    assert fake.dispatched == [outer, outer]


def test_charts_reach_native_renderer_without_permission_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = build_page(validate_spec({"verb": "chart", "code": "ignored"}), "draw();")
    fake = WebView(monkeypatch, [{**IDLE, "done": {"kind": "cancel"}}])
    assert _show(built).kind == CANCELLED
    assert fake.created["html"] == built.html
    assert fake.preferences.changes == []


def test_charts_are_offered_on_supported_renderer_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = build_page(validate_spec({"verb": "chart", "code": "ignored"}), "draw();")
    for platform in ("darwin", "linux", "win32"):
        fake = WebView(monkeypatch, [{**IDLE, "done": {"kind": "cancel"}}], platform=platform)
        assert _show(built).kind == CANCELLED
        assert fake.created["html"] == built.html and fake.preferences.changes == []


def test_a_headless_request_never_imports_the_web_view() -> None:
    program = (
        "import sys\n"
        "class Refuse:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'webview' or name.startswith('webview.'):\n"
        "            raise SystemExit('the web view was imported')\n"
        "sys.meta_path.insert(0, Refuse())\n"
        "from fenric.cli import main\n"
        "code = main(['notify', '--message', 'hello'])\n"
        "assert 'webview' not in sys.modules\n"
        "sys.exit(code)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=PROJECT,
        env={"FENRIC_HEADLESS": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (completed.returncode, completed.stdout) == (4, "unavailable: headless\n")
    assert completed.stderr == "fenric: remote: unavailable: headless\n"


@pytest.mark.parametrize(
    ("initial", "visible", "content", "decoration", "expected"),
    [
        ((100, 590, 520, 260), (0, 47, 5120, 1363), 900, 30, (100, 47, 520, 930)),
        ((140, 500, 520, 260), (0, 47, 1440, 823), 300, 44, (140, 416, 520, 344)),
        ((-1500, 400, 520, 500), (-1920, 40, 1920, 1010), 120, 30, (-1500, 750, 520, 150)),
        ((2000, -300, 520, 260), (1920, -900, 1280, 800), 900, 30, (2000, -900, 520, 800)),
        ((700, 100, 520, 260), (100, 80, 400, 600), 900, 30, (100, 80, 400, 600)),
        ((100, 590, 520, 260), (0, 47, 5120, 1363), (900, 120), 30, (100, 827, 520, 150)),
    ],
)
def test_native_resize_uses_current_display_usable_outer_frame(
    monkeypatch: pytest.MonkeyPatch,
    initial: tuple[int, ...],
    visible: tuple[int, ...],
    content: int | tuple[int, ...],
    decoration: int,
    expected: tuple[int, ...],
) -> None:
    heights = [content] if isinstance(content, int) else list(content)
    states = [{**IDLE, "height": height} for height in heights]
    states.append({**states[-1], "done": {"kind": "cancel"}})
    fake = WebView(monkeypatch, states)
    fake.window.native_frame = rect(*initial)
    fake.window.visible_frame = rect(*visible)
    fake.window.title_height = decoration
    assert _show(page()).kind == CANCELLED
    frame = fake.window.native_frame
    assert (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height) == expected
