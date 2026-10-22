"""Operator-configured, temporary tailnet-only HTTPS dialog transport."""

from __future__ import annotations

import http.server
import json
import math
import os
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fenric import backend
from fenric.chrome import Page
from fenric.serve_guard import stop_group
from fenric.session import Session

SETUP_SECONDS = 4.0
PROBE_SECONDS = 30.0
REQUEST_SECONDS = 2.0
ANNOUNCE_SECONDS = 5.0
TERMINAL_SECONDS = 0.5
MAX_BODY = 65536
MAX_CONFIG = 16384


class RemoteError(RuntimeError):
    """A fixed, value-free reason remote cannot be used."""


def config_path() -> Path:
    return (
        Path(os.environ.get("FENRIC_CONFIG_DIR", "~/.config/fenric")).expanduser() / "remote.json"
    )


def _json(raw: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def read_config() -> dict[str, Any] | None:
    path = config_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise RemoteError("configuration error") from None
    try:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > MAX_CONFIG
        ):
            raise ValueError
        value = _json(path.read_bytes())
        if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
            raise ValueError
        if not value["enabled"] and set(value) == {"enabled"}:
            return value
        if set(value) != {"enabled", "tailscale", "ports", "grace", "announce"}:
            raise ValueError
        if not isinstance(value["tailscale"], str) or not Path(value["tailscale"]).is_absolute():
            raise ValueError
        ports = value["ports"]
        if (
            not isinstance(ports, list)
            or len(ports) != 2
            or any(type(port) is not int for port in ports)
            or not 1024 <= ports[0] <= ports[1] <= 65535
            or ports[1] - ports[0] > 31
        ):
            raise ValueError
        grace = value["grace"]
        if type(grace) not in (int, float) or not math.isfinite(grace) or not 0 <= grace <= 60:
            raise ValueError
        if (
            not isinstance(value["announce"], list)
            or len(value["announce"]) > 64
            or any(
                not isinstance(item, str) or not item or "\0" in item for item in value["announce"]
            )
        ):
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise RemoteError("configuration error") from None


def _save(value: dict[str, Any]) -> None:
    path = config_path()
    scratch: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, scratch = tempfile.mkstemp(prefix=".remote-", dir=path.parent)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
        os.replace(scratch, path)
    except OSError:
        raise RemoteError("configuration error") from None
    finally:
        if scratch:
            try:
                Path(scratch).unlink(missing_ok=True)
            except OSError:
                pass


def off() -> None:
    _save({"enabled": False})


