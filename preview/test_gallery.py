"""Paged screenshot comments through the shipped remote adapter and real session."""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import pytest
from harness import close_proxy_context, open_dialog, proxy_dialog
from playwright.sync_api import Browser, expect

from fenric.chrome import build_page
from fenric.gallery import load_gallery
from fenric.remote import Transport
from fenric.result import validate_result
from fenric.session import Session
from fenric.spec import validate_spec

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


@pytest.mark.parametrize(
    "size,scheme", [((320, 568), "light"), ((390, 844), "dark"), ((844, 390), "light")]
)
def test_gallery_mobile_comments(browser: Browser, size, scheme):
    raw = load_gallery(EXAMPLES / "image-comments.json")
    raw["questions"][0]["required"] = True
    spec = validate_spec(raw)
    results = []
    session = Session(
        600,
        lambda payload: validate_result(spec, payload),
        lambda value: results.append(value) or "ok",
    )
    transport = Transport(
        build_page(spec, remote=True), session, "gallery.example:444", "owner@example.com"
    )
    session.start()
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        is_mobile=True,
        has_touch=True,
        color_scheme=scheme,
    )
    try:
        context.route(
            "https://gallery.example:444/**", lambda route: proxy_dialog(route, transport)
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(transport.origin + transport.path)
        image = page.locator('[data-question="0"] .question-image')
        expect(image).to_have_attribute("data-image-state", "ready")
        assert image.evaluate("e => [e.naturalWidth, e.naturalHeight]") == [320, 960]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("document.activeElement === document.body")
        assert page.locator("[data-action=next]").get_attribute("aria-disabled") == "true"
        page.locator("[data-action=next]").click(force=True)
        assert page.locator('[data-question="0"]').is_visible()
        field = page.locator("#q0")
        assert float(field.evaluate("e => getComputedStyle(e).fontSize").replace("px", "")) >= 16
        field.fill("Mobile: enlarge the button")
        field.press("Enter")
        field.type("Keep this note")
        assert page.locator('[data-question="0"]').is_visible()
        page.locator("[data-image-zoom]:visible").click()
        assert page.locator("[data-image-zoom]:visible").get_attribute("aria-pressed") == "true"
        assert page.locator(".image-viewport:visible").evaluate(
            "e => e.scrollHeight > e.clientHeight"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("[data-action=next]").click()
        expect(page.locator('[data-question="1"] .question-image')).to_have_attribute(
            "data-image-state", "ready"
        )
        assert page.locator(".step-heading:visible").inner_text() == "Wide layout"
        page.locator("[data-image-zoom]:visible").click()
        assert page.locator(".image-viewport:visible").evaluate(
            "e => e.scrollWidth > e.clientWidth"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#q1").fill("Wide: good spacing")
        page.locator("[data-action=back]").click()
        assert page.locator("#q0").input_value() == "Mobile: enlarge the button\nKeep this note"
        page.locator("[data-action=next]").dblclick()
        assert page.locator('[data-question="1"]').is_visible()
        assert page.locator("#q1").input_value() == "Wide: good spacing"
        page.locator("[data-action=next]").click()
        expect(page.locator("#wizard-review")).to_be_visible()
        rows = page.locator(".review-answer")
        assert rows.count() == 2
        assert rows.locator(".badge").all_text_contents() == ["Caller image", "Caller image"]
        assert rows.nth(0).locator("h3").inner_text() == "Mobile layout"
        assert rows.nth(0).locator("img").get_attribute("alt") == "Mobile layout"
        assert "enlarge the button" in rows.nth(0).inner_text()
        assert "Wide: good spacing" in rows.nth(1).inner_text()
        page.get_by_role("button", name="Edit: Wide layout", exact=True).click()
        page.locator("#q1").fill("")
        page.locator("[data-action=next]").click()
        assert "Not answered (optional)" in rows.nth(1).inner_text()
        page.locator("#wizard-review h2").focus()
        page.keyboard.press("Enter")
        assert results == []
        page.locator("[data-action=ok]").dblclick()
        expect(page.get_by_text("Sent!", exact=True)).to_be_visible()
        assert results == [{"mobile": "Mobile: enlarge the button\nKeep this note", "wide": ""}]
        assert errors == []
    finally:
        close_proxy_context(context)
        transport.close()
        session.close()


@pytest.mark.parametrize(
    "ending", ["cancel", "escape", "close", "decode", "decode-form", "zoom-form"]
)
def test_gallery_failure_and_cancellation(browser: Browser, ending):
    raw = load_gallery(EXAMPLES / "image-comments.json")
    if ending.startswith("decode"):
        data = base64.b64decode(raw["questions"][0]["image"]["data"].split(",")[1])
        invalid_pixels = b"not compressed pixels"
        chunk = b"IDAT" + invalid_pixels
        data = (
            data[:33]
            + struct.pack(">I", len(invalid_pixels))
            + chunk
            + struct.pack(">I", zlib.crc32(chunk))
            + data[-12:]
        )
        raw["questions"][0]["image"]["data"] = (
            "data:image/png;base64," + base64.b64encode(data).decode()
        )
    if ending in ("decode-form", "zoom-form"):
        raw["presentation"] = "form"
    dialog = open_dialog(browser, raw)
    try:
        page = dialog.page
        if ending.startswith("decode"):
            expect(page.locator(".image-error").first).to_be_visible()
            expect(page.locator(".image-error").first).to_contain_text("could not be displayed")
            page.locator("#q0").fill("Must not submit an unseen image")
            action = "ok" if ending == "decode-form" else "next"
            page.locator(f"[data-action={action}]").click(force=True)
            if ending == "decode":
                assert page.locator('[data-question="0"]').is_visible()
            assert dialog.done() is None
        elif ending == "zoom-form":
            expect(page.locator(".question-image").first).to_have_attribute(
                "data-image-state", "ready"
            )
            zoom = page.locator("[data-image-zoom]").first
            page.keyboard.press("Tab")
            zoom.focus()
            page.keyboard.press("Enter")
            assert dialog.done() is None
            expect(zoom).to_have_attribute("aria-pressed", "true")
            expect(zoom).to_have_text("Natural size")
            page.keyboard.press("Enter")
            assert dialog.done() is None
            expect(zoom).to_have_attribute("aria-pressed", "false")
        elif ending == "close":
            page.locator("#q0").fill("Unsubmitted draft")
            assert dialog.done() is None
            page.close()
        else:
            page.locator("#q0").fill("Unsubmitted draft")
            if ending == "escape":
                page.keyboard.press("Escape")
            else:
                page.get_by_role("button", name="Cancel", exact=True).click()
            assert dialog.done() == {"kind": "cancel"}
    finally:
        dialog.close()
