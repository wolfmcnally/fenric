"""Allowlist sanitizer for the untrusted HTML the ``custom`` verb shows.

Anything not on the allowlist is removed. Elements that can carry active or
external content are dropped with everything inside them; every other
unknown element is unwrapped to its text. Only a few inert attributes
survive, and an image survives only when its source is an inline ``data:``
image.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

_ALLOWED = frozenset(
    "abbr b blockquote br caption code dd div dl dt em figcaption figure "
    "h1 h2 h3 h4 h5 h6 hr i img kbd li mark ol p pre s small span strong sub sup "
    "table tbody td tfoot th thead tr u ul".split()
)
_VOID = frozenset({"br", "hr", "img"})
_DROPPED_WITH_CONTENT = frozenset(
    "script style iframe frame frameset object embed applet template noscript "
    "svg math audio video canvas head title select textarea button".split()
)
_ATTRIBUTES = {
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "img": {"alt", "width", "height"},
    "abbr": {"title"},
}
_DATA_IMAGE_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/gif;base64,",
    "data:image/webp;base64,",
)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.dropping: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.dropping:
            if tag in _DROPPED_WITH_CONTENT:
                self.dropping.append(tag)
            return
        if tag in _DROPPED_WITH_CONTENT:
            self.dropping.append(tag)
            return
        if tag not in _ALLOWED:
            return
        kept = _kept_attributes(tag, attrs)
        if tag == "img" and not any(name == "src" for name, _ in kept):
            return
        rendered = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in kept)
        self.out.append(f"<{tag}{rendered}>")
        if tag not in _VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.dropping:
            if tag == self.dropping[-1]:
                self.dropping.pop()
            return
        if tag not in self.open:
            return
        while self.open:
            closing = self.open.pop()
            self.out.append(f"</{closing}>")
            if closing == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(escape(data, quote=False))


def _kept_attributes(tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    allowed = _ATTRIBUTES.get(tag, set())
    kept: list[tuple[str, str]] = []
    for name, value in attrs:
        if value is None:
            continue
        if tag == "img" and name == "src":
            if value.strip().lower().startswith(_DATA_IMAGE_PREFIXES):
                kept.append((name, value.strip()))
        elif name in allowed:
            kept.append((name, value))
    return kept


def sanitize_html(markup: str) -> str:
    """Return ``markup`` reduced to the allowlist, with every tag balanced."""
    parser = _Sanitizer()
    parser.feed(markup)
    parser.close()
    while parser.open:
        parser.out.append(f"</{parser.open.pop()}>")
    return "".join(parser.out)
