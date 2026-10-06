"""aio/control.py: the control socket the supervisor offers to the console.

The console asks the supervisor, over a Unix socket in the run directory, for a fixed set of
operations on Headscale (and, since phase 3, on the backups). There are no parameters: no query
strings, no request bodies, no container names, no commands. Anything else is refused (404, 405
or 400); values that an operation needs travel in a file in the run directory, never in a request.

    POST /configtest        'headscale configtest'
    POST /restart           restart Headscale and wait until it is healthy
    GET  /status            health of the supervised processes + Headscale version
    POST /backup            start a backup now
    POST /backup-settings   apply the backup schedule and retention the console wrote
    POST /backup-upload     register an uploaded archive
    POST /restore           restore the archive named in the request file

The supervisor (aio/supervisor.py) supplies the backends; this module only serves the contract.
Standard library only.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import re
import socketserver

log = logging.getLogger("control")

API_VERSION = 1
VERSION_TTL = 60.0

# The whole API: (method, path) -> backend name. Nothing else is served.
ROUTES = {
    ("POST", "/configtest"): "configtest",
    ("POST", "/restart"): "restart",
    ("GET", "/status"): "status",
    ("POST", "/backup"): "backup",
    ("POST", "/backup-settings"): "backup_settings",   # the values travel in a file in the run dir
    ("POST", "/backup-upload"): "backup_upload",       # names travel in a file in the run dir
    ("POST", "/restore"): "restore",                   # the archive name travels in a file in the run dir
}
PATHS = {path for _, path in ROUTES}

_VERSION = re.compile(r"v?\d+\.\d+\.\d+[0-9A-Za-z.+-]*")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "hse-control"
    # name -> callable returning (HTTP status, payload); set by serve()
    backends: dict = {}
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
            if name not in self.backends:
                return self._send(404, {"ok": False, "error": "not found"})
            code, payload = self.backends[name]()
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


def serve(path: str, backends: dict) -> Server:
    """Bind the Unix socket (replacing a stale one) readable by owner+group.

    ``backends`` maps the route names ("configtest", "restart", "status", "backup"...) to callables
    returning (HTTP status, payload); a route without a backend answers 404."""
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
