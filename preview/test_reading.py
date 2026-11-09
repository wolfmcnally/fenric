"""Selection, copying and local-only speech through trusted user gestures."""

from pathlib import Path

import pytest
from harness import open_dialog
from playwright.sync_api import expect


def speech_double(page, local=True):
    page.evaluate(
        """local => {
      window.spoken=[]; window.cancels=0;
      window.SpeechSynthesisUtterance=class { constructor(text){this.text=text;} };
      Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{
        getVoices:()=>[{name:'remote',lang:'en-US',localService:false},
          ...(local?[{name:'device',lang:'en-US',localService:true}]:[])],
        speak:u=>{window.spoken.push(u);u.onstart();}, cancel:()=>window.cancels++
      }});
    }""",
        local,
    )


@pytest.mark.parametrize("width", [760, 390])
def test_static_text_selection_copy_and_speech_exclude_answers(browser, width):
    dialog = open_dialog(
        browser,
        {
            "verb": "ask",
            "title": "Proposal",
            "message": "A long proposal. " * 40,
            "questions": [{"id": "notes", "prompt": "Your comments", "multiline": True}],
        },
    )
    try:
        page = dialog.page
        page.set_viewport_size({"width": width, "height": 844})
        page.locator("textarea").fill("PRIVATE TYPED ANSWER")
        assert (
            page.locator(".message").evaluate("e=>getComputedStyle(e).webkitUserSelect") == "text"
        )
        page.locator("[data-read-select]").click()
        text = page.evaluate("getSelection().toString()")
        assert "Proposal" in text and "Your comments" in text
        assert "PRIVATE" not in text and "Speak" not in text
        page.evaluate(
            "Object.defineProperty(navigator,'clipboard',{configurable:true,"
            "value:{writeText:async text=>window.copied=text}})"
        )
        page.locator("[data-read-copy]").click()
        expect(page.locator("[data-read-status]")).to_have_text("Copied displayed text.")
        assert page.evaluate("window.copied") == text
        speech_double(page)
        # Programmatic caller events cannot start speech.
        page.locator("[data-read-speak]").evaluate("e=>e.click()")
        assert page.evaluate("window.spoken.length") == 0
        page.locator("[data-read-speak]").click()
        assert page.evaluate("window.spoken[0].voice.localService") is True
        # Exercise bounded sequential chunks and exact complete speech payload.
        for _ in range(20):
            if page.locator("[data-read-stop]").is_hidden():
                break
            page.evaluate("window.spoken.at(-1).onend()")
        assert page.evaluate("window.spoken.map(u=>u.text).join('')") == text
        expect(page.locator("[data-read-status]")).to_have_text("Finished speaking.")
        page.locator("[data-read-speak]").click()
        page.locator("[data-read-stop]").click()
        assert page.evaluate("window.cancels") == 1
        page.locator("[data-read-speak]").click()
        page.get_by_role("button", name="OK", exact=True).click()
        assert page.evaluate("window.cancels") == 2
        assert dialog.done()["kind"] == "submit"
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    finally:
        dialog.close()


def test_no_local_voice_no_automatic_fallback_and_truthful_copy_failure(browser):
    dialog = open_dialog(browser, {"verb": "notify", "message": "Private proposal"})
    try:
        page = dialog.page
        speech_double(page, local=False)
        page.locator("[data-read-speak]").click()
        assert page.evaluate("window.spoken") == []
        expect(page.locator("[data-read-status]")).to_contain_text("No remote voice")
        page.evaluate(
            "Object.defineProperty(navigator,'clipboard',{configurable:true,"
            "value:{writeText:async()=>{throw Error('no')}}})"
        )
        page.locator("[data-read-copy]").click()
        expect(page.locator("[data-read-status]")).to_contain_text("Copy unavailable")
        assert page.evaluate("getSelection().toString()") == "Private proposal"
        assert dialog.done() is None
    finally:
        dialog.close()


def test_markdown_reads_only_active_document_and_keeps_frames_opaque(browser, tmp_path: Path):
    files = [tmp_path / "one.md", tmp_path / "two.md"]
    files[0].write_text("# First\n\nA **readable** proposal.\n")
    files[1].write_text("# Second\n\nOther proposal.\n")
    dialog = open_dialog(browser, {"verb": "markdown", "files": [str(p) for p in files]})
    try:
        page = dialog.page
        speech_double(page)
        page.locator("[data-read-speak]").click()
        spoken = page.evaluate("window.spoken[0].text")
        assert "readable" in spoken and "**" not in spoken and "Second" not in spoken
        page.get_by_role("tab").nth(1).click()
        assert page.evaluate("window.cancels") == 1
        page.locator("[data-read-select]").click()
        selected = page.evaluate("getSelection().toString()")
        assert "Second" in selected and "First" not in selected
        assert page.locator("iframe").first.get_attribute("sandbox") == ""
        assert page.locator("iframe").first.evaluate("e=>e.contentDocument===null")
        assert dialog.done() is None
    finally:
        dialog.close()


