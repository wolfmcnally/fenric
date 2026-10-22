"""Page construction: trusted markup, the inner frames, and their policies."""

from __future__ import annotations

import base64
import hashlib
import html
import re
from typing import Any

from fenric.chrome import GENERATED_BADGE, build_page, outer_policy
from fenric.spec import validate_spec

HOSTILE = '<img src=x onerror=alert(1)>"&SENTINEL'


def page_for(**spec: Any) -> str:
    return build_page(validate_spec(spec), "/*chart*/" if spec["verb"] == "chart" else None).html


def policy_of(document: str) -> str:
    found = re.findall(r'http-equiv="Content-Security-Policy" content="([^"]*)"', document)
    assert len(found) == 1
    return html.unescape(found[0])


def frame_of(document: str) -> tuple[str, str]:
    """Return an inner frame's sandbox value and its unescaped document."""
    found = re.findall(
        r'<iframe id="fenric-(?:custom|chart)" sandbox="([^"]*)" height="\d+" srcdoc="([^"]*)">',
        document,
    )
    assert len(found) == 1
    return found[0][0], html.unescape(found[0][1])


def badged(document: str) -> bool:
    """The frame sits in a marked area whose badge is trusted chrome, outside the frame."""
    opening = f'<div class="generated"><span class="badge">{GENERATED_BADGE}</span><iframe '
    outside = re.sub(r'srcdoc="[^"]*"', "", document)
    return outside.count(opening) == 1 and outside.count("<iframe") == 1


