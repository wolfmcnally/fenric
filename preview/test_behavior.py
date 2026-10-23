"""How the shipped chrome behaves in a real browser.

Each check drives a page the way a person would (clicks, typing, keys) and
reads the outcome from ``window.fenric``, the object the native backend reads.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from harness import Dialog, close_proxy_context, open_dialog, proxy_dialog
from playwright.sync_api import Browser, expect
from samples import SAMPLES

from fenric.chrome import build_page
from fenric.engine import prepare_destination
from fenric.result import validate_result
from fenric.spec import VERBS

Open = Callable[..., Dialog]


@pytest.fixture
def open_(browser: Browser) -> Iterator[Open]:
    opened: list[Dialog] = []

    def opener(raw: dict[str, Any], **options: Any) -> Dialog:
        dialog = open_dialog(browser, raw, **options)
        opened.append(dialog)
        return dialog

    yield opener
    for dialog in opened:
        dialog.close()


def submitted(dialog: Dialog) -> dict[str, Any]:
    done = dialog.done()
    assert done is not None and done["kind"] == "submit", done
    return done["payload"]


def test_the_samples_cover_every_verb() -> None:
    assert {sample["verb"] for sample in SAMPLES.values()} == set(VERBS)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("remote_surface", [False, True])
def test_selected_html_preserves_navigation_and_opaque_resource_boundary(
    browser: Browser, open_: Open, scheme: str, remote_surface: bool
) -> None:
    import secrets

    from fenric.html_bundle import load_bundle
    from fenric.remote import Transport
    from fenric.session import Session
    from fenric.spec import validate_spec

    raw = SAMPLES["html"]
    context = None
    transport = None
    session = None
    if remote_surface:
        spec = validate_spec(raw)
        bundle = load_bundle(spec.fields["root"], spec.fields["entry"], spec.fields["files"])
        token = secrets.token_urlsafe(32)
        built = build_page(
            spec,
            remote=True,
            bundle=bundle,
            file_token=token,
            frame_url=f"/{token}/files/index.html",
        )
        session = Session(None, lambda p: None, lambda v: "ok")
        transport = Transport(built, session, "fixture.invalid:8443", "owner@example.com")
        context = browser.new_context(color_scheme=scheme, viewport={"width": 375, "height": 800})
        context.route(
            "https://fixture.invalid:8443/**", lambda route: proxy_dialog(route, transport)
        )
        page = context.new_page()
        page.goto(transport.origin + transport.path)
    else:
        dialog = open_(raw, scheme=scheme)
        page = dialog.page
        page.set_viewport_size({"width": 375, "height": 800})
    try:
        outside = []
        page.context.route(
            "https://outside.invalid/**",
            lambda route: (outside.append(route.request.url), route.abort()),
        )
        frame = page.frame_locator("#fenric-html")
        expect(frame.locator("#data")).to_have_text("Selected presentation data")
        expect(frame.locator("h1")).to_have_css("color", "rgb(70, 130, 180)")
        assert frame.locator("#inline-image").evaluate("e=>e.naturalWidth") == 1
        assert frame.locator("#selected-image").evaluate("e=>e.naturalWidth") == 1
        assert page.evaluate("window.fenric.done") is None
        if not remote_surface:
            # The initial fit measures natural chrome + the requested frame,
            # rather than echoing the current viewport and feeding resize back.
            page.set_viewport_size({"width": 520, "height": 230})
            natural = page.evaluate("window.fenric.height")
            assert 420 < natural < 800
            page.set_viewport_size({"width": 520, "height": natural})
            assert page.locator("main").evaluate("e=>e.scrollHeight <= e.clientHeight + 1")
            frame.locator("body").evaluate(
                "body=>{const p=document.createElement('p');"
                "p.textContent='Synthetic long review content. '.repeat(1000);body.append(p)}"
            )
            page.set_viewport_size({"width": 360, "height": 290})
            small = page.locator("#fenric-html").bounding_box()
            assert small is not None and small["width"] > 250 and small["height"] >= 100
            button = page.locator("button[data-primary]")
            button.scroll_into_view_if_needed()
            bounds = button.bounding_box()
            assert bounds and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 290
            inner_scroll = frame.locator("body").evaluate(
                "()=>{const s=document.scrollingElement;s.scrollTop=60;"
                "return {height:s.scrollHeight,client:s.clientHeight,top:s.scrollTop}}"
            )
            assert inner_scroll["height"] > inner_scroll["client"] and inner_scroll["top"] > 0
            page.set_viewport_size({"width": 1000, "height": 1000})
            large = page.locator("#fenric-html").bounding_box()
            assert large and large["height"] - small["height"] > 500
            assert large["width"] - small["width"] > 600
            assert page.evaluate("window.fenric.done") is None
            assert not page.evaluate("window.fenric.touched")
            assert "touched" not in (page.locator("body").get_attribute("class") or "").split()
        frame.get_by_role("link", name="Open detail").click()
        expect(frame.locator("h1")).to_have_text("Detail page")
        assert frame.locator("body").get_attribute("data-parent") == "isolated"
        assert page.evaluate("window.fenric.done") is None
        assert frame.locator("body").evaluate(
            "()=>fetch('https://outside.invalid/nope').then(()=>false,()=>true)"
        )
        assert outside == []
        frame.get_by_role("link", name="Return to report").click()
        expect(frame.locator("#data")).to_have_text("Selected presentation data")
        assert page.evaluate("window.fenric.done") is None
    finally:
        if context:
            close_proxy_context(context)
        if transport:
            transport.close()
        if session:
            session.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_multiline_fields_fit_wrapped_content_without_moving_selection(
    open_: Open, scheme: str
) -> None:
    dialog = open_(
        {
            "verb": "ask",
            "message": "Surrounding context\n" * 50,
            "questions": [
                {"id": "text", "prompt": "Freeform", "multiline": True},
                {"id": "other", "prompt": "Independent", "multiline": True, "required": False},
            ],
        },
        scheme=scheme,
    )
    page = dialog.page
    field = page.locator("textarea").first
    metrics = """e => { const s = getComputedStyle(e); return {
        height: e.getBoundingClientRect().height, scroll: e.scrollHeight,
        client: e.clientHeight, line: parseFloat(s.lineHeight),
        extra: parseFloat(s.paddingTop)+parseFloat(s.paddingBottom)+
               parseFloat(s.borderTopWidth)+parseFloat(s.borderBottomWidth)
    }}"""
    empty = field.evaluate(metrics)
    assert abs(empty["height"] - (empty["line"] + empty["extra"])) <= 1
    for lines in (1, 10, 11, 30):
        field.fill("\n".join(["answer"] * lines))
        current = field.evaluate(metrics)
        assert abs(current["height"] - (min(lines, 10) * current["line"] + current["extra"])) <= 1
        assert (current["scroll"] > current["client"] + 1) == (lines > 10)
    assert page.locator("textarea").nth(1).evaluate(metrics)["height"] == empty["height"]
    field.fill("word " * 35)
    wide = field.evaluate(metrics)
    assert empty["height"] + wide["line"] < wide["height"] < 10 * wide["line"] + wide["extra"]
    field.evaluate("e => e.setSelectionRange(7, 12)")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page_y = page.evaluate("scrollY")
    assert page_y > 0
    field.dispatch_event("input")
    assert page.evaluate("scrollY") == page_y
    page.set_viewport_size({"width": 320, "height": 600})
    page.wait_for_function(
        "height => document.querySelector('textarea').getBoundingClientRect().height > height",
        arg=wide["height"] + wide["line"],
    )
    narrow = field.evaluate(metrics)
    assert narrow["height"] > wide["height"] + narrow["line"]
    assert field.evaluate("e => [e.selectionStart,e.selectionEnd]") == [7, 12]
    assert field.evaluate("e => e === document.activeElement")
    field.fill("x" * 400)
    assert field.evaluate(metrics)["height"] > empty["height"] + empty["line"]
    field.fill("short")
    assert field.evaluate(metrics)["height"] == empty["height"]
    assert dialog.done() is None
    assert not dialog.errors


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_copy_view_writes_only_literal_payload_and_reports_actual_completion(
    open_: Open, scheme: str
) -> None:
    payload = (
        '\ufeff  \t"straight" ‘literal curly’\r\n'
        "<script>window.BAD=true</script>\n```literal fence```\n"
    )
    dialog = open_(
        {"verb": "notify", "message": "Not copied", "copy_text": payload, "timeout": 120},
        scheme=scheme,
    )
    page = dialog.page
    page.set_viewport_size({"width": 390, "height": 600})
    view = page.locator("#copy-text")
    assert view.text_content() == payload
    assert view.evaluate("e => getComputedStyle(e).webkitUserSelect") == "text"
    assert not page.evaluate("Boolean(window.BAD)")
    # A controlled clipboard writer proves exact arguments/feedback, not OS paste.
    page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
      writeText: text => { window.copied = text; return new Promise((resolve,reject) => {
        window.copyResolve=resolve; window.copyReject=reject;
      }); }
    }})""")
    page.locator("#copy-button").click()
    assert page.evaluate("window.copied") == payload
    assert page.locator("#copy-status").inner_text() == "Copying…"
    assert page.locator("#copy-button").get_attribute("aria-disabled") == "true"
    assert dialog.done() is None and dialog.state()["touched"]
    page.evaluate("window.copyResolve()")
    assert page.locator("#copy-status").inner_text() == "Copied"
    page.locator("#copy-button").click()
    page.evaluate("window.copyReject(new Error('fixture refusal'))")
    assert "Copy unavailable" in page.locator("#copy-status").inner_text()
    assert dialog.done() is None
    # The fallback copy event must receive the exact same plain text and restore selection.
    page.evaluate("""() => {
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
      document.execCommand = () => { const event = new Event('copy',{cancelable:true});
        Object.defineProperty(event,'clipboardData',{value:{setData:(type,text)=>{
          window.copyType=type; window.fallbackCopied=text;
        }}}); document.dispatchEvent(event); return true; };
    }""")
    page.locator("#copy-button").focus()
    page.locator("#copy-button").press("Enter")
    assert page.evaluate("[window.copyType,window.fallbackCopied]") == ["text/plain", payload]
    assert page.locator("#copy-status").inner_text() == "Copied"
    assert page.evaluate("document.activeElement.id") == "copy-button"
    page.evaluate("document.execCommand = () => false")
    page.locator("#copy-button").click()
    assert "Copy unavailable" in page.locator("#copy-status").inner_text()
    assert dialog.done() is None and not dialog.errors


