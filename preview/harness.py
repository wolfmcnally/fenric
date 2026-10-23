"""Open a dialog's page in WebKit exactly as Fenric builds it.

The page is the HTML ``build_page`` returns, loaded as it is, with
Playwright's clock installed and paused before the content runs so the
countdown moves only when told to. Outcomes are read from ``window.fenric``,
the object the native backend reads.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from playwright.sync_api import APIResponse, Browser, BrowserContext, Error, Page, Playwright, Route

from fenric.charts import ChartError, compile_chart
from fenric.chrome import build_page
from fenric.engine import prepare_destination
from fenric.html_bundle import load_bundle
from fenric.remote import Transport
from fenric.session import Session
from fenric.spec import Spec, validate_spec

INSTALL_HINT = (
    "WebKit for Playwright is not installed; run the harness's install command"
    " (python preview/run.py install, from the project directory)"
)
CHART_HINT = "the chart toolchain is not provisioned; run fenric setup-charts (it needs Bun)"
SCALE = 2


class HarnessError(RuntimeError):
    """The harness cannot run on this machine; the message says what to do."""


@dataclass
class Dialog:
    """One open dialog: its validated spec, its page, and the errors its scripts raised."""

    spec: Spec
    page: Page
    context: BrowserContext
    errors: list[str]
    transport: Transport | None = None
    session: Session | None = None

    def state(self) -> dict[str, Any]:
        return self.page.evaluate("JSON.parse(JSON.stringify(window.fenric))")

    def done(self) -> dict[str, Any] | None:
        return self.state()["done"]

    def primary_disabled(self) -> bool:
        return self.page.evaluate(
            "document.querySelector('button[data-primary]')"
            ".getAttribute('aria-disabled') === 'true'"
        )

    def fit(self) -> None:
        """Size the viewport to the page's measured height, as the native window is sized."""
        height = self.state()["height"]
        self.page.set_viewport_size({"width": self.page.viewport_size["width"], "height": height})

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            try:
                if self.transport:
                    self.transport.close()
            finally:
                if self.session:
                    self.session.close()


def launch(playwright: Playwright) -> Browser:
    try:
        return playwright.webkit.launch()
    except Error as exc:
        if "Executable doesn't exist" in str(exc):
            raise HarnessError(INSTALL_HINT) from None
        raise


def open_dialog(browser: Browser, raw: dict[str, Any], *, scheme: str = "light") -> Dialog:
    """Load the page for ``raw`` with the clock paused at zero."""
    spec = validate_spec(raw)
    chart_script = None
    if spec.verb == "chart":
        try:
            chart_script = compile_chart(spec.fields["code"])
        except ChartError as exc:
            raise HarnessError(f"{CHART_HINT} ({exc})") from None
    transport = None
    session = None
    context = None
    try:
        if spec.verb == "html":
            bundle = load_bundle(spec.fields["root"], spec.fields["entry"], spec.fields["files"])
            token = secrets.token_urlsafe(32)
            session = Session(spec.timeout, lambda p: None, lambda v: "ok")
            stub = build_page(spec, bundle=bundle, file_token=token)
            transport = Transport(stub, session, "", "", local=True)
            built = build_page(
                spec,
                bundle=bundle,
                file_token=token,
                frame_url=transport.origin + transport.files_path + quote(bundle.entry),
            )
        else:
            built = build_page(spec, chart_script, destination=prepare_destination(spec).metadata)
        context = browser.new_context(
            viewport={"width": built.width, "height": 800},
            color_scheme=scheme,
            device_scale_factor=SCALE,
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.clock.install(time=0)
        page.clock.pause_at(0)
        page.set_content(built.html)
        return Dialog(
            spec=spec,
            page=page,
            context=context,
            errors=errors,
            transport=transport,
            session=session,
        )
    except BaseException:
        try:
            if context:
                context.close()
        finally:
            try:
                if transport:
                    transport.close()
            finally:
                if session:
                    session.close()
        raise


def proxy_dialog(route: Route, transport: Transport) -> APIResponse:
    """Forward a test HTTPS request through the real authenticated loopback handler."""
    incoming = route.request
    parsed = urlsplit(incoming.url)
    assert parsed.netloc == transport.authority
    headers = {
        **incoming.headers,
        "Host": transport.authority,
        "X-Forwarded-Host": transport.authority,
        "X-Forwarded-Proto": "https",
        "Tailscale-User-Login": "owner@example.com",
    }
    response = route.fetch(
        url=f"http://127.0.0.1:{transport.server.server_port}" + parsed.path, headers=headers
    )
    route.fulfill(response=response)
    return response


def close_proxy_context(context: BrowserContext) -> None:
    """Drain route callbacks before disposing their context-owned API responses."""
    context.unroute_all(behavior="wait")
    context.close()
