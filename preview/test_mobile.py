"""Phone layout and acknowledged terminal rendering in mobile WebKit."""

from __future__ import annotations

import pytest
from harness import close_proxy_context, proxy_dialog
from playwright.sync_api import Browser

from fenric.chrome import build_page
from fenric.spec import validate_spec


@pytest.mark.parametrize("width", [320, 390])
def test_mobile_copy_uses_user_gesture_and_exact_text(browser: Browser, width: int) -> None:
    payload = '\ufeff  "literal"\r\n\t<script>not executed</script>\n'
    built = build_page(
        validate_spec({"verb": "notify", "message": "Copy", "copy_text": payload}), remote=True
    )
    context = browser.new_context(
        viewport={"width": width, "height": 700}, is_mobile=True, has_touch=True
    )
    try:
        page = context.new_page()

        def respond(route):
            if route.request.resource_type == "document":
                route.fulfill(body=built.html, content_type="text/html")
            else:
                route.fulfill(
                    json={
                        "touched": True,
                        "remaining": None,
                        "status": None,
                        "settled_elsewhere": False,
                    }
                )

        page.route("https://fixture.example/**", respond)
        page.goto("https://fixture.example/copy")
        assert page.evaluate("isSecureContext")
        # Browser emulation and a stubbed writer prove gesture/arguments, not an iPhone OS paste.
        page.evaluate("""() => Object.defineProperty(navigator,'clipboard',{value:{writeText:text=>{
          window.copied=text; window.copyHadGesture=navigator.userActivation.isActive;
          return Promise.resolve();
        }}})""")
        page.locator("#copy-button").tap()
        assert page.evaluate("[window.copied,window.copyHadGesture]") == [payload, True]
        assert page.locator("#copy-status").inner_text() == "Copied"
        assert page.evaluate("window.fenric.done") is None
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("size", [(320, 568), (390, 844), (844, 390)])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("presentation", ["form", "wizard"])
def test_mobile_fit_focus_scroll_and_terminal(
    browser: Browser, size: tuple[int, int], scheme: str, presentation: str
) -> None:
    raw = {
        "verb": "ask",
        "presentation": presentation,
        "title": "Phone check",
        "message": "Surrounding form context\n" * 40,
        "timeout": 600,
        "questions": [
            {"id": "choice", "prompt": "Choose", "options": ["Long" * 80], "other": True},
            {"id": "text", "prompt": "A note", "multiline": True, "required": False},
        ],
    }
    built = build_page(validate_spec(raw), remote=True)
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
        color_scheme=scheme,
    )
    try:
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        # Use the shipped adapter; refuse the actual submit exchange, not a timer proxy.
        def respond(route):
            if route.request.resource_type == "document":
                route.fulfill(status=200, content_type="text/html", body=built.html)
            elif (
                route.request.post_data_json
                and route.request.post_data_json.get("kind") == "submit"
            ):
                route.abort()
            else:
                route.fulfill(
                    json={
                        "touched": True,
                        "remaining": None,
                        "status": None,
                        "settled_elsewhere": False,
                    }
                )

        context.route("https://phone.example/**", respond)
        page.goto("https://phone.example/dialog")
        viewport = page.locator('meta[name="viewport"]').get_attribute("content")
        assert viewport == "width=device-width, initial-scale=1"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator('input[value="other"]').check()
        field = page.locator("input.other")
        field.fill("A custom answer")
        assert field.evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 16
        # Desktop mobile emulation cannot reproduce physical iPhone focus auto-zoom.
        # Computed font size and the unrestricted viewport are the falsifiable proxies.
        if presentation == "wizard":
            page.keyboard.press("Enter")
        page.locator("textarea").fill("line\n" * 30)
        metrics = page.locator("textarea").evaluate("""e => {
          const s=getComputedStyle(e); return [e.clientHeight,e.scrollHeight,
          parseFloat(s.lineHeight)*10+parseFloat(s.paddingTop)+parseFloat(s.paddingBottom)];
        }""")
        assert abs(metrics[0] - metrics[2]) <= 1 and metrics[1] > metrics[0]
        # Real IME composition remains a native manual criterion.
        assert (
            page.locator("textarea").evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 16
        )
        if presentation == "wizard":
            page.locator("[data-action=next]").click()
            assert page.locator("#wizard-review").is_visible()
        assert page.evaluate("document.documentElement.scrollHeight > innerHeight")
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        assert page.evaluate("scrollY > 0")
        with page.expect_event(
            "requestfailed",
            predicate=lambda request: (
                request.url.endswith("/action") and request.post_data_json.get("kind") == "submit"
            ),
        ):
            page.locator("[data-action=ok]").click()
        assert page.get_by_text("Sent!", exact=True).count() == 0
        assert page.evaluate("window.fenric.done.kind") == "submit"
        page.evaluate(
            "window.fenricSync({touched:true, remaining:null, "
            "status:'ok', settled_elsewhere:false})"
        )
        assert page.locator("main").inner_text() == "Sent!\n\nYou may close this tab now."
        assert page.locator("input, textarea, button").count() == 0
        assert page.locator("main").get_attribute("role") == "status"
        bounds = page.locator("main h1").bounding_box()
        assert bounds is not None and abs(bounds["x"] + bounds["width"] / 2 - size[0] / 2) < 2
        assert size[1] * 0.25 < bounds["y"] < size[1] * 0.65
        assert page.evaluate("scrollY") == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.keyboard.press("Enter")
        page.keyboard.press("Escape")
        assert page.evaluate("window.fenric.done") is None
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize(
    "status,elsewhere",
    [
        ("cancelled", False),
        ("unavailable", False),
        ("error", False),
        ("ok", True),
    ],
)
def test_mobile_terminal_does_not_claim_unsent_success(
    browser: Browser, status: str, elsewhere: bool
) -> None:
    context = browser.new_context(viewport={"width": 320, "height": 568}, is_mobile=True)
    try:
        page = context.new_page()
        page.set_content(build_page(validate_spec({"verb": "confirm", "message": "Test?"})).html)
        page.evaluate(
            "s => window.fenricSync({touched:false, remaining:null, ...s})",
            {"status": status, "settled_elsewhere": elsewhere},
        )
        expected = "Completed on another surface" if elsewhere else status
        assert page.get_by_text("Sent!", exact=True).count() == 0
        assert page.get_by_text(expected, exact=True).is_visible()
        assert page.locator("button").count() == 0
        page.keyboard.press("Enter")
        assert page.evaluate("window.fenric.done") is None
    finally:
        context.close()


