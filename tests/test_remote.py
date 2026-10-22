"""Remote public contracts over real loopback HTTP and owned process boundaries."""

from __future__ import annotations

import http.client
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from fenric import backend, remote
from fenric.chrome import build_page
from fenric.cli import main
from fenric.engine import run
from fenric.session import CallerSignals, Session
from fenric.spec import validate_spec

from .support import submits

PROJECT = Path(__file__).resolve().parents[1]
LOGIN, HOST = "operator@example.com", "host.tail123.ts.net"
SENTINEL = "private-value-DO-NOT-ECHO"
CONFIRM = {"verb": "confirm", "message": "Proceed?"}


def wait_for(predicate, seconds: float = 3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "observable condition did not occur within the bound"


class WaitingBackend:
    def __init__(self) -> None:
        self.page = None
        self.session = None
        self.ready = threading.Event()

    def show(self, page, session):
        self.page, self.session = page, session
        self.ready.set()
        assert session.done.wait(4), "dialog did not settle"
        return session.outcome


@pytest.fixture
def tailnet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Replace only the external Tailscale executable and HTTPS proxy boundary."""
    directory = tmp_path / "tailnet"
    directory.mkdir()
    info = {
        "BackendState": "Running",
        "Self": {"DNSName": HOST + ".", "UserID": 7},
        "User": {"7": {"LoginName": LOGIN}},
        "CertDomains": [HOST],
    }
    (directory / "info.json").write_text(json.dumps(info))
    worker = directory / "fake-worker.py"
    worker.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, signal, sys, time
here = pathlib.Path(__file__).parent
with (here / "calls").open("a") as out: out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["status", "--json"]:
    if (here / "hang").exists(): time.sleep(20)
    print((here / "info.json").read_text()); sys.exit(0)
if sys.argv[1] == "serve":
    port = sys.argv[2].split("=")[1]
    mapping = here / (port + ".json")
    try:
        with mapping.open("x") as out: json.dump({"pid":os.getpid(),"target":sys.argv[-1]}, out)
    except FileExistsError: sys.exit(1)
    live = True
    def stop(*args):
        global live
        live = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while live: time.sleep(.01)
    finally: mapping.unlink(missing_ok=True)
    sys.exit(0)
sys.exit(1)
"""
    )
    program = directory / "fake-tailnet"
    program.write_text(
        "#!/bin/sh\n" + shlex.quote(sys.executable) + " " + shlex.quote(str(worker)) + ' "$@"\n'
    )
    program.chmod(0o700)
    config = {
        "enabled": True,
        "tailscale": str(program),
        "ports": [8443, 8445],
        "grace": 0.05,
        "announce": [],
    }
    remote._save(config)
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    class Proxy:
        def open(self, url, timeout):
            parsed = urlsplit(url)
            try:
                mapping = json.loads((directory / f"{parsed.port}.json").read_text())
            except (OSError, ValueError):
                raise OSError("not ready") from None
            request = urllib.request.Request(
                mapping["target"] + parsed.path,
                headers={
                    "Host": parsed.netloc,
                    "X-Forwarded-Host": parsed.netloc,
                    "X-Forwarded-Proto": "https",
                    "Tailscale-User-Login": LOGIN,
                },
            )
            return direct.open(request, timeout=timeout)

    monkeypatch.setattr(remote.urllib.request, "build_opener", lambda *args: Proxy())
    monkeypatch.setattr(remote, "SETUP_SECONDS", 0.8)
    monkeypatch.setattr(remote, "PROBE_SECONDS", 1)
    monkeypatch.setattr(remote, "TERMINAL_SECONDS", 0.1)
    # The constructor default is production's 4s; hang proof uses its real budget.
    yield directory, config
    assert list(directory.glob("8*.json")) == [], "an owned Serve mapping was left behind"


