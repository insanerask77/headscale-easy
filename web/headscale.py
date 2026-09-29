"""Data access for Headscale Easy.

Three sources, all read-only except the API:

  - Headscale REST API v1 (with the API key): nodes, users, keys, policy and
    every write operation.
  - Headscale's SQLite database, mounted read-only: the Hostinfo each client
    reports (OS, Tailscale version, DERP relay, endpoints), which API v1 does
    not expose. If it is unavailable (e.g. Headscale on PostgreSQL) those
    columns are simply shown empty.
  - Headscale's config.yaml: the tailnet DNS settings. The web UI rewrites
    only the marked dns: block (see apply_dns).
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from i18n import _

log = logging.getLogger("headscale-easy")

HEADSCALE_URL = os.environ.get("HEADSCALE_URL", "http://headscale:8080").rstrip("/")
HEADSCALE_API_KEY = os.environ["HEADSCALE_API_KEY"]
HEADSCALE_OIDC_ISSUER = os.environ.get("HEADSCALE_OIDC_ISSUER", "")
HEADSCALE_DB = os.environ.get("HEADSCALE_DB", "/headscale/db.sqlite")
HEADSCALE_CONFIG = os.environ.get("HEADSCALE_CONFIG", "/etc/headscale/config.yaml")

# With a self-signed certificate: Caddy's CA, to verify the public OIDC issuer
EXTRA_CA_FILE = os.environ.get("EXTRA_CA_FILE", "")


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if EXTRA_CA_FILE and os.path.isfile(EXTRA_CA_FILE):
        ctx.load_verify_locations(EXTRA_CA_FILE)
    return ctx


TLS = _tls_context()


def http_json(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
              timeout: float = 15) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=TLS) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


# -----------------------------------------------------------------------------
# REST API v1
# -----------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {HEADSCALE_API_KEY}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return http_json(method, f"{HEADSCALE_URL}/api/v1{path}", headers=headers, body=data)


def api_error(exc: Exception) -> str:
    """Readable message from a Headscale API error."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = json.loads(exc.read() or b"{}")
            return body.get("message") or body.get("error") or str(exc)
        except (ValueError, OSError):
            return str(exc)
    return str(exc)


def user_for_sub(sub: str) -> dict | None:
    """The Headscale user behind an OIDC session, or None if they never
    registered a device (Headscale creates the user on that first login).

    Headscale stores providerId = <issuer without trailing '/'>/<sub>. The web
    UI signs in against the same identity provider, so the match is exact and
    does not depend on names someone else could reuse.
    """
    if not HEADSCALE_OIDC_ISSUER or not sub:
        return None
    expected = HEADSCALE_OIDC_ISSUER.rstrip("/") + "/" + sub.lstrip("/")
    for user in all_users():
        if user.get("providerId") == expected:
            return user
    return None


def all_users() -> list[dict]:
    return api("GET", "/user").get("users", [])


def all_nodes() -> list[dict]:
    return api("GET", "/node").get("nodes", [])


def user_nodes(user: dict) -> list[dict]:
    nodes = api("GET", "/node?" + urllib.parse.urlencode({"user": user["name"]})).get("nodes", [])
    # Headscale filters by name; re-check by id in case two users share a name
    # (a local one and an OIDC one).
    return [n for n in nodes if str(n.get("user", {}).get("id")) == str(user["id"])]


def get_node(node_id: str) -> dict | None:
    if not str(node_id).isdigit():
        return None
    try:
        return api("GET", f"/node/{node_id}").get("node")
    except urllib.error.HTTPError:
        return None


def owned_node(user: dict | None, node_id: str) -> dict | None:
    """The node, only if it belongs to the user."""
    if not user or not str(node_id).isdigit():
        return None
    return next((n for n in user_nodes(user) if str(n.get("id")) == str(node_id)), None)


def all_keys() -> list[dict]:
    return api("GET", "/preauthkey").get("preAuthKeys", [])


def user_keys(user: dict) -> list[dict]:
    return [k for k in all_keys() if str((k.get("user") or {}).get("id")) == str(user["id"])]


def owned_key(user: dict | None, key_id: str) -> dict | None:
    if not user or not str(key_id).isdigit():
        return None
    return next((k for k in user_keys(user) if str(k.get("id")) == str(key_id)), None)


def get_policy() -> dict:
    """{'policy': str, 'updatedAt': str}; empty policy if there is none yet."""
    try:
        return api("GET", "/policy")
    except urllib.error.HTTPError:
        return {"policy": "", "updatedAt": ""}