def test_integrated_secret_confirmation_and_context(open_: Open, tmp_path: Path) -> None:
    dialog = open_({"verb": "secret", "fields": ["TOKEN"], "into": str(tmp_path / "fixture.env")})
    context = {
        "host": "fixture-host",
        "environment": "disposable",
        "project": "sample",
        "session": "fixture-session",
        "scope": "secret-destination",
        "expires": "2026-10-14T23:00:00+00:00",
        "original": '</pre><script>throw new Error("injected")</script>' + "long-original-" * 100,
        "destination": "Exact synthetic destination " + "long-path-" * 50,
    }
    built = build_page(
        dialog.spec, destination=prepare_destination(dialog.spec).metadata, interaction=context
    )
    page = dialog.page
    page.set_viewport_size({"width": 390, "height": 800})
    page.set_content(built.html)
    assert page.locator(".interaction-context script").count() == 0
    assert page.locator(".interaction-context").inner_text().find("<script>") >= 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.fill("input[type=password]", "SYNTHETIC_BROWSER_FIXTURE")
    assert dialog.primary_disabled()
    page.click("button[data-primary]", force=True)
    assert dialog.done() is None
    page.check("[data-destination-confirmation]")
    assert not dialog.primary_disabled()
    page.click("button[data-primary]")
    assert submitted(dialog) == {
        "action": "ok",
        "values": {"f0": "SYNTHETIC_BROWSER_FIXTURE"},
        "destination_confirmed": True,
    }
    page.evaluate(
        "window.fenricSync({touched:true,remaining:null,status:null,settled_elsewhere:false,rejected:true})"
    )
    assert dialog.done() is None
    assert (
        page.locator("#submission-refusal").inner_text()
        == "Answer not accepted. Review and try again."
    )
    page.click("button[data-primary]")
    assert submitted(dialog)["destination_confirmed"] is True
    page.evaluate(
        "window.fenricSync({touched:true,remaining:null,status:'ok',settled_elsewhere:true})"
    )
    assert page.locator("input").count() == 0
    assert page.locator("h1").inner_text() == "Completed on another surface"
    assert not dialog.errors

    command = "printf " + "x" * 800 + " <script>untrusted</script>"
    command_dialog = open_(
        {
            "verb": "ask",
            "message": "Command (untrusted action text):\n" + command,
            "questions": [
                {
                    "id": "decision",
                    "prompt": "Choose what happens to this command",
                    "kind": "choice",
                    "options": ["Deny", "Approve once"],
                }
            ],
        }
    )
    context.update(technical_details=True, destination=None, expires="2026-10-16 05:00:00 UTC")
    command_page = command_dialog.page
    command_page.set_viewport_size({"width": 320, "height": 800})
    command_page.set_content(build_page(command_dialog.spec, interaction=context).html)
    assert command_page.locator(".message").inner_text().endswith(command)
    assert command_page.locator(".message script").count() == 0
    assert command_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    technical = command_page.locator("details.interaction-technical")
    assert technical.get_attribute("open") is None
    # WebKit gives nested open details a layout box even inside closed details.
    # Check rendered text, rather than Playwright's box-based visibility proxy.
    assert context["original"] not in command_page.locator("main").inner_text()
    assert "Receiving host: fixture-host" in command_page.locator("main").inner_text()
    assert "Expires: 2026-10-16 05:00:00 UTC" in command_page.locator("main").inner_text()
    summary = technical.locator(":scope > summary")
    command_page.keyboard.press("Tab")
    summary.focus()
    summary.press("Enter")
    assert technical.get_attribute("open") is not None
    assert technical.locator("pre").inner_text() == context["original"]
    assert context["original"] in command_page.locator("main").inner_text()
    assert command_dialog.done() is None
    assert command_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not command_dialog.errors