def request(
    transport, *, path=None, method="GET", action=None, headers=None, raw=None, marker=None
):
    expected = [
        ("Host", transport.authority),
        ("X-Forwarded-Host", transport.authority),
        ("X-Forwarded-Proto", "https"),
        ("Tailscale-User-Login", LOGIN),
    ]
    if marker is not None or method == "POST":
        expected.append(("Fenric-Request", marker or str(uuid.uuid4())))
    if method == "POST":
        expected += [("Origin", transport.origin), ("Content-Type", "application/json")]
    body = raw if raw is not None else json.dumps(action).encode() if action is not None else b""
    expected += [("Content-Length", str(len(body)))] if method == "POST" else []
    if headers is not None:
        expected = headers
    connection = http.client.HTTPConnection("127.0.0.1", transport.server.server_port, timeout=3)
    try:
        connection.putrequest(method, path or transport.path, skip_host=True)
        for key, value in expected:
            connection.putheader(key, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def dialog(tailnet, spec=CONFIRM, **options):
    statuses = []
    renderer = WaitingBackend()
    answers = []
    ready = threading.Event()

    def status(value):
        statuses.append(value)
        ready.set()

    thread = threading.Thread(
        target=lambda: answers.append(run(spec, backend=renderer, remote_status=status, **options))
    )
    thread.start()
    assert ready.wait(3) and renderer.ready.wait(3)
    assert statuses[0]["status"] == "available", statuses
    directory, _ = tailnet
    url = urlsplit(statuses[0]["url"])
    mapping = json.loads((directory / f"{url.port}.json").read_text())
    # Locate the actual listener from the proxy's public mapping, not a fixture-created receipt.
    transport = type(
        "Endpoint",
        (),
        {
            "authority": url.netloc,
            "origin": f"https://{url.netloc}",
            "path": url.path,
            "server": type("Listener", (), {"server_port": urlsplit(mapping["target"]).port}),
        },
    )
    return renderer, transport, thread, answers, statuses


def end(renderer, thread):
    renderer.session.settle(backend.Outcome(backend.CANCELLED))
    thread.join(6)
    assert not thread.is_alive()


def test_http_refuses_foreign_and_malformed_requests(capfd, monkeypatch):
    monkeypatch.setattr(remote, "REQUEST_SECONDS", 0.05)
    writes = []
    session = Session(None, lambda payload: payload, lambda value: writes.append(value) or "ok")
    transport = remote.Transport(
        build_page(validate_spec(CONFIRM), remote=True), session, HOST + ":8443", LOGIN
    )
    try:
        assert request(transport)[0] == 200
        good = [
            ("Host", transport.authority),
            ("X-Forwarded-Host", transport.authority),
            ("X-Forwarded-Proto", "https"),
            ("Tailscale-User-Login", LOGIN),
        ]
        cases = [
            ([], "GET", transport.path, None),
            (good, "GET", "/" + SENTINEL, None),
            (good, "TRACE", transport.path, None),
        ]
        for key in ("Host", "X-Forwarded-Host", "X-Forwarded-Proto", "Tailscale-User-Login"):
            cases += [
                ([(k, SENTINEL if k == key else v) for k, v in good], "GET", transport.path, None),
                (good + [(key, dict(good)[key])], "GET", transport.path, None),
            ]
        for key in ("Host", "X-Forwarded-Host", "X-Forwarded-Proto", "Tailscale-User-Login"):
            cases.append(([(k, v) for k, v in good if k != key], "GET", transport.path, None))
        post = good + [
            ("Origin", transport.origin),
            ("Content-Type", "application/json"),
            ("Fenric-Request", str(uuid.uuid4())),
        ]
        cases += [
            (post + [("Content-Length", "999999")], "POST", transport.path + "/action", b""),
            (post + [("Content-Length", "8")], "POST", transport.path + "/action", b"not-json"),
            (
                [(k, SENTINEL if k == "Origin" else v) for k, v in post],
                "POST",
                transport.path + "/action",
                b"",
            ),
            (good + [("Tailscale-Funnel-Request", "?1")], "GET", transport.path, None),
        ]
        duplicate = b'{"kind":"touch","kind":"cancel"}'
        cases += [
            (
                post + [("Content-Length", str(len(duplicate)))],
                "POST",
                transport.path + "/action",
                duplicate,
            ),
            (post + [("Content-Length", "64")], "POST", transport.path + "/action", b""),
            (
                [(k, "text/plain" if k == "Content-Type" else v) for k, v in post]
                + [("Content-Length", "16")],
                "POST",
                transport.path + "/action",
                b'{"kind":"touch"}',
            ),
            (good + [("Transfer-Encoding", "chunked")], "GET", transport.path, None),
        ]
        for headers, method, path, body in cases:
            code, data, response_headers = request(
                transport, path=path, method=method, headers=headers, raw=body
            )
            assert code in (400, 403) and data == b'{"error":"refused"}'
            assert response_headers["Cache-Control"] == "no-store"
            assert SENTINEL.encode() not in data
        assert writes == [] and session.state()["status"] is None
    finally:
        transport.close()
    output = capfd.readouterr()
    assert output.out == output.err == ""


def test_remote_desktop_race_stores_once_and_replays(tailnet, tmp_path, capfd):
    sink = tmp_path / "sink"
    command = tmp_path / "store.py"
    command.write_text(
        "import pathlib,sys\np=pathlib.Path(sys.argv[1])\n"
        "p.open('a').write(sys.stdin.read()+'\\n')\n"
    )
    spec = {
        "verb": "secret",
        "fields": ["KEY"],
        "store": shlex.join([sys.executable, str(command), str(sink)]),
    }
    renderer, transport, thread, answers, statuses = dialog(tailnet, spec)
    markers = [str(uuid.uuid4()) for _ in range(5)]
    barrier = threading.Barrier(5)

    def submit(index):
        payload = {"action": "ok", "values": {"f0": f"{SENTINEL}-{index}"}}
        barrier.wait()
        if index == 0:
            renderer.session.submit(payload, native=True)
            return
        return request(
            transport,
            method="POST",
            path=transport.path + "/action",
            action={"kind": "submit", "payload": payload},
            marker=markers[index],
        )

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(submit, range(5)))
        assert all(
            result is None or json.loads(result[1])["status"] in (None, "ok") for result in results
        )
        assert renderer.session.done.wait(1)
        stored = sink.read_text().splitlines()
        assert len(stored) == 1 and stored[0] in [f"{SENTINEL}-{i}" for i in range(5)]
        winner = int(stored[0].rsplit("-", 1)[1])
        for index in range(1, 5):
            replay = request(
                transport,
                method="POST",
                path=transport.path + "/action",
                action={
                    "kind": "submit",
                    "payload": {"action": "ok", "values": {"f0": f"{SENTINEL}-{index}"}},
                },
                marker=markers[index],
            )
            state = json.loads(replay[1])
            assert state["status"] == "ok" and state["settled_elsewhere"] is (index != winner)
        assert sink.read_text().splitlines() == stored
    finally:
        end(renderer, thread)
    assert answers[0].status == "ok" and answers[0].value is None
    assert renderer.session.outcome.payload is None and len(statuses) == 1
    captured = capfd.readouterr()
    assert SENTINEL not in captured.out + captured.err + json.dumps(statuses)


