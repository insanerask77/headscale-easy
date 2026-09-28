"""Acceso a Headscale para el portal Mi VPN.

Tres fuentes, todas de sólo lectura salvo la API:

  - API REST v1 (con la API key): nodos, usuarios, claves y todas las
    operaciones de escritura.
  - Base de datos SQLite, montada en sólo lectura: el Hostinfo que envía cada
    cliente (SO, versión de Tailscale, relay DERP, endpoints), que la API v1
    no expone. Si no está disponible (p. ej. Headscale con PostgreSQL), esas
    columnas simplemente se muestran vacías.
  - config.yaml, montado en sólo lectura: la configuración DNS de la tailnet.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("mi-vpn")

HEADSCALE_URL = os.environ.get("HEADSCALE_URL", "http://headscale:8080").rstrip("/")
HEADSCALE_API_KEY = os.environ["HEADSCALE_API_KEY"]
HEADSCALE_OIDC_ISSUER = os.environ["HEADSCALE_OIDC_ISSUER"]
HEADSCALE_DB = os.environ.get("HEADSCALE_DB", "/headscale/db.sqlite")
HEADSCALE_CONFIG = os.environ.get("HEADSCALE_CONFIG", "/etc/headscale/config.yaml")

# Con SSL_MODE=selfsigned: CA de Caddy para validar la URL pública de Authentik
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
# API REST v1
# -----------------------------------------------------------------------------

def api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {HEADSCALE_API_KEY}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return http_json(method, f"{HEADSCALE_URL}/api/v1{path}", headers=headers, body=data)


def user_for_sub(sub: str) -> dict | None:
    """Usuario de Headscale del dueño de la sesión, o None si todavía no ha
    registrado ningún dispositivo (Headscale lo crea en ese primer login).

    Headscale guarda providerId = <issuer sin '/' final>/<sub>, y el 'sub' de
    Authentik (sub_mode=hashed_user_id) es igual en todos sus proveedores: la
    coincidencia es exacta y no depende de nombres que otro pudiera reutilizar.
    """
    expected = HEADSCALE_OIDC_ISSUER.rstrip("/") + "/" + sub.lstrip("/")
    for user in api("GET", "/user").get("users", []):
        if user.get("providerId") == expected:
            return user
    return None


def user_nodes(user: dict) -> list[dict]:
    nodes = api("GET", "/node?" + urllib.parse.urlencode({"user": user["name"]})).get("nodes", [])
    # El filtro por nombre lo hace Headscale; se revalida por id por si hubiera
    # dos usuarios con el mismo nombre (uno de CLI y otro de OIDC).
    return [n for n in nodes if str(n.get("user", {}).get("id")) == str(user["id"])]


def owned_node(user: dict | None, node_id: str) -> dict | None:
    """El nodo, sólo si pertenece al usuario. Toda operación pasa por aquí."""
    if not user or not str(node_id).isdigit():
        return None
    return next((n for n in user_nodes(user) if str(n.get("id")) == str(node_id)), None)


def user_keys(user: dict) -> list[dict]:
    keys = api("GET", "/preauthkey").get("preAuthKeys", [])
    return [k for k in keys if str((k.get("user") or {}).get("id")) == str(user["id"])]


def owned_key(user: dict | None, key_id: str) -> dict | None:
    if not user or not str(key_id).isdigit():
        return None
    return next((k for k in user_keys(user) if str(k.get("id")) == str(key_id)), None)


# -----------------------------------------------------------------------------
# Base de datos (sólo lectura): Hostinfo, endpoints
# -----------------------------------------------------------------------------

def host_details(node_ids: list[str]) -> dict[str, dict]:
    """{node_id: {"hostinfo": {...}, "endpoints": [...]}} para esos nodos.

    Headscale usa SQLite en modo WAL, que admite lectores en otro proceso con
    mode=ro mientras existan los ficheros -wal y -shm (Headscale los mantiene).
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
        log.warning("no se pudo leer el Hostinfo de la base de datos: %s", exc)
        return {}

    out = {}
    for node_id, host_info, endpoints in rows:
        out[str(node_id)] = {
            "hostinfo": _loads(host_info, {}),
            "endpoints": _loads(endpoints, []) or [],
        }
    return out


def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# -----------------------------------------------------------------------------
# config.yaml (sólo lectura): DNS de la tailnet
# -----------------------------------------------------------------------------