def api_keys() -> list[dict]:
    return api("GET", "/apikey").get("apiKeys", [])


def api_key_prefix(key: str) -> str:
    """hskey-api-<12-character prefix>-<secret>. The prefix itself may contain
    '-', so it is taken by length, not by splitting."""
    head = "hskey-api-"
    return key[len(head):len(head) + 12] if key.startswith(head) and len(key) > len(head) + 12 else ""


def own_api_key_prefix() -> str:
    """Prefix of the API key used by the web UI itself (must not be expired)."""
    return api_key_prefix(HEADSCALE_API_KEY)


# -----------------------------------------------------------------------------
# Database (read-only): Hostinfo and endpoints
# -----------------------------------------------------------------------------

def host_details(node_ids: list[str]) -> dict[str, dict]:
    """{node_id: {"hostinfo": {...}, "endpoints": [...]}} for those nodes.

    Headscale runs SQLite in WAL mode, which allows readers in another process
    with mode=ro as long as the -wal and -shm files exist (Headscale keeps them).
    """
    if not node_ids or not os.path.isfile(HEADSCALE_DB):
        return {}
    try:
        con = sqlite3.connect(f"file:{HEADSCALE_DB}?mode=ro", uri=True, timeout=3)
        try:
            marks = ",".join("?" * len(node_ids))
            rows = con.execute(
                f"SELECT id, host_info, endpoints FROM nodes WHERE id IN ({marks})",
                [int(i) for i in node_ids],
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("could not read Hostinfo from the database: %s", exc)
        return {}

    return {
        str(node_id): {"hostinfo": _loads(host_info, {}), "endpoints": _loads(endpoints, []) or []}
        for node_id, host_info, endpoints in rows
    }


def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# -----------------------------------------------------------------------------
# External data, cached: DERP relay names and the latest Tailscale release
# -----------------------------------------------------------------------------

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, loader, fallback):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    try:
        value = loader()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        log.info("no %s (%s): using the fallback", key, exc)
        value, ttl = fallback, 300  # retry soon
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
    return value


def derp_regions() -> dict[int, str]:
    """{region_id: name}: Headscale's embedded relay (from config.yaml) and
    Tailscale's public ones (when there is Internet access)."""
    def load():
        data = http_json("GET", "https://controlplane.tailscale.com/derpmap/default", timeout=5)
        return {int(k): v.get("RegionName", f"Region {k}") for k, v in data.get("Regions", {}).items()}

    regions = dict(_cached("derpmap", 6 * 3600, load, {}))
    try:
        with open(HEADSCALE_CONFIG, encoding="utf-8") as fh:
            text = fh.read()
        rid = re.search(r"^\s+region_id:\s*(\d+)", text, re.M)
        rname = re.search(r"^\s+region_name:\s*\"?([^\"\n]+)", text, re.M)
        if rid:
            regions[int(rid.group(1))] = rname.group(1).strip() if rname else "Headscale"
    except OSError:
        pass
    return regions


def latest_tailscale_version() -> str:
    """Latest stable Tailscale release, to flag devices that need an update."""
    def load():
        return http_json("GET", "https://pkgs.tailscale.com/stable/?mode=json", timeout=5)["TarballsVersion"]

    return _cached("latest", 6 * 3600, load, "")


# -----------------------------------------------------------------------------
# Docker (Unix socket, standard library only): validate and restart Headscale
# -----------------------------------------------------------------------------

DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
HEADSCALE_CONTAINER = os.environ.get("HEADSCALE_CONTAINER", "headscale")


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 60):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def docker(method: str, path: str, body: dict | None = None, timeout: float = 60) -> tuple[int, bytes]:
    conn = _UnixHTTPConnection(DOCKER_SOCKET, timeout=timeout)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def docker_available() -> bool:
    try:
        return docker("GET", "/_ping", timeout=3)[0] == 200
    except OSError:
        return False


def headscale_configtest() -> tuple[bool, str]:
    """Run 'headscale configtest' inside the container, which reads the same
    mounted config.yaml. Returns (ok, output)."""
    status, raw = docker("POST", f"/containers/{HEADSCALE_CONTAINER}/exec", {
        "AttachStdout": True, "AttachStderr": True, "Tty": True,
        "Cmd": ["headscale", "configtest"],
    })
    if status != 201:
        return False, f"docker exec: HTTP {status}"
    exec_id = json.loads(raw)["Id"]
    _, out = docker("POST", f"/exec/{exec_id}/start", {"Detach": False, "Tty": True}, timeout=90)
    _, info = docker("GET", f"/exec/{exec_id}/json")
    code = json.loads(info).get("ExitCode")
    return code == 0, re.sub(r"\x1b\[[0-9;]*m", "", out.decode(errors="replace")).strip()