def test_integrated_review_only_actions(open_: Open) -> None:
    dialog = open_(
        {"verb": "ask", "questions": [{"id": "answer", "prompt": "Fixture"}], "timeout": 1}
    )
    context = {
        "host": "fixture",
        "environment": "disposable",
        "project": "sample",
        "session": "fixture-session",
        "scope": "structured-answer",
        "expires": "fixture expiry",
        "original": "Complete fixture question",
        "destination": None,
    }
    dialog.page.set_content(build_page(dialog.spec, interaction=context, review_only=True).html)
    assert dialog.page.locator("input,textarea,select,button[data-primary]").count() == 0
    assert "Review only" in dialog.page.locator("main").inner_text()
    dialog.page.keyboard.press("Enter")
    dialog.page.clock.run_for(2000)
    assert dialog.done() is None
    dialog.page.keyboard.press("Escape")
    assert dialog.done() == {"kind": "cancel"}
    assert not dialog.errors


def test_every_sample_renders_without_script_errors(open_: Open) -> None:
    for name, raw in SAMPLES.items():
        for scheme in ("light", "dark"):
            dialog = open_(raw, scheme=scheme)
            state = dialog.state()
            assert dialog.errors == [], (name, scheme, dialog.errors)
            assert state["height"] > 0 and state["done"] is None, (name, scheme, state)


def test_confirm_buttons_and_keys_return_their_actions(open_: Open) -> None:
    raw = {"verb": "confirm", "message": "Proceed?"}
    dialog = open_(raw)
    dialog.page.click("button[data-action=yes]")
    assert submitted(dialog) == {"action": "yes", "values": {}}
    assert validate_result(dialog.spec, submitted(dialog)) is True

    dialog = open_(raw)
    dialog.page.click("button[data-action=no]")
    assert validate_result(dialog.spec, submitted(dialog)) is False

    dialog = open_(raw)
    dialog.page.keyboard.press("Tab")
    dialog.page.locator("button[data-primary]").focus()
    dialog.page.keyboard.press("Enter")
    assert submitted(dialog)["action"] == "yes"

    dialog = open_(raw)
    dialog.page.keyboard.press("Tab")
    dialog.page.locator("button[data-action=no]").focus()
    dialog.page.keyboard.press("Enter")
    assert validate_result(dialog.spec, submitted(dialog)) is False

    dialog = open_(raw)
    dialog.page.keyboard.press("Escape")
    assert dialog.done() == {"kind": "cancel"}

    dialog = open_({"verb": "pick", "message": "m", "options": ["a", "b"]})
    dialog.page.click("button[data-action=cancel]")
    assert dialog.done() == {"kind": "cancel"}

    dialog = open_({"verb": "pick", "message": "m", "options": ["a", "b"]})
    dialog.page.keyboard.press("Tab")
    dialog.page.locator("button[data-action=cancel]").focus()
    dialog.page.keyboard.press("Enter")
    assert dialog.done() == {"kind": "cancel"}


def test_the_primary_button_waits_for_every_required_answer(open_: Open) -> None:
    dialog = open_(SAMPLES["ask"])
    page = dialog.page
    assert dialog.primary_disabled()
    # A waiting button looks unavailable, not only marked so.
    opacity = "getComputedStyle(document.querySelector('button[data-primary]')).opacity"
    assert float(page.evaluate(opacity)) < 0.6
    page.click("button[data-primary]", force=True)
    assert dialog.done() is None

    page.fill("[data-key=q0]", "Faster start")
    assert dialog.primary_disabled(), "the single choice is still unanswered"
    # Typing "Something else" chooses it.
    page.fill("[data-key='q1.other']", "Maintenance")
    assert page.is_checked("[data-key=q1] input[value=other]")
    assert not dialog.primary_disabled()
    page.fill("[data-key=q0]", "   ")
    assert dialog.primary_disabled(), "blank text does not answer a question"
    page.fill("[data-key=q0]", "Faster start")
    assert float(page.evaluate(opacity)) == 1.0
    page.check("[data-key=q2] input[value='1']")
    page.check("[data-key=q2] input[value='2']")
    page.click("button[data-primary]")
    payload = submitted(dialog)
    assert validate_result(dialog.spec, payload) == {
        "summary": "Faster start",
        "kind": "Maintenance",
        "notify": ["Sales", "Customers"],
    }

    dialog = open_(SAMPLES["form"])
    assert dialog.primary_disabled()
    dialog.page.fill("[data-key=c0]", "billing")
    assert dialog.primary_disabled(), "the required select is still empty"
    dialog.page.select_option("[data-key=c1]", "1")
    assert not dialog.primary_disabled()
    # Each checkbox is set away from its default, so a box read wrongly shows.
    dialog.page.check("[data-key=c2]")
    dialog.page.uncheck("[data-key=c3]")
    dialog.page.fill("[data-key=c4]", "Owned by the billing team")
    dialog.page.click("button[data-primary]")
    assert validate_result(dialog.spec, submitted(dialog)) == {
        "name": "billing",
        "region": "eu-west",
        "public": True,
        "backups": False,
        "notes": "Owned by the billing team",
    }

    dialog = open_(SAMPLES["pick"])
    assert dialog.primary_disabled()
    dialog.page.check("[data-key=choice] input[value='2']")
    dialog.page.click("button[data-primary]")
    assert validate_result(dialog.spec, submitted(dialog)) == "local"


