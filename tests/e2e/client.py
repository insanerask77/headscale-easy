"""HTTP client and stack helpers for the end-to-end tests (standard library only).

The stack is reached through Caddy on 127.0.0.1:<HTTP_PORT> while every
request carries the public Host (HEADSCALE_PUBLIC_URL in .env), so no
/etc/hosts entry is needed on the machine running the tests. Redirects are
never followed automatically: the tests check them.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def read_env(path: str = os.path.join(ROOT, ".env")) -> dict:
    """KEY=value pairs of the .env written by install.sh."""
    env = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _sep, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


ENV = read_env() if os.path.exists(os.path.join(ROOT, ".env")) else {}
PUBLIC_URL = os.environ.get("E2E_PUBLIC_URL") or ENV.get("HEADSCALE_PUBLIC_URL", "")
CONNECT = os.environ.get("E2E_CONNECT") or f"127.0.0.1:{ENV.get('HTTP_PORT', '80')}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Response:
    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status, self.headers, self.body, self.url = status, headers, body, url

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body or b"null")

    @property
    def location(self) -> str:
        loc = self.headers.get("Location", "")
        return urllib.parse.urljoin(self.url, loc) if loc else ""

    def __repr__(self) -> str:
        return f"<{self.status} {self.url} -> {self.location or self.text[:300]!r}>"


class Client:
    """A browser-like client: one cookie jar (by name; the stack's cookie
    names never collide), the public Host header, no automatic redirects."""

    def __init__(self, public_url: str = PUBLIC_URL, connect: str = CONNECT):
        self.public = urllib.parse.urlsplit(public_url)
        self.connect = connect
        self.cookies: dict[str, str] = {}

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.public.scheme}://{self.public.netloc}{path}"

    def request(self, method: str, path: str, data=None, headers: dict | None = None,
                timeout: float = 30) -> Response:
        public_url = self.url(path)
        parts = urllib.parse.urlsplit(public_url)
        if parts.netloc != self.public.netloc:
            raise ValueError(f"refusing to leave the stack: {public_url}")
        target = urllib.parse.urlunsplit(("http", self.connect, parts.path or "/", parts.query, ""))
        body = None
        hdrs = {"Host": self.public.netloc, "Accept-Language": "en", "User-Agent": "headscale-easy-e2e"}
        if isinstance(data, dict | list) and (headers or {}).get("Content-Type") == "application/json":
            body = json.dumps(data).encode()
        elif isinstance(data, dict | list):
            body = urllib.parse.urlencode(data, doseq=True).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif data is not None:
            body = data if isinstance(data, bytes) else str(data).encode()
        if self.cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        hdrs.update(headers or {})
        req = urllib.request.Request(target, data=body, method=method, headers=hdrs)
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                result = Response(resp.status, resp.headers, resp.read(), public_url)
        except urllib.error.HTTPError as exc:
            result = Response(exc.code, exc.headers, exc.read(), public_url)
        for raw in result.headers.get_all("Set-Cookie") or []:
            jar = cookies.SimpleCookie()
            jar.load(raw)
            for name, morsel in jar.items():
                if morsel["max-age"] == "0" or not morsel.value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return result

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, data=None, **kw) -> Response:
        return self.request("POST", path, data=data, **kw)

    # --- web UI ---
    def csrf(self, page: str = "/admin/settings/sessions") -> str:
        resp = self.get(page)
        m = re.search(r'name="csrf" value="([^"]+)"', resp.text)
        if not m:
            raise AssertionError(f"no CSRF token on {page}: {resp!r}")
        return m.group(1)

    def form(self, path: str, fields: dict | None = None, page: str = "/admin/settings/sessions") -> Response:
        """POST a web UI form with this session's CSRF token."""
        return self.post(path, {"csrf": self.csrf(page), **(fields or {})})

    def login_apikey(self, key: str) -> Response:
        return self.post("/admin/login/apikey", {"api_key": key})


class HeadscaleAPI:
    """Headscale's REST API through Caddy (the same path any API client uses)."""

    def __init__(self, key: str, client: Client | None = None):
        self.key, self.client = key, client or Client()

    def call(self, method: str, path: str, body=None) -> Response:
        headers = {"Authorization": f"Bearer {self.key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self.client.request(method, f"/api/v1{path}", data=body, headers=headers)

    def ok(self, method: str, path: str, body=None):
        resp = self.call(method, path, body)
        if resp.status != 200:
            raise AssertionError(f"{method} /api/v1{path}: {resp!r}")
        return resp.json()

    def users(self) -> list[dict]:
        return self.ok("GET", "/user").get("users") or []

    def user(self, name: str) -> dict | None:
        return next((u for u in self.users() if u.get("name") == name), None)

    def nodes(self) -> list[dict]:
        return self.ok("GET", "/node").get("nodes") or []


def wait_for(what: str, check, timeout: float = 120, interval: float = 2):
    """Poll check() until it returns something truthy; fail after timeout."""
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError, AssertionError) as exc:  # connection refused while restarting...
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for {what} (last error: {last})")


def run(*args: str, check: bool = True, timeout: float = 600) -> subprocess.CompletedProcess:
    """Run a command in the project directory and return its output."""
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed ({proc.returncode}):\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return proc


def container_started_at(name: str) -> str:
    return run("docker", "inspect", "-f", "{{.State.StartedAt}}", name).stdout.strip()