def test_visits_touch_timeout_and_caller_cancel_share_clock(tailnet, monkeypatch):
    monkeypatch.setattr(backend, "HARD_LIMIT_SECONDS", 1.5)
    monkeypatch.setattr(backend, "SAFETY_NET_SECONDS", 0.02)
    monkeypatch.setattr(backend, "POLL_SECONDS", 0.01)
    cancel = threading.Event()
    renderer, transport, thread, answers, _ = dialog(
        tailnet, {**CONFIRM, "timeout": 0.6}, cancel=cancel
    )
    try:
        before = renderer.session.state()["remaining"]
        for _ in range(2):
            assert request(transport)[0] == 200
        shared = json.loads(request(transport, path=transport.path + "/state")[1])
        assert shared["touched"] is False and 0 < shared["remaining"] <= before
        assert (
            request(
                transport, method="POST", path=transport.path + "/action", action={"kind": "touch"}
            )[0]
            == 200
        )
        time.sleep(max(0, renderer.session.started + 0.63 - time.monotonic()))
        assert not renderer.session.done.is_set()
        assert renderer.session.state()["touched"] is True
        assert (
            request(
                transport, method="POST", path=transport.path + "/action", action={"kind": "expire"}
            )[0]
            == 200
        )
        assert not renderer.session.done.is_set()
        cancel.set()
        assert renderer.session.done.wait(0.2)
    finally:
        end(renderer, thread)
    assert answers[0].status == "cancelled"
    renderer, _, thread, answers, _ = dialog(tailnet, {**CONFIRM, "timeout": 0.6})
    assert renderer.session.done.wait(0.8)
    end(renderer, thread)
    assert answers[0].detail == "timed out"