def _command(program: str, *args: str, timeout: float) -> Any:
    child = None
    try:
        child = subprocess.Popen(
            [program, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        output, _ = child.communicate(timeout=timeout)
        if child.returncode or len(output) > 1 << 20:
            raise ValueError
        return _json(output)
    except (OSError, ValueError, subprocess.TimeoutExpired, RecursionError):
        raise RemoteError("tailscale unavailable") from None
    finally:
        if child is not None:
            stop_group(child)


def _identity(program: str, timeout: float) -> tuple[str, str]:
    value = _command(program, "status", "--json", timeout=timeout)
    if not isinstance(value, dict) or value.get("BackendState") != "Running":
        raise RemoteError("tailnet unavailable")
    node = value.get("Self")
    if not isinstance(node, dict) or node.get("Tags"):
        raise RemoteError("owner unavailable")
    user = value.get("User", {}).get(str(node.get("UserID")))
    login = user.get("LoginName") if isinstance(user, dict) else None
    if not isinstance(login, str) or not login or not login.isascii():
        raise RemoteError("owner unavailable")
    host = node.get("DNSName")
    if not isinstance(host, str):
        raise RemoteError("HTTPS unavailable")
    host = host.rstrip(".")
    if (
        not host.endswith(".ts.net")
        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in host)
        or host not in (value.get("CertDomains") or [])
    ):
        raise RemoteError("HTTPS unavailable")
    return host, login


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class Transport:
    """An authenticated loopback endpoint; it never returns submitted values."""

    def __init__(self, page: Page, session: Session, authority: str, login: str) -> None:
        self.session = session
        self.token = secrets.token_urlsafe(32)
        self.path = "/" + self.token
        self.authority = authority
        self.origin = "https://" + authority
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(REQUEST_SECONDS)

            def log_message(self, *args: Any) -> None:
                pass

            def log_error(self, *args: Any) -> None:
                pass

            def send_error(
                self, code: int, message: str | None = None, explain: str | None = None
            ) -> None:
                self.reply(400, b'{"error":"refused"}')

            def reply(self, code: int, data: bytes, content: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            def allowed(self, post: bool = False) -> bool:
                expected = {
                    "Host": authority,
                    "X-Forwarded-Host": authority,
                    "X-Forwarded-Proto": "https",
                    "Tailscale-User-Login": login,
                }
                if post:
                    expected.update({"Origin": owner.origin, "Content-Type": "application/json"})
                markers = self.headers.get_all("Fenric-Request")
                if markers is not None:
                    try:
                        marker = uuid.UUID(markers[0])
                        if len(markers) != 1 or marker.version != 4 or str(marker) != markers[0]:
                            return False
                    except (ValueError, IndexError):
                        return False
                elif post:
                    return False
                return (
                    len(self.requestline) <= 2048
                    and sum(len(k) + len(v) for k, v in self.headers.items()) <= 8192
                    and not self.headers.get_all("Tailscale-Funnel-Request")
                    and not self.headers.get_all("Transfer-Encoding")
                    and all(self.headers.get_all(k) == [v] for k, v in expected.items())
                )

            def refuse(self) -> None:
                self.reply(403, b'{"error":"refused"}')

            def state_reply(self) -> None:
                value = owner.session.state(self.headers.get("Fenric-Request"))
                self.reply(200, json.dumps(value, separators=(",", ":")).encode())

            def do_GET(self) -> None:
                if not self.allowed():
                    self.refuse()
                elif self.path == owner.path:
                    if owner.session.done.is_set():
                        self.state_reply()
                    else:
                        self.reply(200, page.html.encode(), "text/html; charset=utf-8")
                elif self.path == owner.path + "/state":
                    self.state_reply()
                else:
                    self.refuse()

            def do_POST(self) -> None:
                if not self.allowed(True) or self.path != owner.path + "/action":
                    self.refuse()
                    return
                lengths = self.headers.get_all("Content-Length")
                if (
                    not lengths
                    or len(lengths) != 1
                    or not lengths[0].isascii()
                    or not lengths[0].isdigit()
                    or not 0 < int(lengths[0]) <= MAX_BODY
                ):
                    self.refuse()
                    return
                try:
                    value = _json(self.rfile.read(int(lengths[0])))
                    if not isinstance(value, dict):
                        raise ValueError
                    kind = value.get("kind")
                    if kind == "submit" and set(value) == {"kind", "payload"}:
                        if not owner.session.submit(
                            value["payload"],
                            marker=self.headers["Fenric-Request"],
                            surface="remote",
                            subject=login,
                            acknowledge_settled=True,
                        ):
                            raise ValueError
                    elif kind in ("touch", "cancel", "expire") and set(value) == {"kind"}:
                        if kind == "touch":
                            owner.session.touch()
                        elif kind == "cancel":
                            owner.session.settle(
                                backend.Outcome(backend.CANCELLED),
                                marker=self.headers["Fenric-Request"],
                                surface="remote",
                                subject=login,
                            )
                        else:
                            owner.session.expire(surface="remote", subject=login)
                    else:
                        raise ValueError
                except (ValueError, UnicodeError, RecursionError, OSError):
                    self.refuse()
                    return
                finally:
                    value = None
                self.state_reply()

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


class Remote:
    """Prepare asynchronously, report once, then own only this session's resources."""

    def __init__(
        self,
        page: Page,
        session: Session,
        *,
        desktop_only: bool = False,
        callback: Callable[[dict[str, Any]], None] | None = None,
        status_file: str | os.PathLike[str] | None = None,
        config: dict[str, Any] | None = None,
        budget: float | None = None,
        private: bool = False,
    ) -> None:
        self.page, self.session = page, session
        self.callback, self.status_file = callback, status_file
        self.config = config
        self.private = private
        self.desktop_only = desktop_only
        self.budget = SETUP_SECONDS if budget is None else budget
        self.transport: Transport | None = None
        self.guard: subprocess.Popen[bytes] | None = None
        self.status: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._announce: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self._thread.start()

    def _report(self, reason: str | None = None, url: str | None = None) -> None:
        if self.status is not None:
            return
        self.status = {
            "status": "available" if url else "unavailable",
            "url": url,
            "reason": reason,
        }
        if not self.private:
            print(
                "fenric: remote: " + (url or "unavailable: " + (reason or "setup failed")),
                file=sys.stderr,
            )
        if self.status_file is not None and not self.private:
            try:
                Path(self.status_file).write_text(json.dumps(self.status) + "\n", encoding="utf-8")
            except (OSError, ValueError):
                pass
        if self.callback:
            try:
                self.callback(dict(self.status))
            except Exception:
                pass

    def _run(self) -> None:
        deadline = time.monotonic() + self.budget
        try:
            if self.desktop_only or os.environ.get("FENRIC_DESKTOP_ONLY") == "1":
                raise RemoteError("disabled")
            config = self.config if self.config is not None else read_config()
            if config is None:
                raise RemoteError("not configured")
            if not config["enabled"]:
                raise RemoteError("disabled")
            host, login = _identity(config["tailscale"], max(0.01, deadline - time.monotonic()))
            if self.private and not self.session.surface_eligible("remote", login):
                raise RemoteError("surface not admitted")
            if self._stop.is_set() or self.session.done.is_set():
                raise RemoteError("session ended")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for port in range(config["ports"][0], config["ports"][1] + 1):
                if self._stop.is_set() or time.monotonic() >= deadline:
                    break
                transport = Transport(self.page, self.session, f"{host}:{port}", login)
                self.transport = transport
                guard = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("serve_guard.py")),
                        config["tailscale"],
                        "serve",
                        f"--https={port}",
                        "--yes",
                        f"http://127.0.0.1:{transport.server.server_port}",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.guard = guard
                url = transport.origin + transport.path
                while not self._stop.is_set() and time.monotonic() < deadline:
                    if guard.poll() is not None:
                        break
                    try:
                        with opener.open(
                            url, timeout=min(0.5, max(0.01, deadline - time.monotonic()))
                        ) as response:
                            if response.status == 200 and response.headers.get(
                                "Content-Type", ""
                            ).startswith("text/html"):
                                if self.session.done.is_set():
                                    raise RemoteError("session ended")
                                self._report(url=url)
                                if not self.private:
                                    self._announcement(config, url)
                                return
                    except (OSError, ValueError):
                        pass
                    self._stop.wait(0.05)
                self._cleanup()
            if self._stop.is_set() or self.session.done.is_set():
                raise RemoteError("session ended")
            if time.monotonic() >= deadline:
                raise RemoteError("setup failed")
            raise RemoteError("ports unavailable")
        except RemoteError as exc:
            self._report(str(exc))
        except Exception:
            self._report("setup failed")
        finally:
            if self.status is None or self.status["status"] != "available":
                self._cleanup()

    def _announcement(self, config: dict[str, Any], url: str) -> None:
        if (
            self._stop.wait(config["grace"])
            or self.session.state()["touched"]
            or self.session.done.is_set()
        ):
            return
        if config["announce"]:
            try:
                self._announce = subprocess.Popen(
                    config["announce"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._announce.communicate(url.encode(), timeout=ANNOUNCE_SECONDS)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                if self._announce is not None and self._announce.poll() is None:
                    stop_group(self._announce)
            finally:
                if self._announce is not None:
                    stop_group(self._announce)
                self._announce = None

    def _cleanup(self) -> None:
        guard, self.guard = self.guard, None
        if guard:
            if guard.stdin:
                guard.stdin.close()
            try:
                guard.wait(timeout=9)
            except subprocess.TimeoutExpired:
                # A broken guard is a fixed failure, never a global Serve reset.
                guard.kill()
                guard.wait(timeout=1)
        transport, self.transport = self.transport, None
        if transport:
            transport.close()

    def close(self) -> None:
        if self.transport and self.status and self.status["status"] == "available":
            self._stop.wait(TERMINAL_SECONDS)
        self._stop.set()
        announce = self._announce
        if announce is not None and announce.poll() is None:
            try:
                os.killpg(announce.pid, signal.SIGINT)
            except ProcessLookupError:
                # Natural exit may win between the check and the signal.
                pass
        self._thread.join(self.budget + ANNOUNCE_SECONDS + 10)
        self._cleanup()


def setup(
    *,
    tailscale: str = "tailscale",
    port_start: int = 8443,
    port_end: int = 8450,
    grace: float = 3,
    announce: str | None = None,
) -> None:
    program = shutil.which(tailscale)
    if not program:
        raise RemoteError("tailscale unavailable")
    try:
        command = shlex.split(announce) if announce else []
        if command:
            executable = shutil.which(command[0])
            if not executable:
                raise ValueError
            command[0] = str(Path(executable).resolve())
        config = {
            "enabled": True,
            "tailscale": str(Path(program).resolve()),
            "ports": [port_start, port_end],
            "grace": grace,
            "announce": command,
        }
        if (
            not 1024 <= port_start <= port_end <= 65535
            or port_end - port_start > 31
            or not math.isfinite(grace)
            or not 0 <= grace <= 60
        ):
            raise ValueError
    except (ValueError, OSError):
        raise RemoteError("configuration error") from None
    session = Session(None, lambda value: value, lambda value: "ok")
    page = Page("Fenric setup", "<!doctype html><title>Fenric setup</title>", None, 520)
    probe = Remote(page, session, config=dict(config, announce=[]), budget=PROBE_SECONDS)
    probe.start()
    # Setup deliberately verifies HTTPS before writing enabled configuration.
    deadline = time.monotonic() + PROBE_SECONDS + 1
    while probe.status is None and time.monotonic() < deadline:
        time.sleep(0.05)
    probe.close()
    if not probe.status or probe.status["status"] != "available":
        raise RemoteError(probe.status["reason"] if probe.status else "setup failed")
    _save(config)