@pytest.mark.parametrize("key", ["ArrowLeft", "ArrowRight", "Home", "End"])
def test_keyboard_document_transition_stops_speech(browser, tmp_path: Path, key):
    files = [tmp_path / "a.md", tmp_path / "b.md"]
    for document in files:
        document.write_text(document.name + " old document text " * 40)
    dialog = open_dialog(browser, {"verb": "markdown", "files": [str(p) for p in files]})
    try:
        page = dialog.page
        speech_double(page)
        page.get_by_role("tab").nth(0 if key in ("ArrowRight", "End") else 1).click()
        page.locator("[data-read-speak]").click()
        page.evaluate("() => {window.oldEnd=window.spoken[0].onend;}")
        page.locator('[role="tab"][aria-selected="true"]').focus()
        page.keyboard.press(key)
        assert page.evaluate("window.cancels") == 1
        page.evaluate("window.oldEnd()")
        assert page.evaluate("window.spoken.length") == 1
    finally:
        dialog.close()


def test_wizard_review_never_reads_mirrored_answers_and_edit_stops_speech(browser):
    dialog = open_dialog(
        browser,
        {
            "verb": "ask",
            "presentation": "wizard",
            "questions": [{"id": "notes", "prompt": "Comment on the proposal", "multiline": True}],
        },
    )
    try:
        page = dialog.page
        page.locator("textarea").fill("PRIVATE TYPED ANSWER")
        page.get_by_role("button", name="Review", exact=True).click()
        expect(page.locator(".review-answer p")).to_have_text("PRIVATE TYPED ANSWER")
        page.locator("[data-read-select]").click()
        assert "PRIVATE" not in page.evaluate("getSelection().toString()")
        page.evaluate(
            "Object.defineProperty(navigator,'clipboard',{configurable:true,"
            "value:{writeText:async text=>window.copied=text}})"
        )
        page.locator("[data-read-copy]").click()
        expect(page.locator("[data-read-status]")).to_have_text("Copied displayed text.")
        assert "PRIVATE" not in page.evaluate("window.copied")
        speech_double(page)
        page.locator("[data-read-speak]").click()
        assert "PRIVATE" not in page.evaluate("window.spoken[0].text")
        page.get_by_role("button", name="Edit: Comment on the proposal", exact=True).click()
        assert page.evaluate("window.cancels") == 1
        expect(page.locator("[data-read-stop]")).to_be_hidden()
        expect(page.locator("[data-read-preview]")).to_be_hidden()
        assert page.locator("textarea").input_value() == "PRIVATE TYPED ANSWER"
        assert dialog.done() is None
    finally:
        dialog.close()


def test_custom_projection_is_inert_and_secret_has_no_reading_controls(browser):
    dialog = open_dialog(
        browser,
        {"verb": "custom", "html": "<p>Safe &amp; <b>clear</b>.</p><script>HIDDEN</script>"},
    )
    try:
        page = dialog.page
        page.locator("[data-read-select]").click()
        assert page.evaluate("getSelection().toString()") == "Safe & clear."
        assert page.evaluate("window.HIDDEN") is None
        assert dialog.done() is None
    finally:
        dialog.close()
    secret = open_dialog(browser, {"verb": "secret", "fields": ["TOKEN"], "store": "cat"})
    try:
        assert secret.page.locator(".reading-tools").count() == 0
    finally:
        secret.close()


@pytest.mark.parametrize("ending", ["remote", "pagehide", "error", "throw"])
def test_speech_stops_and_stale_callbacks_cannot_resume(browser, ending):
    dialog = open_dialog(browser, {"verb": "notify", "message": "Long text. " * 80})
    try:
        page = dialog.page
        speech_double(page)
        page.locator("[data-read-speak]").click()
        assert page.evaluate("window.spoken.length") == 1
        # Returning the assigned callback would make Playwright invoke it now.
        page.evaluate("() => {window.oldEnd=window.spoken[0].onend;}")
        assert page.evaluate("window.spoken.length") == 1
        if ending == "remote":
            page.evaluate(
                "window.fenricSync({touched:true,remaining:null,status:'ok',settled_elsewhere:true})"
            )
        elif ending == "pagehide":
            page.evaluate("dispatchEvent(new Event('pagehide'))")
        elif ending == "error":
            page.evaluate("window.spoken[0].onerror()")
        else:
            page.evaluate(
                "window.speechSynthesis.speak=()=>{throw Error('device error')};window.oldEnd()"
            )
        assert page.evaluate("window.cancels") == 1
        page.evaluate("window.oldEnd()")
        assert page.evaluate("window.spoken.length") == 1
        assert not dialog.errors
    finally:
        dialog.close()