def test_signal_wakeup_cancels_while_main_thread_is_in_native_call(tmp_path):
    with CallerSignals() as untouched:
        assert not untouched.cancel.is_set()
    assert not untouched.cancel.is_set()
    assert not untouched._thread.is_alive()
    marker = tmp_path / "cancelled"
    program = """
import ctypes, os, pathlib, signal, threading, time
from fenric.session import CallerSignals, Session
with CallerSignals() as signals:
    session=Session(None,lambda value:value,lambda value:'ok',signals.cancel)
    session.start()
    def observe():
        session.done.wait(3)
        pathlib.Path(MARKER).write_text(session.state()['status'])
    threading.Thread(target=observe,daemon=True).start()
    print('ready',flush=True)
    signal.siginterrupt(signal.SIGTERM, False)
    reader,writer=os.pipe()
    def release():
        time.sleep(.8)
        os.write(writer,b'x')
    threading.Thread(target=release,daemon=True).start()
    ctypes.CDLL(None).read(reader,ctypes.create_string_buffer(1),1)
    session.close()
""".replace("MARKER", repr(str(marker)))
    child = subprocess.Popen(
        [sys.executable, "-c", program],
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        time.sleep(0.05)
        assert child.poll() is None and not marker.exists()
        os.kill(child.pid, signal.SIGTERM)
        wait_for(marker.exists, 0.4)
        assert marker.read_text() == "cancelled" and child.poll() is None
        assert child.wait(timeout=3) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


def test_parent_death_stops_owned_foreground_serve(tailnet, tmp_path):
    directory, config = tailnet
    guard = PROJECT / "fenric/serve_guard.py"
    program = """
import pathlib,subprocess,sys,time
child=subprocess.Popen(ARGS,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
pathlib.Path(PID).write_text(str(child.pid))
while True: time.sleep(1)
""".replace(
        "ARGS",
        repr(
            [
                sys.executable,
                str(guard),
                config["tailscale"],
                "serve",
                "--https=8443",
                "--yes",
                "http://127.0.0.1:12345",
            ]
        ),
    ).replace("PID", repr(str(tmp_path / "guard-pid")))
    parent = subprocess.Popen([sys.executable, "-c", program])
    try:
        wait_for(lambda: (directory / "8443.json").exists())
        child_pid = json.loads((directory / "8443.json").read_text())["pid"]
        os.kill(child_pid, 0)
        parent.kill()
        parent.wait(timeout=2)
        wait_for(lambda: not (directory / "8443.json").exists())

        def gone():
            try:
                os.kill(child_pid, 0)
                return False
            except ProcessLookupError:
                return True

        wait_for(gone)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)
        pid_file = tmp_path / "guard-pid"
        if pid_file.exists():
            mapping_file = directory / "8443.json"
            own_pids = [int(pid_file.read_text())]
            if mapping_file.exists():
                own_pids.insert(0, json.loads(mapping_file.read_text())["pid"])
            for own_pid in own_pids:
                try:
                    os.kill(own_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            wait_for(lambda: not mapping_file.exists())


def test_machine_config_and_failures_report_once_without_blocking_desktop(
    tailnet, monkeypatch, tmp_path, capfd
):
    directory, config = tailnet
    process_calls = []
    socket_calls = []
    original_popen = remote.subprocess.Popen
    import socket

    original_socket = socket.socket

    def observed_popen(*args, **kwargs):
        process_calls.append(args[0])
        return original_popen(*args, **kwargs)

    def observed_socket(*args, **kwargs):
        socket_calls.append(args)
        return original_socket(*args, **kwargs)

    remote.off()
    statuses = []
    monkeypatch.setattr(remote.subprocess, "Popen", observed_popen)
    monkeypatch.setattr(socket, "socket", observed_socket)
    assert run(CONFIRM, backend=submits("yes"), remote_status=statuses.append).value is True
    assert statuses[0]["reason"] == "disabled" and not (directory / "calls").exists()
    remote.config_path().unlink()
    statuses.clear()
    assert run(CONFIRM, backend=submits("yes"), remote_status=statuses.append).value is True
    assert statuses[0]["reason"] == "not configured" and not (directory / "calls").exists()
    assert process_calls == [] and socket_calls == []
    monkeypatch.setattr(remote.subprocess, "Popen", original_popen)
    monkeypatch.setattr(socket, "socket", original_socket)
    remote._save(config)
    assert main(["remote", "status"]) == 0
    output = capfd.readouterr()
    assert json.loads(output.out)["enabled"] is True
    for mutation, expected in (
        ({"BackendState": "Stopped"}, "tailnet unavailable"),
        ({"Self": {"Tags": ["tag:test"]}}, "owner unavailable"),
        ({"CertDomains": []}, "HTTPS unavailable"),
    ):
        info = json.loads((directory / "info.json").read_text())
        original = dict(info)
        info.update(mutation)
        (directory / "info.json").write_text(json.dumps(info))
        statuses.clear()

        # Keep the desktop alive until its one bounded status is actually delivered.
        class AwaitStatus:
            def show(self, page, session):
                wait_for(lambda: bool(statuses))
                return backend.Outcome(backend.SUBMITTED, {"action": "yes", "values": {}})

        assert run(CONFIRM, backend=AwaitStatus(), remote_status=statuses.append).value is True
        assert len(statuses) == 1 and statuses[0]["reason"] == expected
        (directory / "info.json").write_text(json.dumps(original))
    (directory / "hang").touch()
    statuses.clear()
    started = time.monotonic()
    assert run(CONFIRM, backend=AwaitStatus(), remote_status=statuses.append).value is True
    assert len(statuses) == 1 and statuses[0]["reason"] == "tailscale unavailable"
    assert time.monotonic() - started < 2
    (directory / "hang").unlink()
    missing = dict(config, tailscale=str(directory / "missing"))
    remote._save(missing)
    statuses.clear()
    assert run(CONFIRM, backend=AwaitStatus(), remote_status=statuses.append).value is True
    assert len(statuses) == 1 and statuses[0]["reason"] == "tailscale unavailable"
    remote._save(config)
    assert main(["remote", "setup", "--tailscale", config["tailscale"], "--grace", "0"]) == 0
    assert remote.config_path().stat().st_mode & 0o777 == 0o600
    assert main(["remote", "off"]) == 0 and remote.read_config() == {"enabled": False}
    remote.config_path().write_text('{"enabled":true,"enabled":false}')
    with pytest.raises(remote.RemoteError, match="configuration error"):
        remote.read_config()


def test_announcement_and_concurrent_ports_preserve_owned_lifecycle(tailnet, tmp_path, monkeypatch):
    directory, config = tailnet
    announce = tmp_path / "announce.py"
    delivered = tmp_path / "announced"
    announce.write_text(
        "import pathlib,sys\npathlib.Path(sys.argv[1]).write_text(sys.stdin.read())\n"
    )
    config["announce"] = [sys.executable, str(announce), str(delivered)]
    config["grace"] = 0.15
    remote._save(config)
    first, first_url, first_thread, _, _ = dialog(tailnet)
    try:
        first.session.touch()
        second, second_url, second_thread, _, _ = dialog(tailnet)
        try:
            assert first_url.authority != second_url.authority
            wait_for(delivered.exists)
            assert delivered.read_text() == second_url.origin + second_url.path
            assert len(list(directory.glob("8*.json"))) == 2
        finally:
            end(second, second_thread)
        assert len(list(directory.glob("8*.json"))) == 1
    finally:
        end(first, first_thread)

    # A natural announcement exit can win after close checks liveness.
    # Inject its refusal at the signal boundary after the real child finishes.
    pid_file, release = tmp_path / "announce-pid", tmp_path / "announce-exit"
    finished = tmp_path / "announce-finished"
    announce.write_text(
        "import os,pathlib,sys,time\n"
        "sys.stdin.read()\npathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)\n"
        "pathlib.Path(sys.argv[3]).touch()\n"
    )
    config["announce"] = [sys.executable, str(announce), str(pid_file), str(release), str(finished)]
    config["grace"] = 0
    remote._save(config)
    original_signal = os.killpg
    crossed = threading.Event()

    def raced_signal(group, number):
        if pid_file.exists() and group == int(pid_file.read_text()) and number == signal.SIGINT:
            if not crossed.is_set():
                crossed.set()
                release.touch()

                # The child writes this immediately before exiting. Emulate
                # the OS's exited-group refusal at the signal boundary, rather
                # than probing a departed process group under the test sandbox.
                wait_for(finished.exists)
                raise ProcessLookupError
        return original_signal(group, number)

    monkeypatch.setattr(os, "killpg", raced_signal)
    sink = tmp_path / "announcement-race.env"
    renderer, _, thread, answers, _ = dialog(
        tailnet, {"verb": "secret", "fields": ["KEY"], "into": str(sink)}
    )
    try:
        wait_for(lambda: pid_file.exists() and pid_file.read_text().isdigit())
        renderer.session.submit({"action": "ok", "values": {"f0": "made-up-value"}}, native=True)
        thread.join(6)
        assert not thread.is_alive() and crossed.is_set()
        assert len(answers) == 1 and answers[0].status == "ok"
        assert sink.read_text() == "KEY=made-up-value\n"
    finally:
        release.touch()
        end(renderer, thread)


def test_destination_display_and_retarget_refusal(tmp_path):
    first, other, link = tmp_path / "one.env", tmp_path / "two.env", tmp_path / "linked.env"
    first.write_text("KEY=old\n")
    other.write_text("KEY=other\n")
    link.symlink_to(first)

    class Retarget:
        def show(self, page, session):
            assert str(first.resolve()) in page.html and "FILE</span>" in page.html
            assert "Existing file" in page.html and "dotenv" in page.html
            link.unlink()
            link.symlink_to(other)
            return backend.Outcome(backend.SUBMITTED, {"action": "ok", "values": {"f0": SENTINEL}})

    result = run({"verb": "secret", "fields": ["KEY"], "into": str(link)}, backend=Retarget())
    assert result.exit_code == 3 and "resolution changed" in result.detail
    assert first.read_text() == "KEY=old\n" and other.read_text() == "KEY=other\n"
    renderer = submits(f0="")
    spec = {
        "verb": "secret",
        "fields": ["KEY"],
        "store": shlex.join([sys.executable, "-c", "pass"]),
        "store_label": "<img src=x>Vault",
    }
    assert run(spec, backend=renderer).status == "ok"
    markup = renderer.pages[0].html
    assert "COMMAND</span>" in markup and 'class="generated-description"' in markup
    assert "“&lt;img src=x&gt;Vault”" in markup and str(Path(sys.executable).absolute()) in markup
    assert validate_spec(spec).fields["store_label"] == "<img src=x>Vault"


def test_secret_failures_and_malformed_posts_never_echo_or_repeat(tailnet, tmp_path, capfd):
    invoked = tmp_path / "invoked"
    script = tmp_path / "refuse.py"
    script.write_text(
        "import pathlib,sys\np=pathlib.Path(sys.argv[1])\np.open('a').write('called\\n')\n"
        "print(sys.stdin.read())\nsys.exit(1)\n"
    )
    spec = {
        "verb": "secret",
        "fields": ["KEY"],
        "store": shlex.join([sys.executable, str(script), str(invoked)]),
    }
    status_file = tmp_path / "status.json"
    renderer, transport, thread, answers, _ = dialog(tailnet, spec, status_file=status_file)
    try:
        malformed = {"kind": "submit", "payload": {"action": SENTINEL, "values": {}}}
        assert (
            request(transport, method="POST", path=transport.path + "/action", action=malformed)[0]
            == 403
        )
        assert not renderer.session.done.is_set() and not invoked.exists()
        action = {"kind": "submit", "payload": {"action": "ok", "values": {"f0": SENTINEL}}}
        code, data, _ = request(
            transport, method="POST", path=transport.path + "/action", action=action
        )
        assert code == 200 and json.loads(data)["status"] == "error"
        assert SENTINEL.encode() not in data and invoked.read_text() == "called\n"
        code, replay, _ = request(
            transport, method="POST", path=transport.path + "/action", action=action
        )
        assert code == 200 and json.loads(replay)["status"] == "error"
        assert invoked.read_text() == "called\n"
    finally:
        end(renderer, thread)
    assert answers[0].exit_code == 3 and answers[0].value is None
    output = capfd.readouterr()
    assert SENTINEL not in output.out + output.err + status_file.read_text() + repr(answers)
