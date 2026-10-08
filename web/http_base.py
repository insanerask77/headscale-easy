"""Response and request helpers shared by the console's request handler."""

from __future__ import annotations

import urllib.parse
from http import cookies


class HttpHelpers:
    """Mixin for ``BaseHTTPRequestHandler``: sending responses and reading the cookie jar and form body."""

    # --- responses ---
    def send(self, status: int, body: str | bytes, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, location: str, headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()

    def cookie(self, name: str) -> str | None:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar[name].value if name in jar else None

    def form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 262144:
            return {}
        data = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        # 'route' (checkboxes) and fields named "...[]" (rows) can repeat
        return {k: (v if k == "route" or k.endswith("[]") else v[0]) for k, v in data.items()}