def test_every_verb_hands_back_what_fenric_accepts(open_: Open) -> None:
    for name in ("notify", "pick-multiple", "secret", "custom", "chart"):
        dialog = open_(SAMPLES[name])
        if name == "pick-multiple":
            dialog.page.check("[data-key=choice] input[value='0']")
            dialog.page.check("[data-key=choice] input[value='2']")
        if name == "secret":
            dialog.page.fill("[data-key=f1]", "made-up-value")
        dialog.page.click("button[data-primary]")
        result = validate_result(dialog.spec, submitted(dialog))
        if name == "pick-multiple":
            assert result == ["Unit tests", "Lint"]
        elif name == "secret":
            assert result == [(0, ""), (1, "made-up-value")]
        else:
            assert result is None


def test_new_dialog_waits_for_deliberate_keyboard_engagement(open_: Open) -> None:
    focused = "(() => { const e = document.activeElement; return e === document.body ? null :"
    focused += (
        " [e.tagName, e.getAttribute('type'), e.dataset.key || e.dataset.action || null]; })()"
    )
    for name, raw in SAMPLES.items():
        assert open_(raw).page.evaluate(focused) is None, name
    only_choices = {
        "verb": "ask",
        "questions": [{"id": "c", "prompt": "p", "options": ["a"], "other": True}],
    }
    assert open_(only_choices).page.evaluate(focused) is None
    for scheme in ("light", "dark"):
        for raw in (SAMPLES["notify"], SAMPLES["confirm"], only_choices):
            dialog = open_(raw, scheme=scheme)
            page = dialog.page
            # Focus alone, including programmatic focus, cannot arm a response.
            page.locator("button[data-primary]").focus()
            page.locator("button[data-primary]").dispatch_event("pointerdown")
            page.locator("button[data-primary]").dispatch_event("pointercancel")
            page.keyboard.press("Space")
            page.keyboard.press("Enter")
            assert dialog.done() is None
            assert not dialog.state()["touched"]
            # Start navigation from a fresh page, rather than tabbing out of
            # the last manually focused control and into browser chrome.
            dialog = open_(raw, scheme=scheme)
            page = dialog.page
            page.keyboard.press("Tab")
            if raw is only_choices:
                page.check("input[type=radio]")
            page.locator("button[data-primary]").focus()
            page.keyboard.press("Space")
            assert submitted(dialog)["action"] in ("ok", "yes")
            assert not dialog.errors
    for key in ("Space", "Enter"):
        dialog = open_(SAMPLES["notify"])
        page = dialog.page
        page.keyboard.down(key)
        # A key already held before a deliberate pointer engagement cannot
        # turn into a response through repeat or an orphan button key-up.
        page.click("h1")
        page.locator("button[data-primary]").focus()
        page.keyboard.down(key)
        page.keyboard.up(key)
        assert dialog.done() is None
        page.keyboard.press(key)
        assert submitted(dialog)["action"] == "ok"
    first = open_(SAMPLES["confirm"])
    first.page.click(".message")
    first.page.locator("button[data-primary]").focus()
    second = open_(SAMPLES["notify"])
    # Separate headless contexts do not model OS key-window ownership.
    # Drive the native blur signal explicitly, then verify page-local state.
    first.page.evaluate("window.dispatchEvent(new Event('blur'))")
    first.page.bring_to_front()
    first.page.keyboard.press("Space")
    first.page.keyboard.press("Enter")
    assert first.done() is None and second.done() is None
    first.page.keyboard.press("Tab")
    first.page.locator("button[data-primary]").focus()
    first.page.keyboard.press("Enter")
    assert submitted(first)["action"] == "yes"
    second.page.bring_to_front()
    second.page.keyboard.press("Enter")
    assert second.done() is None
    # Escape remains cancellation, never affirmative consent.
    dialog = open_(SAMPLES["pick"])
    dialog.page.keyboard.press("Enter")
    assert dialog.done() is None
    dialog.page.keyboard.press("Escape")
    assert dialog.done() == {"kind": "cancel"}


def test_an_untouched_countdown_expires_at_its_timeout(open_: Open) -> None:
    dialog = open_({"verb": "confirm", "message": "Proceed?", "timeout": 10})
    dialog.page.clock.run_for(9_900)
    assert dialog.done() is None and not dialog.state()["touched"]
    dialog.page.clock.run_for(100)
    assert dialog.done() == {"kind": "expire"}, "expired at 10 s, not later"


@pytest.mark.parametrize(
    "activity",
    ["passive", "waiting-next", "edit", "choice", "label", "select", "control", "next", "next-key"],
)
def test_only_control_interaction_cancels_the_ring(open_: Open, activity: str) -> None:
    raw = dict(SAMPLES["form"], timeout=10)
    if activity.startswith("next") or activity == "waiting-next":
        raw = {
            "verb": "ask",
            "presentation": "wizard",
            "timeout": 10,
            "questions": [
                {"id": "note", "prompt": "A note", "required": activity == "waiting-next"}
            ],
        }
    dialog = open_(raw)
    page = dialog.page
    page.clock.run_for(2_000)
    ring = page.locator(".ring")
    initial_stroke = page.locator(".ring-fill").evaluate("e => getComputedStyle(e).stroke")
    if activity == "waiting-next":
        page.locator("[data-action=next]").click(force=True)
        assert page.get_by_text("Answer this required question before continuing.").is_visible()
        assert not dialog.state()["touched"] and dialog.done() is None
        page.clock.run_for(8_000)
        assert dialog.done() == {"kind": "expire"}
        return
    if activity == "passive":
        page.mouse.click(1, 1)
        page.locator("[data-primary]").click(force=True)
        page.keyboard.press("Shift")
        page.keyboard.press("Tab")
        page.mouse.wheel(0, 100)
        page.locator("input[type=text]").focus()
        page.locator("input[type=text]").dispatch_event("pointerdown")
        page.locator("input[type=text]").dispatch_event("pointercancel")
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        assert not dialog.state()["touched"] and dialog.done() is None
        page.clock.run_for(8_000)
        assert dialog.done() == {"kind": "expire"}
        return
    if activity == "edit":
        page.locator("input[type=text]").fill("Answer")
    elif activity == "choice":
        page.keyboard.press("Tab")
        page.locator("input[type=checkbox]").first.focus()
        page.keyboard.press("Space")
    elif activity == "label":
        page.locator("label.choice span").first.click()
    elif activity == "next-key":
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
    elif activity == "select":
        page.locator("select").select_option("1")
    elif activity == "control":
        page.locator("input[type=text]").click()
    else:
        page.locator("[data-action=next]").click()
    page.clock.run_for(20_000)
    assert dialog.state()["touched"] and dialog.done() is None
    assert ring.is_visible()
    assert ring.get_attribute("aria-label") == "Timeout canceled"
    appearance = page.locator(".ring-fill").evaluate(
        "e => ({dash:getComputedStyle(e).strokeDasharray, "
        "offset:getComputedStyle(e).strokeDashoffset, stroke:getComputedStyle(e).stroke})"
    )
    assert appearance["dash"] == "none" and float(appearance["offset"].removesuffix("px")) == 0
    assert appearance["stroke"] != initial_stroke
    assert page.get_by_role("img", name="Timeout canceled").is_visible()