def sha256(code: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'"


def scripts_of(document: str) -> list[str]:
    return re.findall(r"<script>(.*?)</script>", document, re.S)


def test_spec_strings_are_escaped_in_trusted_markup() -> None:
    document = page_for(
        verb="ask",
        app=HOSTILE,
        title=HOSTILE,
        message=HOSTILE,
        questions=[
            {
                "id": HOSTILE,
                "prompt": HOSTILE,
                "detail": HOSTILE,
                "options": [{"id": HOSTILE, "label": HOSTILE, "detail": HOSTILE}],
            }
        ],
    )
    assert "<img" not in document
    assert document.count(html.escape(HOSTILE, quote=True)) == 8
    assert "SENTINEL" not in "".join(scripts_of(document))
    payload = '\ufeff\r\n<script>window.BAD = true</script>\t"literal"\n'
    copy_page = page_for(verb="notify", message="Copy only payload", copy_text=payload)
    encoded = re.search(r'data-copy-base64="([^"]+)"', copy_page)
    assert encoded is not None
    assert base64.b64decode(encoded[1]).decode("utf-8") == payload
    assert "window.BAD" not in copy_page
    assert 'id="copy-button"' in copy_page and 'aria-live="polite"' in copy_page


def test_custom_content_sits_sanitized_in_an_inert_frame() -> None:
    document = page_for(verb="custom", html='<p onclick="x()">Hi</p><script>SENTINEL()</script>')
    sandbox, inner = frame_of(document)
    assert sandbox == ""
    assert (
        policy_of(inner)
        == "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:"
    )
    assert "script-src 'none'" in policy_of(inner) and "default-src 'none'" in policy_of(inner)
    assert "<body><p>Hi</p></body>" in inner
    assert "SENTINEL" not in document and "onclick" not in document
    assert badged(document)


def test_chart_runs_with_normal_browser_policy_and_opaque_chrome_boundary() -> None:
    for remote in (False, True):
        page = build_page(
            validate_spec({"verb": "chart", "code": "ignored", "data": [1]}),
            "draw(data);",
            remote=remote,
        )
        sandbox, inner = frame_of(page.html)
        assert sandbox == "allow-scripts" and "allow-same-origin" not in sandbox
        scripts = scripts_of(inner)
        assert len(scripts) == 3 and scripts[2] == "draw(data);"
        assert "Content-Security-Policy" not in page.html
        assert 'id="fenric-chart"' in page.html and badged(page.html)


def test_outer_policy_lists_exactly_the_scripts_the_page_embeds() -> None:
    plain = build_page(validate_spec({"verb": "notify", "message": "m"}))
    outer = re.sub(r'srcdoc="[^"]*"', "", plain.html)
    (chrome_script,) = scripts_of(outer)
    assert policy_of(outer) == outer_policy([sha256(chrome_script).strip("'")[7:]])
    assert policy_of(outer) == (
        f"default-src 'none'; script-src {sha256(chrome_script)} 'unsafe-eval'; "
        "style-src 'unsafe-inline'; img-src data:; frame-src 'none'"
    )

    remote = build_page(validate_spec({"verb": "notify", "message": "m"}), remote=True)
    policy = policy_of(remote.html)
    assert "'unsafe-eval'" not in policy and "connect-src 'self'" in policy
    assert len(re.findall(r"'sha256-[^']+'", policy)) == 2


def test_chart_data_is_never_concatenated_into_code() -> None:
    data = {"note": "</script><script>SENTINEL()</script>", "n": 3}
    page = build_page(validate_spec({"verb": "chart", "code": "ignored", "data": data}), "draw();")
    _, inner = frame_of(page.html)
    assert all("SENTINEL" not in code for code in scripts_of(inner))
    (carrier,) = re.findall(
        r'<script type="application/json" id="fenric-data">(.*?)</script>', inner, re.S
    )
    assert "<" not in carrier and "SENTINEL" in carrier
    assert inner.count("SENTINEL") == 1


def test_timeout_adds_the_ring_and_the_asker_is_labelled() -> None:
    with_ring = page_for(verb="confirm", message="m", app="Deploy bot", timeout=5)
    assert '<body data-timeout="5">' in with_ring
    assert '<circle class="ring-fill"' in with_ring
    assert '<span class="app">Deploy bot</span>' in with_ring
    assert "<title>Deploy bot</title>" in with_ring
    without = page_for(verb="confirm", message="m")
    assert "data-timeout" not in without and '<circle class="ring-fill"' not in without
    assert '<span class="app">Fenric</span>' in without


def test_secret_fields_are_masked_and_carry_no_values() -> None:
    document = page_for(
        verb="secret", fields=[{"name": "api", "label": "API key"}, "db"], store="keep {name}"
    )
    masked = re.findall(r"<input [^>]*>", document)
    assert len(masked) == 2
    for field in masked:
        assert 'type="password"' in field and 'autocomplete="off"' in field
        assert 'spellcheck="false"' in field and "value=" not in field
    assert "keep {name}" not in document


def test_an_other_question_shows_an_always_visible_text_box() -> None:
    document = page_for(
        verb="ask",
        questions=[
            {
                "id": "color",
                "prompt": "Color?",
                "options": [{"label": "Red", "detail": "warm"}],
                "other": True,
            }
        ],
    )
    assert '<fieldset data-key="q0" data-kind="radio" data-required>' in document
    assert '<input type="radio" name="q0" value="other">' in document
    assert 'data-key="q0.other" data-kind="text" data-other-for="q0"' in document
    assert '<span class="detail">warm</span>' in document
    assert "Something else" in document


def test_the_page_references_no_external_resource() -> None:
    for spec in (
        {"verb": "notify", "message": "m"},
        {"verb": "custom", "html": '<img src="https://example.test/x.png"><a href="//x">l</a>'},
        {"verb": "chart", "code": "ignored"},
    ):
        document = page_for(**spec)
        outer = re.sub(r'srcdoc="[^"]*"', "", document)
        inner = frame_of(document)[1] if "<iframe" in document else ""
        markup = outer + re.sub(r"<script\b.*?</script>", "", inner, flags=re.S)
        assert not re.search(r"<[a-z]+\b[^>]*\s(src|href|action)=", markup)
        assert "example.test" not in document
