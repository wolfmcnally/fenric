"""Build the one inline, asset-free page a dialog is rendered from.

The outer page is Fenric's own chrome: every string from the spec is escaped
into markup Fenric authors, and its script holds the only bridge to Python.
Untrusted content never enters that markup. ``custom`` HTML and ``chart``
code live in separate inner frames: custom stays policy-restricted, charts
use ordinary browser networking. Both sit inside a
bordered area whose badge marks them as generated.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from html import escape
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from fenric.html_bundle import HtmlBundle
from fenric.images import MAX_COMMENT_LENGTH
from fenric.sanitize import sanitize_html
from fenric.spec import Spec

GENERATED_BADGE = "Generated content"
OTHER_LABEL = "Something else"
CHART_BOOTSTRAP = (
    'globalThis.data = JSON.parse(document.getElementById("fenric-data").textContent);'
    'globalThis.root = document.getElementById("root");'
    'for(const name of ["pointerdown","keydown","input","wheel"])'
    "document.addEventListener(name,event=>{if(event.isTrusted)"
    'parent.postMessage("fenric-chart-touch","*");},{capture:true});'
    'document.addEventListener("keydown",event=>{if(event.isTrusted&&event.key==="Escape")'
    '{event.preventDefault();parent.postMessage("fenric-chart-cancel","*");}},true);'
)
_FRAME_STYLE = (
    ":root{color-scheme:light dark}"
    "html,body{margin:0}"
    "body{padding:10px 12px;font:14px/1.45 -apple-system,BlinkMacSystemFont,"
    '"Segoe UI",system-ui,sans-serif}'
    "table{border-collapse:collapse}td,th{padding:3px 8px;border:1px solid #8884}"
    "img,svg{max-width:100%}pre{white-space:pre-wrap}"
)
_NARROW, _WIDE = 520, 760


@dataclass(frozen=True)
class Page:
    """Everything a backend needs to show one dialog."""

    title: str
    html: str
    timeout: float | None
    width: int
    bundle: HtmlBundle | None = None
    file_token: str | None = None


def outer_policy(
    script_hashes: list[str], *, remote: bool = False, frame_source: str = "'none'"
) -> str:
    """Return the content policy for the trusted outer page.

    Non-chart pages use this restricted policy; chart pages emit no CSP.
    The inert custom srcdoc inherits this policy and refuses navigation.
    ``'unsafe-eval'`` is what the desktop web view library's bridge needs.
    """
    scripts = " ".join(f"'sha256-{digest}'" for digest in script_hashes)
    bridge = "" if remote else " 'unsafe-eval'"
    connection = "; connect-src 'self'" if remote else ""
    return (
        f"default-src 'none'; script-src {scripts}{bridge}; "
        f"style-src 'unsafe-inline'; img-src data:; frame-src {frame_source}" + connection
    )


def build_page(
    spec: Spec,
    chart_script: str | None = None,
    *,
    destination: dict[str, Any] | None = None,
    remote: bool = False,
    interaction: dict[str, Any] | None = None,
    review_only: bool = False,
    bundle: HtmlBundle | None = None,
    file_token: str | None = None,
    frame_url: str | None = None,
) -> Page:
    """Build the page for ``spec``. A chart needs its already compiled script."""
    fields = spec.fields
    body, actions = _BUILDERS[spec.verb](spec, frame_url if spec.verb == "html" else chart_script)
    parts = ['<header class="asker">', f'<span class="app">{_e(spec.app)}</span>']
    if spec.timeout is not None:
        parts.append(
            '<svg class="ring" viewBox="0 0 20 20" role="img" aria-label="Time remaining">'
            "<title>Time remaining</title>"
            '<circle class="ring-track" cx="10" cy="10" r="8"/>'
            '<circle class="ring-fill" cx="10" cy="10" r="8"/></svg>'
        )
    parts.append("</header>")
    if fields.get("title"):
        parts.append(f"<h1>{_e(fields['title'])}</h1>")
    if fields.get("message"):
        parts.append(f'<p class="message">{_e(fields["message"])}</p>')
    if fields.get("detail"):
        parts.append(f'<p class="detail">{_e(fields["detail"])}</p>')
    if interaction is not None:
        technical = interaction.get("technical_details", False)
        heading = "Receiving context" if technical else "Requesting host"
        parts.append(f'<section class="interaction-context"><h2>{heading}</h2>')
        if technical:
            parts.append(f"<p><strong>Receiving host:</strong> {_e(interaction['host'])}</p>")
            parts.append(f"<p><strong>Expires:</strong> {_e(interaction['expires'])}</p>")
            parts.append(
                '<details class="interaction-technical"><summary>'
                "Technical details — complete original request</summary>"
            )
        keys = (
            ("environment", "project", "session", "scope")
            if technical
            else ("host", "environment", "project", "session", "scope", "expires")
        )
        for key in keys:
            parts.append(f"<p><strong>{_e(key)}:</strong> {_e(interaction[key])}</p>")
        parts.append('<p class="detail">The response deadline still applies after interaction.</p>')
        parts.append("<details open><summary>Complete original request</summary><pre>")
        parts.append(_e(interaction["original"]))
        parts.append("</pre></details>")
        if interaction.get("destination") is not None:
            parts.append("<details open><summary>Exact receiving destination</summary><pre>")
            parts.append(_e(interaction["destination"]))
            parts.append("</pre></details>")
        if technical:
            parts.append("</details>")
        parts.append("</section>")
    if destination is not None:
        generated = ' class="generated-description"' if destination["generated"] else ""
        display = (
            f"“{destination['display']}”" if destination["generated"] else destination["display"]
        )
        parts.append(
            '<details class="destination"><summary>'
            f'<span class="destination-badge">{_e(destination["kind"])}</span> '
            f'<span class="destination-host">{_e(destination["host"])}</span> '
            f'<span{generated}>{_e(display)}</span></summary><div class="destination-details">'
            + "".join(f"<div>{_e(line)}</div>" for line in destination["details"])
            + "</div></details>"
        )
    if review_only:
        body = '<p role="status">Review only. Answer using an admitted surface.</p>'
        actions = _button("cancel", "Close")
    parts.append(body)
    if interaction is not None and spec.verb == "secret" and not review_only:
        parts.append(
            '<label class="choice"><input type="checkbox" data-destination-confirmation>'
            "<span>Confirm this receiving host and exact destination.</span></label>"
        )
    parts.append(f'<footer class="actions">{actions}</footer>')
    timeout = "" if spec.timeout is None else f' data-timeout="{spec.timeout:g}"'
    script = _inline_script(_asset("_assets/chrome.js"))
    remote_script = _inline_script(_asset("_assets/remote.js")) if remote else ""
    hashes = [_script_hash(script)]
    if remote:
        hashes.append(_script_hash(remote_script))
    source = "'none'"
    if spec.verb == "html":
        source = "'self'"
        if not remote:
            location = urlsplit(frame_url or "")
            source = (
                f"{location.scheme}://{location.netloc}/{file_token}/files/"
                if location.scheme in ("http", "https") and file_token
                else "'none'"
            )
    policy = outer_policy(hashes, remote=remote, frame_source=source)
    policy_meta = (
        ""
        if spec.verb == "chart"
        else (f'<meta http-equiv="Content-Security-Policy" content="{_e(policy)}">')
    )
    marker = ' data-remote="true"' if remote else ""
    if spec.verb == "html" and not remote and not review_only:
        marker += ' data-html="true"'
    if interaction is not None:
        marker += ' data-integrated="true"'
    if review_only:
        marker += ' data-review-only="true"'
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{policy_meta}"
        f"<title>{_e(spec.app)}</title><style>{_asset('chrome.css')}</style></head>"
        f'<body{timeout}{marker}><main id="fenric">{"".join(parts)}</main>'
        f"<script>{script}</script>"
        + (f"<script>{remote_script}</script>" if remote else "")
        + "</body></html>"
    )
    wide = spec.verb in ("custom", "chart", "html") or any(
        "image" in question for question in fields.get("questions", [])
    )
    return Page(
        title=spec.app,
        html=html,
        timeout=spec.timeout,
        width=_WIDE if wide else _NARROW,
        bundle=bundle,
        file_token=file_token,
    )


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _asset(name: str) -> str:
    return resources.files("fenric").joinpath(name).read_text(encoding="utf-8")


def _inline_script(code: str) -> str:
    """Make ``code`` safe to sit inside a script element without changing it."""
    return (
        code.replace("</script", "<\\/script")
        .replace("</SCRIPT", "<\\/SCRIPT")
        .replace("<!--", "<\\!--")
    )


def _script_hash(code: str) -> str:
    return base64.b64encode(hashlib.sha256(code.encode("utf-8")).digest()).decode("ascii")


def _button(action: str, label: str, *, primary: bool = False) -> str:
    marker = " data-primary" if primary else ""
    return f'<button type="button" data-action="{action}"{marker}>{_e(label)}</button>'


# A builder returns the dialog body and its buttons.
_Built = tuple[str, str]

_OK = _button("ok", "OK", primary=True)
_CANCEL_OK = _button("cancel", "Cancel") + _OK


def _required(flag: bool) -> str:
    return " data-required" if flag else ""


def _choices(key: str, options: list[dict[str, Any]], *, multiple: bool, other: bool) -> str:
    kind = "checkbox" if multiple else "radio"
    rows = []
    for index, option in enumerate(options):
        detail = f'<span class="detail">{_e(option["detail"])}</span>' if option["detail"] else ""
        rows.append(
            f'<label class="choice"><input type="{kind}" name="{key}" value="{index}">'
            f"<span>{_e(option['label'])}{detail}</span></label>"
        )
    if other:
        if not multiple:
            rows.append(
                f'<label class="choice"><input type="radio" name="{key}" value="other">'
                f"<span>{OTHER_LABEL}</span></label>"
            )
        rows.append(
            f'<input type="text" class="other" data-key="{key}.other" data-kind="text"'
            f' data-other-for="{key}" placeholder="{OTHER_LABEL}" autocomplete="off">'
        )
    return "".join(rows)


def _group(
    key: str,
    legend: str,
    detail: str | None,
    options: list[dict[str, Any]],
    *,
    multiple: bool,
    other: bool,
    required: bool,
) -> str:
    kind = "checks" if multiple else "radio"
    note = f'<p class="detail">{_e(detail)}</p>' if detail else ""
    head = f"<legend>{_e(legend)}</legend>" if legend else ""
    return (
        f'<fieldset data-key="{key}" data-kind="{kind}"{_required(required)}>{head}{note}'
        f"{_choices(key, options, multiple=multiple, other=other)}</fieldset>"
    )


def _text_input(
    key: str,
    *,
    multiline: bool,
    required: bool,
    placeholder: str | None,
    maxlength: int | None = None,
) -> str:
    hint = f' placeholder="{_e(placeholder)}"' if placeholder else ""
    common = (
        f'id="{key}" data-key="{key}" data-kind="text"{_required(required)}{hint}'
        ' autocomplete="off"'
    )
    if maxlength is not None:
        common += f' maxlength="{maxlength}"'
    if multiline:
        return f'<textarea rows="1" {common}></textarea>'
    return f'<input type="text" {common}>'


def _notify(spec: Spec, chart_script: str | None) -> _Built:
    if "copy_text" in spec.fields:
        encoded = base64.b64encode(spec.fields["copy_text"].encode("utf-8")).decode("ascii")
        return (
            '<section class="copy-view"><span class="badge">Caller text</span>'
            '<pre id="copy-text" tabindex="0" '
            f'data-copy-base64="{encoded}"></pre>'
            '<button type="button" id="copy-button">Copy text</button>'
            '<p id="copy-status" role="status" aria-live="polite"></p></section>',
            _OK,
        )
    return "", _OK


def _confirm(spec: Spec, chart_script: str | None) -> _Built:
    actions = _button("no", spec.fields["no_label"]) + _button(
        "yes", spec.fields["yes_label"], primary=True
    )
    return "", actions


def _pick(spec: Spec, chart_script: str | None) -> _Built:
    multiple = spec.fields["multiple"]
    body = _group(
        "choice",
        "",
        None,
        spec.fields["options"],
        multiple=multiple,
        other=False,
        required=not multiple,
    )
    return body, _CANCEL_OK


def _ask(spec: Spec, chart_script: str | None) -> _Built:
    blocks = []
    for index, question in enumerate(spec.fields["questions"]):
        key = f"q{index}"
        if question["kind"] == "text":
            image = question.get("image")
            note = f'<p class="detail">{_e(question["detail"])}</p>' if question["detail"] else ""
            control = _text_input(
                key,
                multiline=question["multiline"],
                required=question["required"],
                placeholder=None,
                maxlength=MAX_COMMENT_LENGTH if image else None,
            )
            label = _e(question["prompt"])
            illustration = ""
            row_class = "row"
            if image:
                illustration = (
                    '<figure class="caller-image generated"><span class="badge">Caller image</span>'
                    '<div class="image-viewport" tabindex="0" role="region"'
                    f' aria-label="{_e(image["alt"])}">'
                    f'<img class="question-image" src="{_e(image["data"])}"'
                    f' alt="{_e(image["alt"])}"'
                    f' width="{image["width"]}" height="{image["height"]}"'
                    ' data-image-state="loading"></div>'
                    + (
                        f'<figcaption class="detail">{_e(question["detail"])}</figcaption>'
                        if question["detail"]
                        else ""
                    )
                    + '<button type="button" data-image-zoom aria-pressed="false">'
                    "Natural size</button>"
                    '<p class="image-error" role="status">Loading image…</p></figure>'
                )
                label = (
                    f"Comment on {_e(question['prompt'])} "
                    f"({'required' if question['required'] else 'optional'})"
                )
                row_class = "row image-comment"
                note = f'<p class="detail">Up to {MAX_COMMENT_LENGTH} characters.</p>'
            blocks.append(
                illustration
                + f'<div class="{row_class}"><label class="label" for="{key}">'
                + label
                + "</label>"
                + note
                + control
                + "</div>"
            )
        else:
            blocks.append(
                _group(
                    key,
                    question["prompt"],
                    question["detail"],
                    question["options"],
                    multiple=question["kind"] == "multi",
                    other=question["other"],
                    required=question["required"],
                )
            )
    if spec.fields["presentation"] != "wizard":
        return "".join(blocks), _CANCEL_OK
    pages = []
    for index, (question, block) in enumerate(zip(spec.fields["questions"], blocks, strict=True)):
        condition = question.get("when")
        dependency = (
            f' data-dep-question="{condition["question"]}"'
            f' data-dep-option="{condition["option"]}"'
            f' data-dep-operator="{condition["operator"]}"'
            if condition
            else ""
        )
        pages.append(
            f'<section data-question="{index}" data-active="true"{dependency} hidden>'
            f'<h2 class="step-heading" tabindex="-1">{_e(question["prompt"])}</h2>'
            + block
            + "</section>"
        )
    body = (
        '<div id="wizard"><p id="wizard-progress" role="status" aria-live="polite"></p>'
        + "".join(pages)
        + '<section id="wizard-review" hidden><h2 tabindex="-1">Review your answers</h2>'
        '<div id="wizard-answers"></div></section>'
        '<p id="wizard-error" role="alert"></p></div>'
    )
    actions = (
        _button("cancel", "Cancel")
        + '<button type="button" data-action="back" hidden>Back</button>'
        + '<button type="button" data-action="next" data-primary>Next</button>'
        + '<button type="button" data-action="ok" data-primary hidden>Submit</button>'
    )
    return body, actions


def _secret(spec: Spec, chart_script: str | None) -> _Built:
    rows = [
        f'<div class="row"><label class="label">{_e(entry["label"])}</label>'
        f'<input type="password" data-key="f{index}" data-kind="text" autocomplete="off"'
        ' autocapitalize="off" autocorrect="off" spellcheck="false"></div>'
        for index, entry in enumerate(spec.fields["fields"])
    ]
    return "".join(rows), _CANCEL_OK


def _form_components(components: list[dict[str, Any]], counter: list[int]) -> str:
    out = []
    for component in components:
        kind = component["type"]
        if kind == "heading":
            level = component["level"]
            out.append(f"<h{level}>{_e(component['text'])}</h{level}>")
        elif kind == "text":
            out.append(f"<p>{_e(component['text'])}</p>")
        elif kind == "list":
            tag = "ol" if component["ordered"] else "ul"
            items = "".join(f"<li>{_e(item)}</li>" for item in component["items"])
            out.append(f"<{tag}>{items}</{tag}>")
        elif kind == "card":
            title = f"<h3>{_e(component['title'])}</h3>" if component["title"] else ""
            inner = _form_components(component["children"], counter)
            out.append(f'<section class="card">{title}{inner}</section>')
        else:
            key = f"c{counter[0]}"
            counter[0] += 1
            label = _e(component["label"])
            if kind == "field":
                control = _text_input(
                    key,
                    multiline=component["multiline"],
                    required=component["required"],
                    placeholder=component["placeholder"],
                )
                out.append(f'<div class="row"><label class="label">{label}</label>{control}</div>')
            elif kind == "select":
                choices = "".join(
                    f'<option value="{index}">{_e(option["label"])}</option>'
                    for index, option in enumerate(component["options"])
                )
                out.append(
                    f'<div class="row"><label class="label">{label}</label>'
                    f'<select data-key="{key}" data-kind="select"'
                    f'{_required(component["required"])}><option value=""></option>'
                    f"{choices}</select></div>"
                )
            else:
                checked = " checked" if component["checked"] else ""
                out.append(
                    f'<label class="choice"><input type="checkbox" data-key="{key}"'
                    f' data-kind="check"{checked}><span>{label}</span></label>'
                )
    return "".join(out)


def _form(spec: Spec, chart_script: str | None) -> _Built:
    return _form_components(spec.fields["components"], [0]), _CANCEL_OK


def _frame(document: str, sandbox: str, height: int, frame_id: str, *, url: bool = False) -> str:
    source = "src" if url else "srcdoc"
    return (
        f'<div class="generated"><span class="badge">{GENERATED_BADGE}</span>'
        f'<iframe id="{frame_id}" sandbox="{sandbox}" height="{height}" '
        f'{source}="{_e(document)}"></iframe></div>'
    )


def _frame_document(policy: str | None, body: str) -> str:
    policy_meta = (
        ""
        if policy is None
        else (f'<meta http-equiv="Content-Security-Policy" content="{_e(policy)}">')
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{policy_meta}"
        f"<style>{_FRAME_STYLE}</style></head><body>{body}</body></html>"
    )


def _custom(spec: Spec, chart_script: str | None) -> _Built:
    document = _frame_document(
        "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:",
        sanitize_html(spec.fields["html"]),
    )
    return _frame(document, "", spec.fields["height"], "fenric-custom"), _OK


def _chart(spec: Spec, chart_script: str | None) -> _Built:
    if chart_script is None:
        raise ValueError("a chart page needs its compiled script")
    scripts = [
        _inline_script(code) for code in (_asset("_assets/d3.js"), CHART_BOOTSTRAP, chart_script)
    ]
    data = (
        json.dumps(spec.fields["data"], ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    body = (
        '<div id="root"></div>'
        f'<script type="application/json" id="fenric-data">{data}</script>'
        + "".join(f"<script>{code}</script>" for code in scripts)
    )
    document = _frame_document(None, body)
    return _frame(document, "allow-scripts", spec.fields["height"], "fenric-chart"), _OK


def _html(spec: Spec, frame_url: str | None) -> _Built:
    return _frame(
        frame_url or "about:blank", "allow-scripts", spec.fields["height"], "fenric-html", url=True
    ), _OK


_BUILDERS = {
    "notify": _notify,
    "confirm": _confirm,
    "pick": _pick,
    "ask": _ask,
    "secret": _secret,
    "form": _form,
    "custom": _custom,
    "chart": _chart,
    "html": _html,
}