def test_phone_remote_page_shares_clock_and_real_interaction(browser: Browser, tmp_path) -> None:
    """Actual shipped phone script through the authenticated production HTTP handler."""
    from fenric.chrome import build_page
    from fenric.engine import prepare_destination
    from fenric.remote import Transport
    from fenric.session import Session
    from fenric.spec import validate_spec
    from fenric.target import write_target

    for width, scheme in ((320, "light"), (390, "dark")):
        target = tmp_path / f"phone-{width}.env"
        spec = validate_spec(
            {"verb": "secret", "fields": ["TOKEN"], "into": str(target), "timeout": 2}
        )
        prepared = prepare_destination(spec)

        def sink(value):
            write_target(str(target), ["TOKEN"], value, expected_path=prepared.target.path)
            return "ok"

        session = Session(spec.timeout, lambda value: validate_result(spec, value), sink)
        session.start()
        rendered = build_page(spec, destination=prepared.metadata, remote=True)
        transport = Transport(rendered, session, "phone.tail123.ts.net:8443", "owner@example.com")
        context = browser.new_context(viewport={"width": width, "height": 720}, color_scheme=scheme)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        context.route("**/*", lambda route: proxy_dialog(route, transport))
        try:
            page.goto(transport.origin + transport.path)
            page.locator(".ring-fill[style]").wait_for()
            another = context.new_page()
            another.goto(transport.origin + transport.path)
            another.locator(".ring-fill[style]").wait_for()
            first = session.state()["remaining"]
            assert not session.state()["touched"]
            page.reload()
            page.locator(".ring-fill[style]").wait_for()
            assert 0 < session.state()["remaining"] < first and not session.state()["touched"]
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=f"/tmp/fenric-phone-{width}-{scheme}.png")
            # Enter must open destination details, never submit the optional secret form.
            page.keyboard.press("Tab")
            page.locator(".destination summary").focus()
            page.keyboard.press("Enter")
            page.locator(".destination[open]").wait_for()
            page.locator("body.touched").wait_for(state="attached")
            assert not session.done.is_set() and not target.exists()
            assert str(target.resolve()) in page.locator(".destination-details").inner_text()
            assert page.get_by_role("img", name="Timeout canceled").is_visible()
            another.locator("input[type=password]").fill("losing-phone-value")
            page.locator("input[type=password]").fill("made-up-phone-value")
            page.locator("button[data-primary]").click()
            page.get_by_text("Sent!", exact=True).wait_for()
            another.get_by_text("Completed on another surface", exact=True).wait_for()
            assert another.locator("input[type=password]").count() == 0
            assert target.read_text() == "TOKEN=made-up-phone-value\n"
            assert page.locator("input[type=password]").count() == 0
            assert errors == []
        finally:
            close_proxy_context(context)
            transport.close()
            session.close()

    # Retain the real remote adapter/HTTP boundary above for expiry, connection
    # loss and lost acknowledgements. Navigation and fetches are distinct facts.
    from threading import Event, Timer

    from fenric.remote import TERMINAL_SECONDS

    for scenario in (
        "expiry",
        "gone",
        "unreachable",
        "transient",
        "lost-ack",
        "lost-ack-teardown",
        "unknown-answer",
    ):
        spec = validate_spec(
            {
                "verb": "notify",
                "message": "Synthetic connection test",
                "copy_text": "Inert text",
                "timeout": 60,
            }
        )
        effects = []
        session = Session(
            60,
            lambda value: validate_result(spec, value),
            lambda value: effects.append(value) or "ok",
        )
        session.start()
        transport = Transport(
            build_page(spec, remote=True), session, "phone.tail123.ts.net:8443", "owner@example.com"
        )
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        page = context.new_page()
        counts = {"documents": 0, "states": 0, "failed": 0, "submits": 0, "touches": 0}
        failing = False
        failure_limit = 2
        removed = Event()
        teardown = None

        def connection(route):
            incoming = route.request
            if incoming.is_navigation_request():
                counts["documents"] += 1
            if incoming.method == "POST" and incoming.post_data_json.get("kind") == "touch":
                counts["touches"] += 1
            if removed.is_set() and not incoming.is_navigation_request():
                route.abort("connectionrefused")
                return
            if incoming.url.endswith("/state"):
                counts["states"] += 1
                if failing and scenario in {"gone", "unreachable", "transient"}:
                    if scenario != "transient" or counts["failed"] < failure_limit:
                        counts["failed"] += 1
                        if scenario == "gone":
                            route.fulfill(status=410, body="gone")
                        else:
                            route.abort("connectionrefused")
                        return
            if incoming.method == "POST" and incoming.post_data_json.get("kind") == "submit":
                counts["submits"] += 1
                if (
                    scenario == "unknown-answer"
                    or scenario in {"lost-ack", "lost-ack-teardown"}
                    and counts["submits"] == 1
                ):
                    # Deliver to the real first-settlement sink, then lose only
                    # the acknowledgement. Never manufacture a successful answer.
                    response = route.fetch(
                        url=f"http://127.0.0.1:{transport.server.server_port}"
                        + transport.path
                        + "/action",
                        headers={
                            **incoming.headers,
                            "Host": transport.authority,
                            "X-Forwarded-Host": transport.authority,
                            "X-Forwarded-Proto": "https",
                            "Tailscale-User-Login": "owner@example.com",
                        },
                    )
                    assert response.ok
                    if scenario == "lost-ack-teardown":
                        # Model the real Remote.close grace period, including
                        # removal of the actual transport, not an immortal replay server.
                        nonlocal teardown

                        def close_after_settlement():
                            removed.set()
                            transport.close()

                        teardown = Timer(TERMINAL_SECONDS, close_after_settlement)
                        teardown.start()
                    route.abort("connectionreset")
                    return
            proxy_dialog(route, transport)

        context.route("**/*", connection)
        try:
            page.goto(transport.origin + transport.path)
            page.locator(".ring-fill[style]").wait_for()
            if scenario in {"expiry", "gone", "unreachable"}:
                session.started -= 61  # test-only monotonic elapsed-time advance
                session.expire()
                assert session.done.is_set() and session.status == "unavailable"
            failing = True
            if scenario in {"lost-ack", "lost-ack-teardown", "unknown-answer"}:
                page.locator("button[data-primary]").click()
            if scenario == "transient":
                page.wait_for_timeout(2200)
                assert counts["failed"] == 2
                assert page.get_by_text("Synthetic connection test", exact=True).is_visible()
                # A fresh burst after a proven recovery must not inherit the
                # first burst's count. Removing reset makes this deterministically terminal.
                failure_limit = 4
                page.wait_for_timeout(2200)
                assert counts["failed"] == 4 and not session.done.is_set()
                assert page.get_by_text("Synthetic connection test", exact=True).is_visible()
            else:
                heading = (
                    "unavailable"
                    if scenario == "expiry"
                    else "Sent!"
                    if scenario == "lost-ack"
                    else "Connection unavailable. This view may have expired. "
                    "Any response is unconfirmed."
                )
                if scenario == "lost-ack-teardown":
                    terminal = page.locator("main.terminal h1")
                    terminal.wait_for(timeout=4000)
                    assert terminal.inner_text() in {
                        "Sent!",
                        "Connection unavailable. This view may have expired. "
                        "Any response is unconfirmed.",
                    }
                    assert session.status == "ok" and len(effects) == 1
                else:
                    page.get_by_role("heading", name=heading, exact=True).wait_for(timeout=4000)
                before = dict(counts)
                page.wait_for_timeout(350)
                assert counts == before, "terminal views must stop network activity"
                assert page.locator("button").count() == 0
                if scenario in {"gone", "unreachable"}:
                    assert counts["failed"] == 3
                if scenario == "unknown-answer":
                    assert counts["submits"] == 3 and session.status == "ok"
            assert counts["documents"] == 1, "no document reload was reproduced"
            assert len(effects) == (
                1 if scenario in {"lost-ack", "lost-ack-teardown", "unknown-answer"} else 0
            )
            if scenario in {"expiry", "gone", "unreachable", "transient"}:
                assert counts["touches"] == 0 and session.state()["touched"] is False
        finally:
            close_proxy_context(context)
            if teardown is not None:
                teardown.join(timeout=2)
                assert not teardown.is_alive(), "owned teardown must be terminal"
            transport.close()
            session.close()


