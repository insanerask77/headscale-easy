"""Headscale Easy — Docker helper (hs-helper).

The only part of the stack that talks to the Docker socket. The web UI used to
mount /var/run/docker.sock itself, which is equivalent to root on the host;
now it asks this helper, over a Unix socket in a shared volume, for exactly
three fixed operations on a fixed container:

    POST /configtest   'headscale configtest' in the Headscale container
    POST /restart      restart Headscale and wait until it is healthy
    GET  /status       health of the stack's containers + Headscale version
    POST /backup       start a backup now (all-in-one supervisor only; 404 here)

There are no parameters: no query strings, no request bodies, no container
names, no commands. Anything else is refused (404, 405 or 400). The container
name comes from the helper's own environment (HEADSCALE_CONTAINER), never from
a request.

Standard library only. Runs with no network (network_mode: none), as an
unprivileged user in the Docker socket's group.
"""
from __future__ import annotations

import http.client
import http.server
import json
import logging
import os
import re
import signal
import socket
import socketserver
import sys
import threading
import time
import urllib.parse

log = logging.getLogger("hs-helper")

API_VERSION = 1
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
HELPER_SOCKET = os.environ.get("HELPER_SOCKET", "/run/hse-helper/helper.sock")
HEADSCALE_CONTAINER = os.environ.get("HEADSCALE_CONTAINER", "headscale")
RESTART_WAIT = float(os.environ.get("RESTART_WAIT", "120"))
VERSION_TTL = 60.0

# The whole API: (method, path) -> handler name. Nothing else is served.
ROUTES = {
    ("POST", "/configtest"): "configtest",
    ("POST", "/restart"): "restart",
    ("GET", "/status"): "status",
    ("POST", "/backup"): "backup",   # all-in-one supervisor only (see aio/supervisor.py)
    ("POST", "/backup-settings"): "backup_settings",   # same; the values travel in a file in the run dir
}
PATHS = {path for _, path in ROUTES}

# configtest and restart change or depend on Headscale's state: one at a time
_op_lock = threading.Lock()
_version_cache: dict = {"at": 0.0, "value": None}

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VERSION = re.compile(r"v?\d+\.\d+\.\d+[0-9A-Za-z.+-]*")


# -----------------------------------------------------------------------------
# Docker Engine API over its Unix socket
# -----------------------------------------------------------------------------

class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 60):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def docker(method: str, path: str, body: dict | None = None, timeout: float = 60) -> tuple[int, bytes]:
    """One Docker Engine API call. Only called with paths built in this file."""
    conn = _UnixHTTPConnection(DOCKER_SOCKET, timeout=timeout)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _container() -> str:
    return urllib.parse.quote(HEADSCALE_CONTAINER, safe="")


def docker_ok() -> bool:
    try:
        return docker("GET", "/_ping", timeout=3)[0] == 200
    except OSError:
        return False


def exec_in_headscale(cmd: list[str], timeout: float = 90) -> tuple[int | None, str]:
    """Run a fixed command in the Headscale container; (exit code, output)."""
    status, raw = docker("POST", f"/containers/{_container()}/exec", {
        "AttachStdout": True, "AttachStderr": True, "Tty": True, "Cmd": cmd,
    })
    if status != 201:
        return None, f"docker exec: HTTP {status}"
    exec_id = urllib.parse.quote(json.loads(raw)["Id"], safe="")
    _, out = docker("POST", f"/exec/{exec_id}/start", {"Detach": False, "Tty": True}, timeout=timeout)
    _, info = docker("GET", f"/exec/{exec_id}/json")
    return json.loads(info).get("ExitCode"), _ANSI.sub("", out.decode(errors="replace")).strip()


def configtest() -> tuple[int, dict]:
    with _op_lock:
        code, out = exec_in_headscale(["headscale", "configtest"])
    return 200, {"ok": code == 0, "output": out}


def _health(raw: bytes) -> str | None:
    return (json.loads(raw).get("State", {}).get("Health") or {}).get("Status")


def restart() -> tuple[int, dict]:
    with _op_lock:
        status, _ = docker("POST", f"/containers/{_container()}/restart?t=10", timeout=60)
        if status != 204:
            log.error("could not restart Headscale: HTTP %s", status)
            return 200, {"ok": False, "error": f"docker restart: HTTP {status}"}
        _version_cache["at"] = 0.0
        deadline = time.time() + RESTART_WAIT
        time.sleep(min(3, RESTART_WAIT))
        while time.time() < deadline:
            _, raw = docker("GET", f"/containers/{_container()}/json")
            if _health(raw) == "healthy":
                return 200, {"ok": True}
            time.sleep(2)
    return 200, {"ok": False, "error": "Headscale did not become healthy in time"}


