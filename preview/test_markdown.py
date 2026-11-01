"""Real WebKit selected Markdown navigation, exact Copy and no external requests."""

from pathlib import Path

import pytest
from harness import open_dialog
from playwright.sync_api import expect


@pytest.mark.parametrize("mobile", [False, True])
def test_markdown_navigation_source_copy_and_inert_content(browser, tmp_path: Path, mobile):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    source = '\ufeff# First\r\n\r\n"quotes" `ticks`\t\n![x](https://bad.invalid/image)\n<script>parent.BAD=true</script>\n'
    first.write_bytes(source.encode())
    second.write_text("# Second\n\n- one\n- two\n")
    requests = []
    dialog = open_dialog(
        browser, {"verb": "markdown", "files": [str(first), str(second)]}, request_log=requests
    )
    try:
        page = dialog.page
        if mobile:
            page.set_viewport_size({"width": 390, "height": 844})
        tabs = page.get_by_role("tab")
        expect(tabs.nth(0)).to_have_attribute("aria-selected", "true")
        modes = page.get_by_role("group", name="Document view")
        assert modes.get_by_role("button").all_text_contents() == ["Rendered", "Source"]
        bounds = modes.bounding_box()
        pane = page.locator("#md-panel-0 iframe").bounding_box()
        tabstrip = page.get_by_role("tablist").bounding_box()
        assert bounds and pane and tabstrip
        assert abs(bounds["x"] + bounds["width"] - pane["x"] - pane["width"]) < 2
        assert abs(tabstrip["y"] + tabstrip["height"] - pane["y"]) < 2
        segments = modes.get_by_role("button")
        left, right = segments.nth(0).bounding_box(), segments.nth(1).bounding_box()
        assert left and right and abs(left["x"] + left["width"] - right["x"]) < 2
        # Engage through actual Tab input; programmatic focus is deliberately
        # insufficient to arm activation under the shared focus-safety policy.
        page.keyboard.press("Tab")
        expect(segments.nth(0)).to_be_focused()
        page.keyboard.press("Tab")
        expect(segments.nth(1)).to_be_focused()
        page.keyboard.press("Enter")
        expect(segments.nth(1)).to_have_attribute("aria-pressed", "true")
        page.locator("[data-md-view=source]").click()
        pre = page.locator("[data-md-panel]:not([hidden]) [data-md-source]")
        assert pre.text_content() == source
        page.evaluate(
            'Object.defineProperty(navigator,"clipboard",{configurable:true,'
            "value:{writeText:async text=>{window.copied=text}}})"
        )
        page.get_by_role("button", name="Copy source").first.click()
        expect(page.locator("[data-copy-status]").first).to_have_text("Copied")
        assert page.evaluate("window.copied") == source
        assert dialog.done() is None
        tabs.nth(0).focus()
        page.keyboard.press("ArrowRight")
        expect(tabs.nth(1)).to_be_focused()
        expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
        page.locator("[data-md-view=rendered]").click()
        frame = page.frame_locator("#md-panel-1 iframe")
        expect(frame.get_by_role("heading", name="Second")).to_be_visible()
        assert frame.locator("li").count() == 2
        tabs.nth(1).focus()
        page.keyboard.press("Home")
        expect(tabs.nth(0)).to_be_focused()
        expect(tabs.nth(0)).to_have_attribute("aria-selected", "true")
        page.keyboard.press("End")
        expect(tabs.nth(1)).to_be_focused()
        page.keyboard.press("ArrowLeft")
        expect(tabs.nth(0)).to_be_focused()
        expect(
            page.frame_locator("#md-panel-0 iframe").locator("script, img, a, input")
        ).to_have_count(0)
        assert not requests
        assert page.evaluate("window.BAD") is None
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # A successful event fallback copies exact source and restores Rendered mode.
        page.evaluate("""() => {
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
          document.execCommand = () => { const event = new Event('copy',{cancelable:true});
            Object.defineProperty(event,'clipboardData',{value:{setData:(type,text)=>{
              window.fallbackCopied=text;
            }}}); document.dispatchEvent(event); return true; };
        }""")
        page.get_by_role("button", name="Copy source").first.click()
        assert page.evaluate("window.fallbackCopied") == source
        expect(page.locator("[data-md-view=rendered]")).to_have_attribute("aria-pressed", "true")
        expect(pre).to_be_hidden()
        # Failed clipboard operation must reveal the selectable exact-source fallback.
        page.evaluate(
            'Object.defineProperty(navigator,"clipboard",{configurable:true,'
            'value:{writeText:async()=>{throw new Error("no")}}})'
        )
        page.get_by_role("button", name="Copy source").first.click()
        expect(pre).to_be_visible()
        expect(page.locator("[data-copy-status]").first).to_contain_text("Copy unavailable")
        assert pre.text_content() == source and dialog.done() is None
        page.get_by_role("button", name="Close", exact=True).click()
        assert dialog.done()["payload"] == {"action": "ok", "values": {}}
        # WebKit emits the same single sandbox diagnostic for the unchanged inert
        # custom frame under this installed Playwright clock; prove exact known count.
        warning = (
            "Blocked script execution in 'about:srcdoc' because the document's frame "
            "is sandboxed and the 'allow-scripts' permission is not set."
        )
        assert dialog.errors == [warning, warning]
    finally:
        dialog.close()