def dns_config() -> dict:
    """Lee la sección dns: de config.yaml.

    No hay parser YAML en la biblioteca estándar y la sección tiene una forma
    fija (la genera install.sh), así que basta con un lector de ese subconjunto:
    claves escalares, listas con '- item' y listas en línea '[a, b]'.
    """
    result = {"magic_dns": None, "base_domain": "", "override_local_dns": True,
              "nameservers": [], "search_domains": [], "split": {}}
    try:
        with open(HEADSCALE_CONFIG, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return result

    in_dns, path = False, []
    for raw in lines:
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 0:
            in_dns = text == "dns:"
            path = []
            continue
        if not in_dns:
            continue
        # Mantener la ruta de claves según la indentación (2 espacios)
        level = indent // 2 - 1
        path = path[:level]
        if text.startswith("- "):
            item = text[2:].strip().strip("\"'")
            if path[-1:] == ["search_domains"]:
                result["search_domains"].append(item)
            elif path[-2:] == ["nameservers", "global"]:
                result["nameservers"].append(item)
            elif len(path) == 3 and path[:2] == ["nameservers", "split"]:
                result["split"].setdefault(path[2], []).append(item)
            continue
        key, _, value = text.partition(":")
        value = value.strip().strip("\"'")
        path.append(key)
        if key == "magic_dns":
            result["magic_dns"] = value.lower() == "true"
        elif key == "base_domain":
            result["base_domain"] = value
        elif key == "override_local_dns":
            result["override_local_dns"] = value.lower() == "true"
        elif key == "search_domains" and value.startswith("["):
            result["search_domains"] = [v.strip().strip("\"'") for v in value.strip("[]").split(",") if v.strip()]
        elif key == "global" and value.startswith("["):
            result["nameservers"] += [v.strip().strip("\"'") for v in value.strip("[]").split(",") if v.strip()]
    return result


# -----------------------------------------------------------------------------
# Datos externos cacheados: nombres de relays DERP y última versión estable
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
        log.info("sin %s (%s): se usa el valor por defecto", key, exc)
        value = fallback
        ttl = 300  # reintentar pronto
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
    return value


def derp_regions() -> dict[int, str]:
    """{region_id: nombre} de los relays: el embebido de Headscale (de
    config.yaml) y los públicos de Tailscale (si hay salida a internet)."""
    def load():
        data = http_json("GET", "https://controlplane.tailscale.com/derpmap/default", timeout=5)
        return {int(k): v.get("RegionName", f"Región {k}") for k, v in data.get("Regions", {}).items()}

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
    """Última versión estable de Tailscale, para avisar de actualizaciones."""
    def load():
        data = http_json("GET", "https://pkgs.tailscale.com/stable/?mode=json", timeout=5)
        return data["TarballsVersion"]

    return _cached("latest", 6 * 3600, load, "")


# -----------------------------------------------------------------------------
# Administración (sólo se llama con sesión de admin; app.py lo comprueba)
# -----------------------------------------------------------------------------

def api_error(exc: Exception) -> str:
    """Mensaje legible de un error de la API de Headscale."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = json.loads(exc.read() or b"{}")
            return body.get("message") or body.get("error") or str(exc)
        except (ValueError, OSError):
            return str(exc)
    return str(exc)


def all_users() -> list[dict]:
    return api("GET", "/user").get("users", [])


def all_nodes() -> list[dict]:
    return api("GET", "/node").get("nodes", [])


def get_node(node_id: str) -> dict | None:
    if not str(node_id).isdigit():
        return None
    try:
        return api("GET", f"/node/{node_id}").get("node")
    except urllib.error.HTTPError:
        return None


def all_keys() -> list[dict]:
    return api("GET", "/preauthkey").get("preAuthKeys", [])


def get_policy() -> dict:
    """{'policy': str, 'updatedAt': str}; policy vacío si aún no hay ninguna."""
    try:
        return api("GET", "/policy")
    except urllib.error.HTTPError:
        return {"policy": "", "updatedAt": ""}


def api_keys() -> list[dict]:
    return api("GET", "/apikey").get("apiKeys", [])


def own_api_key_prefix() -> str:
    """Prefijo de la API key que usa el propio portal (no se debe caducar)."""
    parts = HEADSCALE_API_KEY.split("-")
    # hskey-api-<prefijo>-<secreto>
    return parts[2] if len(parts) >= 4 else ""


# -----------------------------------------------------------------------------
# Docker (socket Unix, sólo biblioteca estándar): reiniciar y probar Headscale
# -----------------------------------------------------------------------------

import http.client  # noqa: E402
import socket  # noqa: E402

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
    """Ejecuta 'headscale configtest' dentro del contenedor, que lee el mismo
    config.yaml montado. Devuelve (ok, salida)."""
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
    text = re.sub(r"\x1b\[[0-9;]*m", "", out.decode(errors="replace")).strip()
    return code == 0, text


def restart_headscale(wait: float = 120) -> bool:
    """Reinicia Headscale y espera a que su healthcheck vuelva a 'healthy'."""
    status, _ = docker("POST", f"/containers/{HEADSCALE_CONTAINER}/restart?t=10", timeout=60)
    if status != 204:
        log.error("no se pudo reiniciar Headscale: HTTP %s", status)
        return False
    deadline = time.time() + wait
    time.sleep(3)
    while time.time() < deadline:
        _, raw = docker("GET", f"/containers/{HEADSCALE_CONTAINER}/json")
        state = json.loads(raw).get("State", {})
        if (state.get("Health") or {}).get("Status") == "healthy":
            return True
        time.sleep(2)
    return False


# -----------------------------------------------------------------------------
# DNS editable: bloque delimitado en config.yaml
# -----------------------------------------------------------------------------
# install.sh escribe la sección dns: entre estos marcadores y, al regenerar la
# configuración, conserva el bloque existente. Así lo que se cambie aquí no se
# pierde al reejecutar el instalador.

DNS_BEGIN = "# >>> dns: gestionado por Mi VPN (no edites entre estos marcadores a mano)"
DNS_END = "# <<< dns"

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_dns_lock = threading.Lock()


def valid_domain(value: str) -> bool:
    return bool(_DOMAIN_RE.fullmatch(value.lower()))


def valid_nameserver(value: str) -> bool:
    """IP o resolvedor DoH (https://...)."""
    if value.startswith("https://"):
        return bool(re.fullmatch(r"https://[\w.-]+(:\d+)?(/[\w./%-]*)?", value))
    try:
        import ipaddress
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def render_dns_block(cfg: dict) -> str:
    def items(values, indent):
        return "".join(f"\n{' ' * indent}- {v}" for v in values) if values else " []"

    split = ""
    for domain, servers in cfg.get("split", {}).items():
        split += f"\n      {domain}:{items(servers, 8)}"
    return "\n".join([
        DNS_BEGIN,
        "dns:",
        f"  magic_dns: {'true' if cfg['magic_dns'] else 'false'}",
        f"  base_domain: {cfg['base_domain']}",
        f"  override_local_dns: {'true' if cfg['override_local_dns'] else 'false'}",
        "  nameservers:",
        f"    global:{items(cfg['nameservers'], 6)}",
        f"    split:{split if split else ' {}'}",
        f"  search_domains:{items(cfg['search_domains'], 4)}",
        "  extra_records: []",
        DNS_END,
    ])


def replace_dns_block(text: str, block: str) -> str | None:
    """Sustituye el bloque marcado. None si el fichero no tiene marcadores
    (configuración anterior a este cambio: hay que reejecutar install.sh)."""
    start, end = text.find(DNS_BEGIN), text.find(DNS_END)
    if start < 0 or end < start:
        return None
    return text[:start] + block + text[end + len(DNS_END):]


def apply_dns(cfg: dict) -> tuple[bool, str]:
    """Escribe el DNS, lo valida con 'headscale configtest' y reinicia
    Headscale. Ante cualquier fallo restaura la configuración anterior."""
    with _dns_lock:
        with open(HEADSCALE_CONFIG, encoding="utf-8") as fh:
            original = fh.read()
        updated = replace_dns_block(original, render_dns_block(cfg))
        if updated is None:
            return False, ("config.yaml no tiene el bloque DNS gestionado. "
                           "Reejecuta ./install.sh una vez para activarlo.")

        def write(content: str):
            # En sitio (no rename): el fichero es un bind mount de Docker
            with open(HEADSCALE_CONFIG, "w", encoding="utf-8") as fh:
                fh.write(content)

        write(updated)
        ok, out = headscale_configtest()
        if not ok:
            write(original)
            detail = out.splitlines()[-1] if out else "configuración no válida"
            return False, f"Headscale rechazó la configuración: {detail}"
        if not restart_headscale():
            write(original)
            restart_headscale()
            return False, "Headscale no arrancó con el nuevo DNS; se ha restaurado el anterior."
        return True, ""
