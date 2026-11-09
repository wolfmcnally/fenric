"""Decode and seek synthetic video through both selected-resource transports."""

import secrets
import time
from pathlib import Path

import pytest
from harness import close_proxy_context, open_dialog, proxy_dialog
from playwright.sync_api import expect

from fenric.chrome import build_page
from fenric.html_bundle import load_bundle
from fenric.remote import Transport
from fenric.session import Session
from fenric.spec import validate_spec

DEMO = Path(__file__).resolve().parents[1] / "docs/examples/video-demo"


@pytest.mark.parametrize("extension", ["mp4", "webm"])
@pytest.mark.parametrize("remote", [False, True])
def test_video_decode_seek_replay_and_media_policy(browser, tmp_path, extension, remote):
    name = f"sample.{extension}"
    (tmp_path / name).write_bytes((DEMO / name).read_bytes())
    (tmp_path / "broken.mp4").write_bytes(b"invalid video")
    (tmp_path / "index.html").write_text(
        f'<video controls playsinline preload="metadata" src="{name}"></video>'
    )
    raw = {
        "verb": "html",
        "root": str(tmp_path),
        "entry": "index.html",
        "files": ["index.html", name, "broken.mp4"],
        "timeout": 60,
    }
    dialog = context = transport = session = None
    try:
        if remote:
            token = secrets.token_urlsafe(32)
            built = build_page(
                validate_spec(raw),
                remote=True,
                bundle=load_bundle(str(tmp_path), "index.html", raw["files"]),
                file_token=token,
                frame_url=f"/{token}/files/index.html",
            )
            session = Session(60, lambda p: None, lambda v: "ok")
            transport = Transport(built, session, "fixture.invalid:8443", "owner@example.com")
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.route("https://fixture.invalid:8443/**", lambda r: proxy_dialog(r, transport))
            page = context.new_page()
            page.goto(transport.origin + transport.path)
        else:
            dialog = open_dialog(browser, raw)
            page = dialog.page
            page.clock.resume()
        video = page.frame_locator("#fenric-html").locator("video")

        def wait(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if predicate():
                    return
                page.wait_for_timeout(40)
            assert predicate(), "video observation did not occur"

        wait(lambda: video.evaluate("e=>e.readyState>=1 || e.error!==null"))
        assert video.evaluate("e=>e.error===null && e.videoWidth===160 && e.videoHeight===90")
        assert abs(video.evaluate("e=>e.duration") - 3) < 0.1
        assert video.evaluate("e=>e.controls && e.playsInline && !e.autoplay && e.paused")
        video.evaluate("e=>e.play()")
        wait(lambda: video.evaluate("e=>e.currentTime>.1 && !e.paused"))
        video.evaluate("e=>e.pause()")
        video.evaluate("e=>e.currentTime=1.5")
        wait(lambda: video.evaluate("e=>!e.seeking && Math.abs(e.currentTime-1.5)<.1"))
        video.evaluate("e=>{e.currentTime=2.85;return e.play()}")
        wait(lambda: video.evaluate("e=>e.ended"))
        video.evaluate("e=>{e.currentTime=0;return e.play()}")
        wait(lambda: video.evaluate("e=>e.currentTime>.1 && !e.paused"))
        video.evaluate("e=>e.pause()")
        assert page.evaluate("window.fenric.done") is None
        assert not page.evaluate("window.fenric.touched")
        outside = []
        page.context.route(
            "https://outside.invalid/**", lambda r: (outside.append(r.request.url), r.abort())
        )
        for source in [
            "broken.mp4",
            "unselected.mp4",
            "https://outside.invalid/video.mp4",
            "data",
            "blob",
        ]:
            video.evaluate(
                """async (e,args)=>{
              if(args.source==='data' || args.source==='blob') {
                const b=await (await fetch(args.name)).blob();
                if(args.source==='blob') e.src=URL.createObjectURL(b);
                else {const r=new FileReader();e.src=await new Promise(resolve=>{
                    r.onload=()=>resolve(r.result);r.readAsDataURL(b)});}
              } else e.src=args.source;
              e.load();
            }""",
                {"source": source, "name": name},
            )
            wait(lambda: video.evaluate("e=>e.error!==null"))
        assert outside == []
        # Video and content interactions cannot supply an answer or touch adapter.
        assert page.evaluate("window.fenric.done") is None
        assert not page.evaluate("window.fenric.touched")
        expect(page.locator("#fenric-html")).to_have_attribute("sandbox", "allow-scripts")
    finally:
        if dialog:
            dialog.close()
        if context:
            close_proxy_context(context)
        if transport:
            transport.close()
        if session:
            session.close()
