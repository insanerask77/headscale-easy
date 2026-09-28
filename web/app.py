"""Mi VPN: el panel de la VPN, con la estructura de la consola de Tailscale.

  - Usuarios normales: ven y gestionan sólo SUS dispositivos y claves.
  - Administradores: todos los dispositivos, usuarios, rutas, tags, política
    de Control de acceso, DNS y API keys. Sustituye a Headplane.

Inicio de sesión:
  - OIDC (Authentik o el proveedor propio de Headscale), código + PKCE. El rol
    sale de los grupos (PORTAL_ADMIN_GROUPS) o del email (PORTAL_ADMIN_EMAILS).
  - API key de Headscale (PORTAL_API_KEY_LOGIN=true): sesión de administrador,
    para instalaciones sin OIDC o como acceso de emergencia.

Toda operación comprueba en el servidor el rol y, para usuarios normales, que
el dispositivo o la clave es suyo. Sólo biblioteca estándar de Python.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mi-vpn")

import headscale as hs  # noqa: E402  (después de configurar el logging)
import views  # noqa: E402
import views_admin  # noqa: E402

# -----------------------------------------------------------------------------
# Configuración (entorno)
# -----------------------------------------------------------------------------


def _csv(name: str, default: str = "") -> set[str]:
    return {x.strip() for x in os.environ.get(name, default).split(",") if x.strip()}


PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
BASE = views.BASE
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
SSO = bool(OIDC_ISSUER and OIDC_CLIENT_ID)
API_KEY_LOGIN = os.environ.get("PORTAL_API_KEY_LOGIN", "false").lower() == "true" or not SSO
ADMIN_GROUPS = _csv("PORTAL_ADMIN_GROUPS", "vpn-admins,authentik Admins")
ADMIN_EMAILS = {e.lower() for e in _csv("PORTAL_ADMIN_EMAILS")}
SESSION_SECRET = os.environ["SESSION_SECRET"].encode()
TAILNET_NAME = os.environ.get("TAILNET_NAME", "")
AUTHENTIK = "/authentik/" in OIDC_ISSUER

SECURE_COOKIES = PUBLIC_URL.startswith("https://")
SESSION_TTL = 8 * 3600
STATIC_DIR = Path(__file__).parent / "static"
REDIRECT_URI = f"{PUBLIC_URL}{BASE}/callback"
SERVER_HOST = urllib.parse.urlparse(PUBLIC_URL).hostname or ""
CTX = {"public_url": PUBLIC_URL, "tailnet": TAILNET_NAME, "authentik": AUTHENTIK, "server_host": SERVER_HOST}

# Nombres válidos: given name de nodo (etiqueta DNS) y usuario de Headscale
NODE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}$")
TAG_RE = re.compile(r"^tag:[a-z0-9][a-z0-9-]{0,62}$")
AUTH_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{8,200}$")
KEY_DAYS = {"1", "7", "30", "90"}
APIKEY_DAYS = {"30", "90", "365"}
EXIT_ROUTES = ["0.0.0.0/0", "::/0"]


# -----------------------------------------------------------------------------
# Cookies firmadas
# -----------------------------------------------------------------------------

def sign(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    mac = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def unsign(value: str | None) -> dict | None:
    if not value or "." not in value:
        return None
    payload, mac = value.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


_discovery: dict | None = None


def discovery() -> dict:
    """Configuración OIDC del proveedor (se cachea tras la primera lectura)."""
    global _discovery
    if _discovery is None:
        _discovery = hs.http_json("GET", OIDC_ISSUER.rstrip("/") + "/.well-known/openid-configuration")
    return _discovery


# -----------------------------------------------------------------------------
# Datos para las vistas
# -----------------------------------------------------------------------------

def to_machines(nodes: list[dict]) -> list[views.Machine]:
    details = hs.host_details([str(n["id"]) for n in nodes])
    dns = hs.dns_config()
    latest = hs.latest_tailscale_version()
    regions = hs.derp_regions()
    machines = [views.Machine(n, details.get(str(n["id"])), dns, latest, regions) for n in nodes]
    return sorted(machines, key=lambda m: (not m.online, m.name))


def my_user(session: dict) -> dict | None:
    """Usuario de Headscale de la sesión (None para sesiones con API key)."""
    if not session.get("sub"):
        return None
    return hs.user_for_sub(session["sub"])


def visible_nodes(session: dict) -> list[dict]:
    if session.get("admin"):
        return hs.all_nodes()
    user = my_user(session)
    return hs.user_nodes(user) if user else []


def node_for(session: dict, node_id: str) -> dict | None:
    """El nodo si la sesión puede gestionarlo: cualquiera para un admin, sólo
    los propios para un usuario. Toda acción sobre un nodo pasa por aquí."""
    if session.get("admin"):
        return hs.get_node(node_id)
    return hs.owned_node(my_user(session), node_id)


def dns_ctx() -> dict:
    """¿Se puede editar el DNS desde aquí? Requiere el socket de Docker y el
    bloque DNS marcado en config.yaml."""
    ctx = dict(CTX)
    try:
        with open(hs.HEADSCALE_CONFIG, encoding="utf-8") as fh:
            marked = hs.DNS_BEGIN in fh.read()
        writable = os.access(hs.HEADSCALE_CONFIG, os.W_OK)
    except OSError:
        marked = writable = False
    if not marked:
        ctx["dns_reason"] = "config.yaml no tiene el bloque DNS gestionado: reejecuta ./install.sh una vez para activarlo."
    elif not writable:
        ctx["dns_reason"] = "Mi VPN no tiene permiso de escritura sobre config.yaml."
    elif not hs.docker_available():
        ctx["dns_reason"] = "Mi VPN no tiene acceso a Docker para reiniciar Headscale."
    ctx["dns_editable"] = marked and writable and "dns_reason" not in ctx
    return ctx


def lines(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,]", value or "") if x.strip()]


# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "mi-vpn"
    sys_version = ""

    # --- respuesta ---
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

    def fail(self, status: int, title: str, text: str):
        self.send(status, views.message_page(title, text))

    def cookie(self, name: str) -> str | None:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar[name].value if name in jar else None

    @staticmethod
    def set_cookie(name: str, value: str, max_age: int) -> tuple[str, str]:
        attrs = [f"{name}={value}", f"Path={BASE}", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if SECURE_COOKIES:
            attrs.append("Secure")
        return ("Set-Cookie", "; ".join(attrs))

    def session(self) -> dict | None:
        return unsign(self.cookie("mivpn_session"))

    def form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 262144:
            return {}
        data = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        # 'route' puede repetirse (checkboxes)
        return {k: (v if k == "route" else v[0]) for k, v in data.items()}

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # --- GET ---
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        flash = params.get("m", "")
        try:
            if path == f"{BASE}/healthz":
                return self.send(200, "ok", "text/plain")
            if path.startswith(f"{BASE}/static/"):
                return self.static(path[len(f"{BASE}/static/"):])
            if path == f"{BASE}/login":
                if SSO and not API_KEY_LOGIN:
                    return self.start_sso()
                return self.send(200, views_admin.login_page(SSO, API_KEY_LOGIN))
            if path == f"{BASE}/login/sso" and SSO:
                return self.start_sso()
            if path == f"{BASE}/callback" and SSO:
                return self.callback(params)

            session = self.session()
            if not session:
                return self.redirect(f"{BASE}/login")
            admin = session.get("admin")

            if path in (BASE, f"{BASE}/"):
                return self.redirect(f"{BASE}/machines")
            if path == f"{BASE}/machines":
                users = hs.all_users() if admin else None
                has_user = True if admin else my_user(session) is not None
                page = views.machines_page(session, CTX, to_machines(visible_nodes(session)), has_user, flash, users)
                return self.send(200, page)
            m = re.fullmatch(rf"{BASE}/machines/(\d+)", path)
            if m:
                node = node_for(session, m.group(1))
                if node is None:
                    return self.redirect(f"{BASE}/machines?m=not-found")
                return self.send(200, views.machine_page(session, CTX, to_machines([node])[0], flash))
            if path == f"{BASE}/add":
                return self.send(200, views.add_page(session, CTX))
            if path == f"{BASE}/dns":
                ctx = dns_ctx() if admin else CTX
                return self.send(200, views.dns_page(session, ctx, hs.dns_config(),
                                                     to_machines(visible_nodes(session)), flash=flash))
            if path in (f"{BASE}/settings", f"{BASE}/settings/"):
                return self.redirect(f"{BASE}/settings/general")
            if path == f"{BASE}/settings/general":
                return self.send(200, views.general_page(session, CTX))
            if path == f"{BASE}/settings/keys":
                return self.keys_view(session, flash)

            # --- sólo admins ---
            if path in (f"{BASE}/users", f"{BASE}/acl") and not admin:
                return self.fail(403, "Sin permiso", "Esta sección es sólo para administradores.")
            if path == f"{BASE}/users":
                return self.send(200, views_admin.users_page(session, CTX, hs.all_users(), hs.all_nodes(), flash))
            if path == f"{BASE}/acl":
                return self.send(200, views_admin.acl_page(session, CTX, hs.get_policy(), flash))
            self.fail(404, "No encontrado", "Esa página no existe.")
        except Exception:  # noqa: BLE001 - la UI no debe mostrar trazas
            log.exception("error en GET %s", path)
            self.fail(500, "Algo ha fallado", "No se pudo completar la operación. Vuelve a intentarlo en unos segundos.")

    def keys_view(self, session: dict, flash: str, new_key: dict | None = None, new_apikey: str = ""):
        if session.get("admin"):
            page = views.keys_page(session, CTX, hs.all_keys(), flash, new_key, users=hs.all_users(),
                                   apikeys=hs.api_keys(), own_prefix=hs.own_api_key_prefix(), new_apikey=new_apikey)
        else:
            user = my_user(session)
            page = views.keys_page(session, CTX, hs.user_keys(user) if user else None, flash, new_key)
        self.send(200, page)

    # --- POST ---
    def do_POST(self):
        path = self.path.partition("?")[0]
        try:
            if path == f"{BASE}/login/apikey" and API_KEY_LOGIN:
                return self.apikey_login(self.form())

            session = self.session()
            if not session:
                return self.redirect(f"{BASE}/login")
            form = self.form()
            # CSRF: el token del formulario debe coincidir con el de la sesión
            if not hmac.compare_digest(str(form.get("csrf", "")), session["csrf"]):
                return self.fail(403, "Sesión caducada", "Recarga la página e inténtalo de nuevo.")

            if path == f"{BASE}/logout":
                return self.logout(session)
            if path == f"{BASE}/keys":
                return self.create_key(session, form)
            m = re.fullmatch(rf"{BASE}/keys/(\d+)/revoke", path)
            if m:
                return self.revoke_key(session, m.group(1))
            m = re.fullmatch(rf"{BASE}/machines/(\d+)/(rename|delete|expire|expiry|routes|tags)", path)
            if m:
                return self.machine_action(session, m.group(1), m.group(2), form)

            # --- sólo admins ---
            if not session.get("admin"):
                return self.fail(403, "Sin permiso", "Esta acción es sólo para administradores.")
            if path == f"{BASE}/machines/register":
                return self.register_node(form)
            if path == f"{BASE}/users":
                return self.create_user(session, form)
            m = re.fullmatch(rf"{BASE}/users/(\d+)/(rename|delete)", path)
            if m:
                return self.user_action(session, m.group(1), m.group(2), form)
            if path == f"{BASE}/acl":
                return self.save_acl(session, form)
            if path == f"{BASE}/dns":
                return self.save_dns(session, form)
            if path == f"{BASE}/apikeys":
                return self.create_apikey(session, form)
            m = re.fullmatch(rf"{BASE}/apikeys/(\d+)/expire", path)
            if m:
                return self.expire_apikey(m.group(1))
            self.send(404, "No encontrado", "text/plain")
        except Exception:  # noqa: BLE001
            log.exception("error en POST %s", path)
            self.redirect(f"{BASE}/machines?m=failed")

    # --- estáticos ---
    def static(self, name: str):
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self.send(404, "No encontrado", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        # Las URLs llevan ?v=<hash del contenido>: se pueden cachear sin límite
        self.send(200, target.read_bytes(), ctype, [("Cache-Control", "public, max-age=31536000, immutable")])

    # --- inicio de sesión ---
    def start_sso(self):
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        url = discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": OIDC_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid profile email",
            "state": state,
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        tx = sign({"state": state, "verifier": verifier, "exp": time.time() + 600})
        self.redirect(url, [self.set_cookie("mivpn_oidc", tx, 600)])

    def callback(self, params: dict):
        tx = unsign(self.cookie("mivpn_oidc"))
        if not tx or not params.get("code") or not hmac.compare_digest(params.get("state", ""), tx["state"]):
            return self.fail(400, "No se pudo iniciar sesión", "El inicio de sesión caducó o no es válido. Vuelve a intentarlo.")

        d = discovery()
        basic = base64.b64encode(f"{OIDC_CLIENT_ID}:{OIDC_CLIENT_SECRET}".encode()).decode()
        token = hs.http_json("POST", d["token_endpoint"], headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, body=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": tx["verifier"],
        }).encode())
        # La identidad se toma de userinfo, pedido directamente al proveedor
        # con el access token: no hace falta validar la firma del ID token.
        info = hs.http_json("GET", d["userinfo_endpoint"], headers={"Authorization": f"Bearer {token['access_token']}"})
        groups = sorted(info.get("groups") or [])
        email = (info.get("email") or "").lower()
        admin = bool(set(groups) & ADMIN_GROUPS) or (email and email in ADMIN_EMAILS)
        self.start_session({
            "kind": "oidc",
            "sub": info["sub"],
            "username": info.get("preferred_username", ""),
            "name": info.get("name", ""),
            "email": info.get("email", ""),
            "groups": groups,
            "admin": bool(admin),
            "idt": token.get("id_token", ""),
        })

    def apikey_login(self, form: dict):
        key = str(form.get("api_key", "")).strip()
        valid = False
        if key.startswith("hskey-api-") and len(key) < 200:
            try:
                hs.http_json("GET", f"{hs.HEADSCALE_URL}/api/v1/apikey",
                             headers={"Authorization": f"Bearer {key}"}, timeout=5)
                valid = True
            except (urllib.error.URLError, OSError):
                valid = False
        if not valid:
            time.sleep(1)  # frena intentos de adivinar
            log.warning("login con API key rechazado desde %s", self.address_string())
            return self.send(401, views_admin.login_page(SSO, API_KEY_LOGIN, "API key no válida o caducada."))
        log.info("login con API key (%s…)", key[:14])
        self.start_session({"kind": "apikey", "sub": "", "username": "admin", "name": "Administrador",
                            "email": "", "groups": [], "admin": True})

    def start_session(self, data: dict):
        data.update(csrf=secrets.token_urlsafe(24), exp=time.time() + SESSION_TTL)
        log.info("login: %s (%s%s)", data["username"] or data["name"], data["kind"], ", admin" if data["admin"] else "")
        self.redirect(f"{BASE}/machines", [
            self.set_cookie("mivpn_session", sign(data), SESSION_TTL),
            self.set_cookie("mivpn_oidc", "", 0),
        ])

    def logout(self, session: dict):
        # Con OIDC cierra también la sesión del proveedor; si no, "Cerrar
        # sesión" no permitiría entrar con otro usuario en el mismo navegador.
        target = f"{BASE}/login"
        if session.get("kind") == "oidc" and SSO:
            end = discovery().get("end_session_endpoint")
            if end:
                target = end + "?" + urllib.parse.urlencode({
                    "id_token_hint": session.get("idt", ""),
                    "post_logout_redirect_uri": f"{PUBLIC_URL}{BASE}/",
                    "client_id": OIDC_CLIENT_ID,
                })
        self.redirect(target, [self.set_cookie("mivpn_session", "", 0)])

    # --- dispositivos ---
    def machine_action(self, session: dict, node_id: str, action: str, form: dict):
        # Tras la acción se vuelve a donde estaba el usuario (lista o detalle)
        back = str(form.get("back", "machines"))
        if not re.fullmatch(r"machines(/\d+)?", back) or action == "delete":
            back = "machines"
        dest = f"{BASE}/{back}"

        node = node_for(session, node_id)
        if node is None:
            log.warning("%s intentó '%s' sobre el nodo %s, que no puede gestionar",
                        session["username"], action, node_id)
            return self.redirect(f"{BASE}/machines?m=not-found")
        admin_only = {"expiry", "routes", "tags"}
        if action in admin_only and not session.get("admin"):
            return self.redirect(f"{dest}?m=forbidden")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not NODE_NAME_RE.fullmatch(name):
                    return self.redirect(f"{dest}?m=bad-name")
                hs.api("POST", f"/node/{node_id}/rename/{urllib.parse.quote(name)}")
                return self.redirect(f"{dest}?m=renamed")
            if action == "expire":
                hs.api("POST", f"/node/{node_id}/expire")
                return self.redirect(f"{dest}?m=expired")
            if action == "expiry":
                if form.get("disable") == "1":
                    hs.api("POST", f"/node/{node_id}/expire?disableExpiry=true")
                    return self.redirect(f"{dest}?m=expiry-off")
                # Reactivar: caducidad dentro de 180 días (el valor por defecto de Tailscale)
                until = (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
                hs.api("POST", f"/node/{node_id}/expire?" + urllib.parse.urlencode({"expiry": until}))
                return self.redirect(f"{dest}?m=expiry-on")
            if action == "routes":
                # Sólo se pueden aprobar rutas que el nodo anuncia
                available = set(node.get("availableRoutes") or [])
                chosen = form.get("route") or []
                routes = [r for r in chosen if r in available]
                if "exit" in chosen:
                    routes += [r for r in EXIT_ROUTES if r in available]
                hs.api("POST", f"/node/{node_id}/approve_routes", {"routes": sorted(set(routes))})
                log.info("%s aprobó rutas %s en el nodo %s", session["username"], routes, node_id)
                return self.redirect(f"{dest}?m=routes")
            if action == "tags":
                tags = [t.lower() for t in lines(str(form.get("tags", "")))]
                if any(not TAG_RE.fullmatch(t) for t in tags):
                    return self.redirect(f"{dest}?m=failed")
                hs.api("POST", f"/node/{node_id}/tags", {"tags": tags})
                return self.redirect(f"{dest}?m=tags")
            hs.api("DELETE", f"/node/{node_id}")
            log.info("%s quitó el nodo %s", session["username"], node_id)
            return self.redirect(f"{BASE}/machines?m=removed")
        except urllib.error.HTTPError as exc:
            msg = hs.api_error(exc)
            log.warning("headscale rechazó '%s' sobre %s: %s", action, node_id, msg)
            if action == "tags":
                # El motivo (p. ej. tag sin dueño en tagOwners) es útil: se muestra
                page = views.machine_page(session, CTX, to_machines([node])[0], "")
                return self.send(400, page.replace('<main class="container">',
                                 f'<main class="container"><div class="notice error">No se pudieron guardar los tags: {views.esc(msg)}</div>', 1))
            return self.redirect(f"{dest}?m=failed")

    def register_node(self, form: dict):
        auth_id = str(form.get("auth_id", "")).strip()
        # Admite la URL completa que imprime 'tailscale up'
        auth_id = auth_id.rstrip("/").rsplit("/", 1)[-1]
        user = str(form.get("user", ""))
        if not AUTH_ID_RE.fullmatch(auth_id) or user not in {u["name"] for u in hs.all_users()}:
            return self.redirect(f"{BASE}/machines?m=failed")
        try:
            hs.api("POST", "/auth/register", {"user": user, "authId": auth_id})
        except urllib.error.HTTPError as exc:
            msg = hs.api_error(exc)
            log.warning("registro con Auth ID rechazado: %s", msg)
            session = self.session()
            page = views.machines_page(session, CTX, to_machines(visible_nodes(session)), True, "",
                                       hs.all_users(), error=f"No se pudo registrar: {msg}")
            return self.send(400, page)
        return self.redirect(f"{BASE}/machines?m=registered")

    # --- claves ---
    def create_key(self, session: dict, form: dict):
        if session.get("admin"):
            uid = str(form.get("user_id", ""))
            user = next((u for u in hs.all_users() if str(u["id"]) == uid), None)
        else:
            user = my_user(session)
        if not user:
            return self.redirect(f"{BASE}/settings/keys?m=no-user")
        days = str(form.get("days", "90"))
        if days not in KEY_DAYS:
            days = "90"
        expiration = (datetime.now(timezone.utc) + timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")
        key = hs.api("POST", "/preauthkey", {
            "user": str(user["id"]),
            "reusable": form.get("reusable") == "1",
            "ephemeral": form.get("ephemeral") == "1",
            "expiration": expiration,
        })["preAuthKey"]
        log.info("%s generó una clave para %s (reusable=%s, efímera=%s, %s d)", session["username"],
                 user["name"], key.get("reusable"), key.get("ephemeral"), days)
        # Se muestra en esta misma respuesta: Headscale no vuelve a darla entera
        self.keys_view(session, "", new_key=key)

    def revoke_key(self, session: dict, key_id: str):
        if session.get("admin"):
            ok = any(str(k.get("id")) == key_id for k in hs.all_keys())
        else:
            ok = hs.owned_key(my_user(session), key_id) is not None
        if not ok:
            log.warning("%s intentó revocar la clave %s, que no puede gestionar", session["username"], key_id)
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        hs.api("POST", "/preauthkey/expire", {"id": key_id})
        return self.redirect(f"{BASE}/settings/keys?m=key-revoked")

    def create_apikey(self, session: dict, form: dict):
        days = str(form.get("days", "90"))
        if days not in APIKEY_DAYS:
            days = "90"
        expiration = (datetime.now(timezone.utc) + timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")
        key = hs.api("POST", "/apikey", {"expiration": expiration})["apiKey"]
        log.info("%s creó una API key (%s d)", session["username"], days)
        self.keys_view(session, "", new_apikey=key)

    def expire_apikey(self, key_id: str):
        key = next((k for k in hs.api_keys() if str(k.get("id")) == key_id), None)
        if key is None:
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        own = hs.own_api_key_prefix()
        if own and own in (key.get("prefix") or ""):
            return self.redirect(f"{BASE}/settings/keys?m=apikey-own")
        hs.api("POST", "/apikey/expire", {"id": key_id})
        return self.redirect(f"{BASE}/settings/keys?m=apikey-expired")

    # --- usuarios ---
    def create_user(self, session: dict, form: dict):
        name = str(form.get("name", "")).strip().lower()
        if not USER_NAME_RE.fullmatch(name):
            return self.redirect(f"{BASE}/users?m=bad-user")
        body = {"name": name}
        if form.get("display_name"):
            body["displayName"] = str(form["display_name"]).strip()[:100]
        try:
            hs.api("POST", "/user", body)
        except urllib.error.HTTPError as exc:
            return self.send(400, views_admin.users_page(session, CTX, hs.all_users(), hs.all_nodes(), "",
                                                         error=f"No se pudo crear: {hs.api_error(exc)}"))
        log.info("%s creó el usuario local %s", session["username"], name)
        return self.redirect(f"{BASE}/users?m=user-created")

    def user_action(self, session: dict, user_id: str, action: str, form: dict):
        if not any(str(u["id"]) == user_id for u in hs.all_users()):
            return self.redirect(f"{BASE}/users?m=not-found")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not USER_NAME_RE.fullmatch(name):
                    return self.redirect(f"{BASE}/users?m=bad-user")
                hs.api("POST", f"/user/{user_id}/rename/{urllib.parse.quote(name)}")
                return self.redirect(f"{BASE}/users?m=user-renamed")
            if any(str((n.get("user") or {}).get("id")) == user_id for n in hs.all_nodes()):
                return self.redirect(f"{BASE}/users?m=user-has-nodes")
            hs.api("DELETE", f"/user/{user_id}")
            log.info("%s eliminó el usuario %s", session["username"], user_id)
            return self.redirect(f"{BASE}/users?m=user-deleted")
        except urllib.error.HTTPError as exc:
            return self.send(400, views_admin.users_page(session, CTX, hs.all_users(), hs.all_nodes(), "",
                                                         error=hs.api_error(exc)))

    # --- política ---
    def save_acl(self, session: dict, form: dict):
        policy = str(form.get("policy", ""))
        action = form.get("action", "check")
        try:
            if action == "save":
                hs.api("PUT", "/policy", {"policy": policy})
                log.info("%s guardó la política ACL", session["username"])
                return self.redirect(f"{BASE}/acl?m=acl-saved")
            hs.api("POST", "/policy/check", {"policy": policy})
            result = ("ok", "La política es válida.")
        except urllib.error.HTTPError as exc:
            result = ("error", hs.api_error(exc))
        # Se devuelve el borrador tal cual para no perder lo escrito
        self.send(200, views_admin.acl_page(session, CTX, hs.get_policy(), "", draft=policy, result=result))

    # --- DNS ---
    def save_dns(self, session: dict, form: dict):
        ctx = dns_ctx()
        current = hs.dns_config()
        cfg: dict | None = None

        def again(error: str):
            # Se reenseña lo que el admin escribió, para que no lo pierda
            draft = dict(current, **cfg) if cfg else current
            return self.send(400, views.dns_page(session, ctx, draft, to_machines(visible_nodes(session)), error=error))

        if not ctx.get("dns_editable"):
            return again(ctx.get("dns_reason", "La edición de DNS no está disponible."))

        cfg = {
            "magic_dns": form.get("magic_dns") == "1",
            "override_local_dns": form.get("override_local_dns") == "1",
            "base_domain": str(form.get("base_domain", "")).strip().lower().rstrip("."),
            "nameservers": lines(str(form.get("nameservers", ""))),
            "search_domains": [d.lower() for d in lines(str(form.get("search_domains", "")))],
            "split": {},
        }
        for line in str(form.get("split", "")).splitlines():
            if not line.strip():
                continue
            domain, _, servers = line.partition(":")
            if not servers.strip() and domain.strip():
                return again(f"Split DNS: falta el servidor en «{line.strip()}» (formato: dominio: servidor, servidor).")
            cfg["split"][domain.strip().lower()] = lines(servers)

        if not hs.valid_domain(cfg["base_domain"]):
            return again("El nombre de la red no es un dominio válido.")
        if cfg["base_domain"] == SERVER_HOST or SERVER_HOST.endswith("." + cfg["base_domain"]):
            return again("El nombre de la red tiene que ser distinto del dominio del servidor.")
        bad = [ns for ns in cfg["nameservers"] + [s for v in cfg["split"].values() for s in v]
               if not hs.valid_nameserver(ns)]
        if bad:
            return again(f"Servidor de nombres no válido: {bad[0]}")
        bad = [d for d in cfg["search_domains"] + list(cfg["split"]) if not hs.valid_domain(d)]
        if bad:
            return again(f"Dominio no válido: {bad[0]}")
        if cfg["override_local_dns"] and not cfg["nameservers"]:
            return again("Para usar estos servidores de nombres en los dispositivos hace falta al menos uno.")

        ok, error = hs.apply_dns(cfg)
        if not ok:
            log.warning("%s intentó cambiar el DNS: %s", session["username"], error)
            return again(error)
        log.info("%s cambió el DNS y reinició Headscale", session["username"])
        return self.redirect(f"{BASE}/dns?m=dns-saved")


def main():
    port = int(os.environ.get("PORT", "8000"))
    log.info("Mi VPN escuchando en :%d (pública: %s%s, SSO=%s, login con API key=%s)",
             port, PUBLIC_URL, BASE, SSO, API_KEY_LOGIN)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