@pytest.mark.parametrize("size,scheme", [((390, 844), "light"), ((844, 390), "dark")])
@pytest.mark.parametrize("presentation", ["form", "wizard"])
def test_mobile_timeout_survives_leaving_until_control_interaction(
    browser: Browser, size: tuple[int, int], scheme: str, presentation: str
) -> None:
    """Real remote clock/adapter; OS app dismissal remains a physical-device check."""
    from fenric.remote import Transport
    from fenric.result import validate_result
    from fenric.session import Session

    spec = validate_spec(
        {
            "verb": "ask",
            "presentation": presentation,
            "timeout": 1200,
            "questions": [{"id": "note", "prompt": "A note"}],
        }
    )
    session = Session(spec.timeout, lambda value: validate_result(spec, value), lambda _: "ok")
    transport = Transport(
        build_page(spec, remote=True), session, "phone.tail123.ts.net:8443", "owner@example.com"
    )
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
        color_scheme=scheme,
    )
    actions: list[str] = []
    errors: list[str] = []

    def proxy(route):
        incoming = route.request
        if incoming.method == "POST":
            actions.append(incoming.post_data_json["kind"])
        proxy_dialog(route, transport)

    context.route("**/*", proxy)
    session.start()
    url = transport.origin + transport.path
    try:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator(".ring-fill[style]").wait_for()
        before = session.state()["remaining"]
        page.touchscreen.tap(1, 1)
        page.keyboard.press("Shift")
        page.keyboard.press("Tab")
        field = page.locator("input[type=text]")
        field.focus()
        field.dispatch_event("pointerdown")
        field.dispatch_event("pointercancel")
        # These notifications are not a substitute for physical Safari backgrounding.
        page.evaluate("""() => {
          window.dispatchEvent(new Event('blur'));
          document.dispatchEvent(new Event('visibilitychange'));
          window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true}));
        }""")
        page.wait_for_timeout(250)
        assert not session.state()["touched"] and actions == []
        page.goto("about:blank")
        page.go_back()
        page.locator(".ring-fill[style]").wait_for()
        page.reload()
        page.locator(".ring-fill[style]").wait_for()
        page.close()
        page = context.new_page()
        page.goto(url)
        page.locator(".ring-fill[style]").wait_for()
        assert 0 < session.state()["remaining"] < before
        assert not session.state()["touched"] and not session.done.is_set() and actions == []
        another = context.new_page()
        another.goto(url)
        another.locator(".ring-fill[style]").wait_for()
        page.locator("input[type=text]").fill("A retained answer")
        another.get_by_role("img", name="Timeout canceled").wait_for()
        assert session.state()["touched"] and not session.done.is_set()
        assert "touch" in actions and set(actions) == {"touch"}
        page.reload()
        canceled = page.get_by_role("img", name="Timeout canceled")
        canceled.wait_for()
        fill = page.locator(".ring-fill")
        assert fill.evaluate("e => getComputedStyle(e).strokeDasharray") == "none"
        assert (
            float(fill.evaluate("e => getComputedStyle(e).strokeDashoffset").removesuffix("px"))
            == 0
        )
        # Reload still follows the existing draft reset contract, not answer persistence.
        assert page.locator("input[type=text]").input_value() == ""
        page.locator("[data-action=cancel]").click()
        page.get_by_text("cancelled", exact=True).wait_for()
        assert session.outcome.kind == "cancelled" and errors == []
    finally:
        close_proxy_context(context)
        transport.close()
        session.close()