@pytest.fixture
def chart_resource():
    """A real loopback CORS server; no external host is contacted by a probe."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = b'{"label":"loopback-data"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def chart_probe_code(url: str) -> str:
    import json

    from samples import CHART_CODE

    return (
        CHART_CODE
        + """
try { parent.document.body.dataset.chartReach = "bad"; }
catch (error) { root.dataset.parentDenied = error instanceof Error ? error.name : "unknown"; }
fetch("""
        + json.dumps(url)
        + """).then(response => response.json()).then(value => {
  d3.select(root).append("p").attr("id", "network-result").text(value.label);
});
"""
    )


def test_dynamic_chart_uses_browser_network_and_real_frame_input(
    open_: Open, chart_resource
) -> None:
    server, requests = chart_resource
    raw = dict(SAMPLES["chart"], timeout=10)
    raw["code"] = chart_probe_code(f"http://127.0.0.1:{server.server_port}/desktop")
    dialog = open_(raw)
    frame = dialog.page.frame_locator("#fenric-chart")
    frame.locator("#network-result").wait_for()
    assert frame.locator("#network-result").inner_text() == "loopback-data"
    assert requests == ["/desktop"]
    assert frame.locator("#root").get_attribute("data-parent-denied") == "SecurityError"
    assert dialog.page.evaluate("document.body.dataset.chartReach") is None
    assert frame.locator("rect").count() == 5 and not dialog.state()["touched"]
    frame.locator("#root").evaluate(
        '() => document.dispatchEvent(new PointerEvent("pointerdown", {bubbles:true}))'
    )
    dialog.page.set_viewport_size({"width": 390, "height": 720})
    dialog.page.locator("button[data-primary]").focus()
    dialog.page.evaluate("window.postMessage('fenric-chart-touch', '*')")
    dialog.page.clock.run_for(1_000)
    assert not dialog.state()["touched"] and dialog.done() is None
    assert dialog.page.locator(".ring").is_visible()
    frame.locator("rect").first.click()
    dialog.page.clock.run_for(400)
    assert frame.locator("rect").first.get_attribute("fill") == "rgb(255, 165, 0)"
    assert (
        dialog.state()["touched"]
        and dialog.page.get_by_role("img", name="Timeout canceled").is_visible()
    )
    dialog.page.clock.run_for(20_000)
    assert dialog.done() is None
    assert dialog.page.evaluate("document.activeElement.id") == "fenric-chart"
    dialog.page.keyboard.press("Escape")
    assert dialog.done() == {"kind": "cancel"}


def test_phone_dynamic_chart_preserves_shared_settlement(browser: Browser, chart_resource) -> None:
    """Chart behavior through production auth/HTTP routing at both phone widths."""
    import http.client
    import threading
    from urllib.parse import urlsplit

    from fenric import backend
    from fenric.charts import compile_chart
    from fenric.chrome import build_page
    from fenric.remote import Transport
    from fenric.session import Session
    from fenric.spec import validate_spec

    resource, requests = chart_resource
    code = compile_chart(chart_probe_code("https://chart-resource.test/phone"))
    for width, scheme in ((320, "light"), (390, "dark")):
        for mode in ("answer", "expire", "caller-cancel", "frame-cancel"):
            spec = validate_spec(dict(SAMPLES["chart"], timeout=2 if mode == "expire" else 30))
            cancel = threading.Event()
            settlements = []

            def finish(value):
                settlements.append(value)
                return "ok"

            session = Session(
                spec.timeout, lambda value: validate_result(spec, value), finish, cancel
            )
            rendered = build_page(spec, code, remote=True)
            transport = Transport(
                rendered, session, "phone.tail123.ts.net:8443", "owner@example.com"
            )
            context = browser.new_context(
                viewport={"width": width, "height": 720}, color_scheme=scheme
            )
            errors = []
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            before = len(requests)

            def proxy(route):
                incoming = route.request
                parsed = urlsplit(incoming.url)
                # Distinct HTTPS chart resource forwarded to its real CORS fixture.
                # This branch precedes the authenticated production-dialog proxy.
                if parsed.hostname == "chart-resource.test":
                    connection = http.client.HTTPConnection("127.0.0.1", resource.server_port)
                    connection.request("GET", parsed.path)
                else:
                    response = proxy_dialog(route, transport)
                    assert "content-security-policy" not in response.headers
                    return
                response = connection.getresponse()
                body = response.read()
                response_headers = dict(response.getheaders())
                assert "Content-Security-Policy" not in response_headers
                route.fulfill(status=response.status, headers=response_headers, body=body)
                connection.close()

            context.route("**/*", proxy)
            session.start()
            try:
                page.goto(transport.origin + transport.path)
                frame = page.frame_locator("#fenric-chart")
                frame.locator("#network-result").wait_for()
                page.locator(".ring-fill[style]").wait_for()
                assert len(requests) == before + 1 and requests[-1] == "/phone"
                assert frame.locator("#network-result").inner_text() == "loopback-data"
                assert frame.locator("#root").get_attribute("data-parent-denied") == "SecurityError"
                assert page.evaluate("document.body.dataset.chartReach") is None
                assert frame.locator("rect").count() == 5
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert frame.locator("svg").evaluate(
                    "e => e.getBoundingClientRect().width <= innerWidth"
                )
                assert not session.state()["touched"]
                if mode == "expire":
                    page.get_by_text("unavailable", exact=True).wait_for(timeout=5_000)
                    assert session.done.is_set()
                    assert session.outcome.kind == backend.EXPIRED and settlements == []
                elif mode == "caller-cancel":
                    cancel.set()
                    page.get_by_text("cancelled", exact=True).wait_for(timeout=3_000)
                    assert session.done.is_set()
                    assert session.outcome.kind == backend.CANCELLED and settlements == []
                else:
                    frame.locator("rect").first.click()
                    frame.locator("rect[fill='rgb(255, 165, 0)']").wait_for()
                    page.locator("body.touched").wait_for(state="attached")
                    assert (
                        session.state()["touched"]
                        and page.get_by_role("img", name="Timeout canceled").is_visible()
                    )
                    if mode == "frame-cancel":
                        assert page.evaluate("document.activeElement.id") == "fenric-chart"
                        page.keyboard.press("Escape")
                        page.get_by_text("cancelled", exact=True).wait_for()
                        assert session.outcome.kind == backend.CANCELLED and settlements == []
                    else:
                        page.screenshot(path=f"/tmp/fenric-chart-phone-{width}.png")
                        another = context.new_page()
                        another.goto(transport.origin + transport.path)
                        another.frame_locator("#fenric-chart").locator("rect").first.wait_for()
                        assert session.submit({"action": "ok", "values": {}}, native=True)
                        page.get_by_text("Completed on another surface", exact=True).wait_for()
                        another.get_by_text("Completed on another surface", exact=True).wait_for()
                        assert settlements == [None]
                        assert session.submit({"action": "ok", "values": {}})
                        assert settlements == [None]
                assert errors == []
            finally:
                close_proxy_context(context)
                transport.close()
                session.close()


WIZARD = {
    "verb": "ask",
    "presentation": "wizard",
    "questions": [
        {"id": "route", "prompt": "Choose a route", "options": ["a", "b"], "other": True},
        {
            "id": "areas",
            "prompt": "Choose areas",
            "kind": "multi",
            "options": ["web", "cli"],
            "when": {"question": "route", "equals": "a"},
        },
        {
            "id": "note",
            "prompt": "Describe the change",
            "multiline": True,
            "when": {"question": "areas", "includes": "web"},
        },
        {"id": "optional", "prompt": "Optional note", "required": False},
    ],
}


def test_wizard_retains_active_drafts_and_clears_cascading_branches(open_: Open) -> None:
    for scheme in ("light", "dark"):
        dialog = open_(WIZARD, scheme=scheme)
        page = dialog.page
        page.set_viewport_size({"width": 360, "height": 740})
        assert page.locator(".step-heading:visible").inner_text() == "Choose a route"
        assert page.evaluate("document.activeElement === document.body")
        page.click("[data-action=next]", force=True)
        assert page.locator("#wizard-error").inner_text() and dialog.done() is None
        page.check("[data-key=q0] input[value='0']")
        page.click("[data-action=next]")
        page.check("[data-key=q1] input[value='0']")
        page.click("[data-action=next]")
        page.fill("[data-key=q2]", "retained draft")
        page.click("[data-action=back]")
        page.click("[data-action=next]")
        assert page.input_value("[data-key=q2]") == "retained draft"
        page.click("[data-action=next]")
        page.click("[data-action=next]")
        assert page.is_visible("#wizard-review") and dialog.done() is None
        assert "retained draft" in page.inner_text("#wizard-answers")
        assert "Not answered (optional)" in page.inner_text("#wizard-answers")
        page.get_by_role("button", name="Edit: Choose a route", exact=True).click()
        page.check("[data-key=q0] input[value='1']")
        assert page.input_value("[data-key=q2]") == ""
        assert not page.is_checked("[data-key=q1] input[value='0']")
        page.check("[data-key=q0] input[value='0']")
        page.click("[data-action=next]")
        assert not page.is_checked("[data-key=q1] input[value='0']")
        page.click("[data-action=back]")
        # Other text equal to an offered id must not activate that branch.
        page.fill("[data-key='q0.other']", "a")
        page.click("[data-action=next]")
        assert page.locator(".step-heading:visible").inner_text() == "Optional note"
        page.click("[data-action=next]")
        assert page.locator("#wizard-answers .review-answer").count() == 2
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.click("[data-action=ok]")
        assert validate_result(dialog.spec, submitted(dialog)) == {"route": "a", "optional": ""}
        page.keyboard.press("Escape")
        assert dialog.done()["kind"] == "submit"
        assert dialog.errors == []


def test_wizard_keyboard_review_and_terminal_actions(open_: Open) -> None:
    raw = {
        "verb": "ask",
        "presentation": "wizard",
        "questions": [
            {"id": "first", "prompt": "First"},
            {"id": "second", "prompt": "Second", "multiline": True, "required": False},
        ],
    }
    repeated = open_(
        {
            **raw,
            "questions": [
                {"id": str(i), "prompt": f"Step {i}", "required": False} for i in range(3)
            ],
        }
    )
    repeated.page.dblclick("[data-action=next]")
    assert repeated.page.locator(".step-heading:visible").inner_text() == "Step 1"
    repeated.page.click("[data-action=next]")
    assert repeated.page.locator(".step-heading:visible").inner_text() == "Step 2"
    dialog = open_(raw)
    page = dialog.page
    page.keyboard.press("Tab")
    assert page.locator("[data-key=q0]").evaluate("e => e === document.activeElement")
    page.fill("[data-key=q0]", "hello")
    page.keyboard.press("Enter")
    assert page.locator(".step-heading:focus").inner_text() == "Second"
    assert dialog.done() is None
    page.evaluate(
        "document.dispatchEvent(new KeyboardEvent('keydown', "
        "{key:'Enter', repeat:true, bubbles:true}))"
    )
    assert page.locator(".step-heading:visible").inner_text() == "Second"
    page.locator("[data-action=back]").focus()
    page.keyboard.press("Enter")
    assert page.locator(".step-heading:visible").inner_text() == "First"
    page.click("[data-action=next]")
    page.fill("[data-key=q1]", "line")
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    assert page.input_value("[data-key=q1]") == "line\n" and dialog.done() is None
    page.click("[data-action=next]", click_count=2)
    assert page.is_visible("#wizard-review") and dialog.done() is None
    page.keyboard.press("Enter")
    assert dialog.done() is None, "review requires explicit Submit focus"
    page.locator("[data-action=ok]").dispatch_event("click", {"detail": 2})
    assert dialog.done() is None, "a second click must not cross from Review into Submit"
    page.get_by_role("button", name="Edit: First", exact=True).click()
    page.fill("[data-key=q0]", "edited")
    page.click("[data-action=next]")
    page.click("[data-action=next]")
    assert "edited" in page.inner_text("#wizard-answers")
    page.click("[data-action=ok]")
    assert validate_result(dialog.spec, submitted(dialog)) == {
        "first": "edited",
        "second": "line\n",
    }
    for action in ("cancel", "escape", "expire", "remote"):
        fresh = open_({**raw, "timeout": 1})
        if action == "cancel":
            fresh.page.click("[data-action=cancel]")
        elif action == "escape":
            fresh.page.keyboard.press("Escape")
        elif action == "expire":
            fresh.page.clock.run_for(1000)
        else:
            fresh.page.evaluate(
                "window.fenricSync({touched:false, remaining:null, "
                "status:'cancel', settled_elsewhere:true})"
            )
            fresh.page.keyboard.press("Escape")
            fresh.page.clock.run_for(2000)
            assert fresh.done() is None and fresh.page.inner_text("main") == "cancel"
            continue
        assert fresh.done() == {"kind": "expire" if action == "expire" else "cancel"}
    single = open_({**raw, "questions": raw["questions"][:1]})
    single.page.fill("[data-key=q0]", "one")
    single.page.click("[data-action=next]")
    assert single.page.is_visible("#wizard-review") and single.done() is None

    for kind, required in (("multi", True), ("choice", False)):
        question = {
            "id": "q",
            "prompt": "Pick",
            "kind": kind,
            "options": ["x"],
            "required": required,
            "other": True,
        }
        choice = open_({**raw, "questions": [question]})
        choice.page.click("[data-action=next]", force=True)
        if required:
            assert choice.page.locator("#wizard-error").inner_text()
            assert not choice.page.is_visible("#wizard-review")
            choice.page.fill("[data-key='q0.other']", "custom")
            choice.page.click("[data-action=next]")
        assert choice.page.is_visible("#wizard-review")
        choice.page.click("[data-action=ok]")
        assert validate_result(choice.spec, submitted(choice)) == {
            "q": ["custom"] if required else None
        }


def test_phone_wizard_submits_only_reviewed_active_answers(browser: Browser) -> None:
    from fenric.chrome import build_page
    from fenric.remote import Transport
    from fenric.session import Session
    from fenric.spec import validate_spec

    spec = validate_spec(WIZARD)
    answers = []

    def finish(value):
        answers.append(value)
        return "ok"

    session = Session(None, lambda value: validate_result(spec, value), finish)
    transport = Transport(
        build_page(spec, remote=True), session, "phone.tail123.ts.net:8443", "owner@example.com"
    )
    context = browser.new_context(viewport={"width": 320, "height": 720})

    context.route("**/*", lambda route: proxy_dialog(route, transport))
    session.start()
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(transport.origin + transport.path)
        another = context.new_page()
        another.goto(transport.origin + transport.path)
        another.fill("[data-key='q0.other']", "losing draft")
        page.check("[data-key=q0] input[value='0']")
        page.click("[data-action=next]")
        assert not session.done.is_set() and answers == []
        page.check("[data-key=q1] input[value='0']")
        page.click("[data-action=next]")
        page.fill("[data-key=q2]", "phone draft")
        page.click("[data-action=next]")
        page.click("[data-action=next]")
        assert page.is_visible("#wizard-review") and answers == []
        page.get_by_role("button", name="Edit: Choose a route", exact=True).click()
        page.check("[data-key=q0] input[value='1']")
        page.click("[data-action=next]")
        page.click("[data-action=next]")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path="/tmp/fenric-wizard-phone-review.png")
        page.click("[data-action=ok]")
        page.get_by_text("Sent!", exact=True).wait_for(timeout=5000)
        another.get_by_text("Completed on another surface", exact=True).wait_for(timeout=5000)
        assert another.locator("[data-key]").count() == 0
        assert answers == [{"route": "b", "optional": ""}]
        assert session.done.is_set() and page.locator("[data-key]").count() == 0
        assert errors == []
    finally:
        close_proxy_context(context)
        transport.close()
        session.close()