def restart_headscale(wait: float = 120) -> bool:
    """Restart Headscale and wait for its healthcheck to report 'healthy'."""
    status, _ = docker("POST", f"/containers/{HEADSCALE_CONTAINER}/restart?t=10", timeout=60)
    if status != 204:
        log.error("could not restart Headscale: HTTP %s", status)
        return False
    deadline = time.time() + wait
    time.sleep(3)
    while time.time() < deadline:
        _, raw = docker("GET", f"/containers/{HEADSCALE_CONTAINER}/json")
        if (json.loads(raw).get("State", {}).get("Health") or {}).get("Status") == "healthy":
            return True
        time.sleep(2)
    return False


# -----------------------------------------------------------------------------
# DNS: marked block in config.yaml
# -----------------------------------------------------------------------------
# install.sh writes the dns: section between these markers and keeps the
# existing block when it regenerates the config, so changes made in the UI
# survive a re-install.

DNS_BEGIN = "# >>> dns: managed by Headscale Easy (do not edit between these markers)"
DNS_END = "# <<< dns"

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_dns_lock = threading.Lock()


def dns_config() -> dict:
    """Read the dns: section of config.yaml.

    There is no YAML parser in the standard library and the section has a fixed
    shape (install.sh and this module write it), so a reader for that subset is
    enough: scalar keys, '- item' lists and inline '[a, b]' lists.
    """
    result = {"magic_dns": None, "base_domain": "", "override_local_dns": True,
              "nameservers": [], "search_domains": [], "split": {}, "extra_records": []}
    try:
        with open(HEADSCALE_CONFIG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return result

    in_dns, path = False, []
    for raw in lines:
        if raw.lstrip().startswith("#"):
            continue
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 0:
            in_dns, path = text == "dns:", []
            continue
        if not in_dns:
            continue
        path = path[:indent // 2 - 1]  # key path from the indentation (2 spaces)
        if path[:1] == ["extra_records"] and (text.startswith("- ") or len(path) >= 1 and indent >= 4):
            # - name: nas.example.com / type: A / value: 100.64.0.5
            if text.startswith("- "):
                result["extra_records"].append({})
                text = text[2:]
            key, _sep, value = text.partition(":")
            if result["extra_records"]:
                result["extra_records"][-1][key.strip()] = value.strip().strip("\"'")
            continue
        if text.startswith("- "):
            item = text[2:].strip().strip("\"'")
            if path[-1:] == ["search_domains"]:
                result["search_domains"].append(item)
            elif path[-2:] == ["nameservers", "global"]:
                result["nameservers"].append(item)
            elif len(path) == 3 and path[:2] == ["nameservers", "split"]:
                result["split"].setdefault(path[2], []).append(item)
            continue
        key, _sep, value = text.partition(":")
        value = value.strip().strip("\"'")
        path.append(key)
        inline = [v.strip().strip("\"'") for v in value.strip("[]").split(",") if v.strip()] \
            if value.startswith("[") else None
        if key == "magic_dns":
            result["magic_dns"] = value.lower() == "true"
        elif key == "base_domain":
            result["base_domain"] = value
        elif key == "override_local_dns":
            result["override_local_dns"] = value.lower() == "true"
        elif key == "search_domains" and inline is not None:
            result["search_domains"] = inline
        elif key == "global" and inline is not None:
            result["nameservers"] += inline
    return result


def valid_domain(value: str) -> bool:
    return bool(_DOMAIN_RE.fullmatch(value.lower()))


def valid_nameserver(value: str) -> bool:
    """An IP address or a DoH resolver (https://...)."""
    if value.startswith("https://"):
        return bool(re.fullmatch(r"https://[\w.-]+(:\d+)?(/[\w./%-]*)?", value))
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def render_dns_block(cfg: dict) -> str:
    def items(values, indent):
        return "".join(f"\n{' ' * indent}- {v}" for v in values) if values else " []"

    split = "".join(f"\n      {domain}:{items(servers, 8)}" for domain, servers in cfg.get("split", {}).items())
    records = "".join(f"\n    - name: {r['name']}\n      type: {r['type']}\n      value: {r['value']}"
                      for r in cfg.get("extra_records", [])) or " []"
    return "\n".join([
        DNS_BEGIN,
        "dns:",
        f"  magic_dns: {'true' if cfg['magic_dns'] else 'false'}",
        f"  base_domain: {cfg['base_domain']}",
        f"  override_local_dns: {'true' if cfg['override_local_dns'] else 'false'}",
        "  nameservers:",
        f"    global:{items(cfg['nameservers'], 6)}",
        f"    split:{split or ' {}'}",
        f"  search_domains:{items(cfg['search_domains'], 4)}",
        f"  extra_records:{records}",
        DNS_END,
    ])


def dns_block_present(text: str) -> bool:
    return DNS_BEGIN in text and DNS_END in text


def replace_dns_block(text: str, block: str) -> str | None:
    """Replace the marked block. None if the file has no markers (a config
    older than this feature: run install.sh once)."""
    start, end = text.find(DNS_BEGIN), text.find(DNS_END)
    if start < 0 or end < start:
        return None
    return text[:start] + block + text[end + len(DNS_END):]


def apply_dns(cfg: dict) -> tuple[bool, str]:
    """Write the DNS block, validate it with 'headscale configtest' and restart
    Headscale. On any failure the previous config is restored."""
    return _apply_config(
        lambda text: replace_dns_block(text, render_dns_block(cfg)),
        _("config.yaml has no managed DNS block. Run ./install.sh once to enable it."),
        _("Headscale did not start with the new DNS settings; the previous ones were restored."))


# -----------------------------------------------------------------------------
# Device key expiry: marked block inside node: in config.yaml
# -----------------------------------------------------------------------------
# Headscale's node.expiry: the key expiry of every new device, whatever the
# registration method. 0 = never (Headscale's default, which is why devices
# showed "Expiry disabled"). Existing devices keep their expiry.

KEY_EXPIRY_BEGIN = "  # >>> key expiry: managed by Headscale Easy (do not edit between these markers)"
KEY_EXPIRY_END = "  # <<< key expiry"
KEY_EXPIRY_MAX_DAYS = 365


def key_expiry_days() -> int | None:
    """Days of the configured node.expiry; 0 = never; None = not managed."""
    try:
        with open(HEADSCALE_CONFIG, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    start, end = text.find(KEY_EXPIRY_BEGIN), text.find(KEY_EXPIRY_END)
    if start < 0 or end < start:
        return None
    m = re.search(r"expiry:\s*\"?([0-9]+)([a-z]*)", text[start:end])
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2) or "d"
    hours = {"d": 24, "h": 1, "w": 168, "m": 1 / 60, "s": 1 / 3600}.get(unit, 24)
    return round(value * hours / 24)


def apply_key_expiry(days: int) -> tuple[bool, str]:
    block = f"{KEY_EXPIRY_BEGIN}\n  expiry: {f'{days}d' if days else '0'}\n{KEY_EXPIRY_END}"

    def change(text: str) -> str | None:
        start, end = text.find(KEY_EXPIRY_BEGIN), text.find(KEY_EXPIRY_END)
        if start < 0 or end < start:
            return None
        return text[:start] + block + text[end + len(KEY_EXPIRY_END):]

    return _apply_config(change,
                         _("config.yaml has no managed key expiry. Run ./install.sh once to enable it."),
                         _("Headscale did not start with the new setting; the previous one was restored."))


def _apply_config(change, missing_msg: str, restart_msg: str) -> tuple[bool, str]:
    """Rewrite config.yaml with change(text), validate it with 'headscale
    configtest' and restart Headscale. On any failure the previous config is
    restored."""
    with _dns_lock:
        with open(HEADSCALE_CONFIG, encoding="utf-8") as fh:
            original = fh.read()
        updated = change(original)
        if updated is None:
            return False, missing_msg

        def write(content: str):
            # In place (no rename): the file is a Docker bind mount
            with open(HEADSCALE_CONFIG, "w", encoding="utf-8") as fh:
                fh.write(content)

        write(updated)
        ok, out = headscale_configtest()
        if not ok:
            write(original)
            detail = out.splitlines()[-1] if out else _("invalid configuration")
            return False, _("Headscale rejected the configuration: {detail}", detail=detail)
        if not restart_headscale():
            write(original)
            restart_headscale()
            return False, restart_msg
        return True, ""
