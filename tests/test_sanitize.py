"""The allowlist sanitizer for untrusted ``custom`` HTML."""

from __future__ import annotations

from fenric.sanitize import sanitize_html


def test_script_form_link_and_event_handler_are_removed() -> None:
    dirty = (
        "<h2>Report</h2>"
        "<script>steal()</script>"
        '<form action="https://example.test/post"><input name="x" value="v">Sign here</form>'
        '<a href="https://example.test/">a link</a>'
        '<p onclick="steal()">Fine <b>print</b></p>'
    )
    clean = sanitize_html(dirty)
    assert clean == "<h2>Report</h2>Sign herea link<p>Fine <b>print</b></p>"
    for removed in ("script", "steal", "form", "input", "href", "https://", "onclick"):
        assert removed not in clean


def test_only_inline_data_images_survive() -> None:
    inline = "data:image/png;base64,iVBORw0KGgo="
    clean = sanitize_html(
        f'<img src="{inline}" alt="dot" onerror="steal()">'
        '<img src="https://example.test/pixel.png">'
        '<img src="javascript:steal()">'
        '<img src="data:text/html;base64,PHNjcmlwdD4=">'
    )
    assert clean == f'<img src="{inline}" alt="dot">'


def test_dropped_elements_take_their_content_with_them() -> None:
    clean = sanitize_html(
        "<style>p{background:url(https://example.test/x)}</style>"
        "<iframe src='https://example.test/'>inside</iframe>"
        "<svg><script>steal()</script><text>drawn</text></svg>"
        "<p>kept &amp; &lt;escaped&gt;</p><!-- a comment -->"
    )
    assert clean == "<p>kept &amp; &lt;escaped&gt;</p>"


def test_output_is_balanced_for_unclosed_and_stray_tags() -> None:
    assert sanitize_html("<ul><li>one<li><b>two") == "<ul><li>one<li><b>two</b></li></li></ul>"
    assert sanitize_html("</div>text</p><em>x</em>") == "text<em>x</em>"
    assert sanitize_html("<p>a<br>b<hr/></p>") == "<p>a<br>b<hr></p>"
    assert sanitize_html('<td colspan="2" style="x" class="y">c</td>') == '<td colspan="2">c</td>'