@pytest.mark.parametrize("width", [760, 390])
def test_long_rendered_document_is_keyboard_scrollable(browser, tmp_path, width):
    source = tmp_path / "long.md"
    source.write_text("# Long document\n\n" + "A paragraph for keyboard scrolling.\n\n" * 150)
    dialog = open_dialog(browser, {"verb": "markdown", "files": [str(source)]})
    try:
        page = dialog.page
        page.clock.resume()
        page.set_viewport_size({"width": width, "height": 800})
        page.get_by_role("tab").click()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.tagName") == "IFRAME"
        frame = page.frames[1]
        for _ in range(3):
            if frame.evaluate("document.activeElement.tagName") == "MAIN":
                break
            page.keyboard.press("Tab")
        assert frame.evaluate("document.activeElement.tagName") == "MAIN"
        assert frame.locator("main").evaluate("e => e.scrollTop") == 0
        page.keyboard.press("PageDown")
        # Poll native scroll state externally: in-frame waitForFunction is blocked by
        # this scriptless sandbox, while direct browser-observed geometry progresses.
        import time

        deadline = time.monotonic() + 2
        while (
            frame.locator("main").evaluate("e => e.scrollTop") == 0 and time.monotonic() < deadline
        ):
            time.sleep(0.025)
        assert frame.locator("main").evaluate("e => e.scrollTop") > 0
        assert dialog.done() is None
    finally:
        dialog.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_document_tabs_scroll_without_mobile_page_overflow(browser, tmp_path, scheme):
    files = []
    for i in range(8):
        document = tmp_path / (f"Document-{i}-" + "long-name-" * 8 + ".md")
        document.write_text(f"# Document {i}\n")
        files.append(str(document))
    dialog = open_dialog(browser, {"verb": "markdown", "files": files})
    try:
        page = dialog.page
        page.set_viewport_size({"width": 320, "height": 640})
        page.emulate_media(color_scheme=scheme)
        tabs = page.get_by_role("tab")
        tabs.first.click()
        page.keyboard.press("End")
        expect(tabs.last).to_be_focused()
        expect(tabs.last).to_have_attribute("aria-selected", "true")
        for selected in [tabs.last, page.locator('[data-md-view][aria-pressed="true"]')]:
            assert "inset" in selected.evaluate("e => getComputedStyle(e).boxShadow")
        assert page.get_by_role("tablist").evaluate("e => e.scrollLeft > 0")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.frame_locator("#md-panel-7 iframe").get_by_role("heading")).to_have_text(
            "Document 7"
        )
        assert dialog.done() is None
    finally:
        dialog.close()