def headscale_version() -> str | None:
    """'headscale version' in the container, cached for a minute."""
    now = time.time()
    if now - _version_cache["at"] < VERSION_TTL:
        return _version_cache["value"]
    value = None
    try:
        code, out = exec_in_headscale(["headscale", "version"], timeout=15)
        match = _VERSION.search(out) if code == 0 else None
        value = match.group(0) if match else None
    except (OSError, ValueError, KeyError):
        log.warning("could not read Headscale's version", exc_info=True)
    _version_cache.update(at=now, value=value)
    return value


def _summary(c: dict) -> dict:
    labels = c.get("Labels") or {}
    names = c.get("Names") or [""]
    status = c.get("Status") or ""
    health = None
    for word in ("healthy", "unhealthy", "starting"):
        if f"({word}" in status:
            health = word
            break
    return {
        "name": names[0].lstrip("/"),
        "service": labels.get("com.docker.compose.service", ""),
        "image": c.get("Image", ""),
        "state": c.get("State", ""),
        "health": health,
        "status": status,
    }


def status() -> tuple[int, dict]:
    """The stack = the containers of Headscale's Compose project."""
    result = {"api": API_VERSION, "docker": False,
              "headscale": {"container": HEADSCALE_CONTAINER, "version": None},
              "containers": []}
    if not docker_ok():
        return 200, result
    result["docker"] = True
    code, raw = docker("GET", f"/containers/{_container()}/json", timeout=10)
    if code != 200:
        return 200, result
    project = ((json.loads(raw).get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project")
    if project:
        filters = urllib.parse.quote(json.dumps({"label": [f"com.docker.compose.project={project}"]}))
        code, raw = docker("GET", f"/containers/json?all=1&filters={filters}", timeout=10)
        if code == 200:
            result["containers"] = sorted((_summary(c) for c in json.loads(raw)), key=lambda c: c["name"])
    result["headscale"]["version"] = headscale_version()
    return 200, result


# -----------------------------------------------------------------------------
# HTTP over a Unix socket
# -----------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "hs-helper"
    # name -> callable returning (HTTP status, payload). None = the Docker
    # backends of this module; the all-in-one supervisor (aio/supervisor.py)
    # serves the same contract with its own.
    backends: dict | None = None
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def address_string(self) -> str:
        return "unix"

    def log_message(self, fmt, *args):
        log.info("%s", fmt % args)

    def _send(self, code: int, payload: dict, extra: dict | None = None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        method, path = self.command, self.path
        # No parameters of any kind: exact paths only, no query, no body
        if "?" in path or "#" in path:
            return self._send(400, {"ok": False, "error": "parameters are not accepted"})
        if self.headers.get("Transfer-Encoding") or int(self.headers.get("Content-Length") or 0) != 0:
            return self._send(400, {"ok": False, "error": "a request body is not accepted"})
        if path not in PATHS:
            return self._send(404, {"ok": False, "error": "not found"})
        name = ROUTES.get((method, path))
        if name is None:
            allowed = ", ".join(m for m, p in ROUTES if p == path)
            return self._send(405, {"ok": False, "error": "method not allowed"}, {"Allow": allowed})
        try:
            backends = self.backends or {"configtest": configtest, "restart": restart, "status": status}
            if name not in backends:  # /backup on the Docker helper: the route does not exist here
                return self._send(404, {"ok": False, "error": "not found"})
            code, payload = backends[name]()
        except OSError as exc:
            log.error("%s: Docker unreachable: %s", name, exc)
            return self._send(503, {"ok": False, "error": "Docker is not reachable"})
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            log.exception("%s failed", name)
            return self._send(500, {"ok": False, "error": "internal error"})
        self._send(code, payload)

    def __getattr__(self, name: str):
        # do_GET, do_POST, do_DELETE, do_PUT...: every method goes through
        # _handle, which answers 405 for anything not in ROUTES.
        if name.startswith("do_"):
            return self._handle
        raise AttributeError(name)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        conn, _ = super().get_request()
        return conn, ("unix", 0)


def serve(path: str = HELPER_SOCKET, backends: dict | None = None) -> Server:
    """Bind the Unix socket (replacing a stale one) readable by owner+group.

    ``backends`` maps "configtest" / "restart" / "status" (/ "backup") to callables returning
    (HTTP status, payload); the default is the Docker implementation above."""
    handler = Handler
    if backends is not None:
        handler = type("BackendHandler", (Handler,), {"backends": dict(backends)})
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    old = os.umask(0o117)
    try:
        server = Server(path, handler)
    finally:
        os.umask(old)
    os.chmod(path, 0o660)
    return server


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s hs-helper: %(message)s")
    server = serve()
    log.info("listening on %s (container: %s, docker: %s)", HELPER_SOCKET, HEADSCALE_CONTAINER,
             "reachable" if docker_ok() else "NOT reachable")
    # PID 1 ignores SIGTERM unless handled: stop at once on 'docker stop'
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
