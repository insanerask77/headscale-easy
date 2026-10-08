"""Config renderer for the all-in-one image.

Renders Headscale's ``config.yaml`` and the ``Caddyfile`` (``dns_block``,
``key_expiry_block``, ``database_block``, ``derp_paths_block``,
``render_headscale_config`` and ``render_caddyfile``) from ``templates/*.tmpl``:
upstreams on 127.0.0.1 and everything under /data. The golden files in
tests/fixtures/render are frozen outputs that pin the shape of the config.

Settings precedence: environment > /data/config/settings.json > defaults.
Standard library only.
"""
import ipaddress
import json
import os
import re
import tempfile
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.environ.get("HSE_TEMPLATES_DIR") or os.path.join(ROOT, "templates")
DATA_DIR = os.environ.get("HSE_DATA_DIR", "/data")

DNS_BEGIN = "# >>> dns: managed by Headscale Easy (do not edit between these markers)"
DNS_END = "# <<< dns"
EXPIRY_BEGIN = "  # >>> key expiry: managed by Headscale Easy (do not edit between these markers)"
EXPIRY_END = "  # <<< key expiry"
DERP_BEGIN = "  # >>> derp map: managed by Headscale Easy (do not edit between these markers)"
DERP_END = "  # <<< derp map"

# --- Settings ------------------------------------------------------------------------
# key -> (environment variable, default). Defaults of None mean "unset".
SETTINGS = {
    "public_url": ("HSE_PUBLIC_URL", None),
    "tls": ("HSE_TLS", None),  # auto | internal | off; default follows the URL scheme
    "acme_email": ("ACME_EMAIL", ""),
    "derp_port": ("HSE_DERP_PORT", "3478"),
    "admin_email": ("HSE_ADMIN_EMAIL", ""),
    "oidc_issuer": ("OIDC_ISSUER", ""),
    "oidc_client_id": ("OIDC_CLIENT_ID", ""),
    "oidc_client_secret": ("OIDC_CLIENT_SECRET", ""),
    "oidc_scope": ("OIDC_SCOPE", "openid profile email"),
    "db_type": ("HEADSCALE_DB_TYPE", "sqlite"),
    "pg_host": ("HEADSCALE_PG_HOST", ""),
    "pg_port": ("HEADSCALE_PG_PORT", "5432"),
    "pg_name": ("HEADSCALE_PG_NAME", "headscale"),
    "pg_user": ("HEADSCALE_PG_USER", "headscale"),
    "pg_pass": ("HEADSCALE_PG_PASS", ""),
    "pg_sslmode": ("HEADSCALE_PG_SSLMODE", "disable"),
    "ui_lang": ("UI_LANG", "en"),
    "tz": ("TZ", "UTC"),
    "tailnet_name": ("TAILNET_NAME", "myorg"),
    "base_domain": ("HSE_BASE_DOMAIN", ""),  # MagicDNS domain; empty = DEFAULT_BASE_DOMAIN
    "node_key_expiry": ("NODE_KEY_EXPIRY", "180d"),
    "signup_mode": ("HSE_SIGNUP", "off"),  # off | invite | open: self-registration on the sign-in page
    "derp_mode": ("HSE_DERP_MODE", None),  # embedded (default) | public | custom
    "derp_url": ("HSE_DERP_URL", ""),  # custom: a DERP map served over http(s)
    "network_isolation": ("NETWORK_ISOLATION", "true"),
    "http_port": ("HEADSCALE_HTTP_PORT", "8080"),
    "metrics_port": ("HEADSCALE_METRICS_PORT", "9090"),
    "grpc_port": ("HEADSCALE_GRPC_PORT", "50443"),
    "ip_prefixes_v4": ("IP_PREFIXES_V4", "100.64.0.0/10"),
    "ip_prefixes_v6": ("IP_PREFIXES_V6", "fd7a:115c:a1e0::/48"),
    "log_level": ("LOG_LEVEL", "info"),
    # Console knobs
    "session_secret": ("SESSION_SECRET", ""),
    "mfa_required": ("MFA_REQUIRED", "admins"),
    "smtp_host": ("SMTP_HOST", ""),
    "smtp_port": ("SMTP_PORT", "587"),
    "smtp_username": ("SMTP_USERNAME", ""),
    "smtp_password": ("SMTP_PASSWORD", ""),
    "smtp_use_tls": ("SMTP_USE_TLS", "false"),
    "smtp_use_ssl": ("SMTP_USE_SSL", "false"),
    "smtp_from": ("SMTP_FROM", ""),
    "notify_urls": ("NOTIFY_URLS", ""),
    "notify_events": ("NOTIFY_EVENTS", ""),
    "portal_admin_emails": ("PORTAL_ADMIN_EMAILS", ""),
    # Console tuning. Unset (None) = the console's own default; set = passed to it
    "expiry_warning_days": ("EXPIRY_WARNING_DAYS", None),  # a key "expires soon" within N days (14)
    "inactive_days": ("INACTIVE_DAYS", None),  # a machine is "inactive" after N days offline (30)
    "auto_rename_localhost": ("AUTO_RENAME_LOCALHOST", None),  # rename "localhost" machines (true)
    "rename_interval": ("RENAME_INTERVAL", None),  # seconds between rename passes (5)
    "audit_retention_days": ("AUDIT_RETENTION_DAYS", None),  # activity log retention, 0 = forever (90)
    "status_update_check": ("STATUS_UPDATE_CHECK", None),  # look for a newer release (true)
    "signin_rate_limit": ("SIGNIN_RATE_LIMIT", None),  # failed sign-ins allowed per window (10)
    "signin_rate_window": ("SIGNIN_RATE_WINDOW", None),  # window in seconds (600)
    "portal_api_key_login": ("PORTAL_API_KEY_LOGIN", None),  # emergency sign-in with the Headscale API key
    "backup_upload_max_mb": ("BACKUP_UPLOAD_MAX_MB", None),  # largest backup the console accepts (1024)
    # Advanced configurations (advanced/): a proxy in front, an external Authentik as OIDC provider, a read-only PostgreSQL role
    "trusted_proxies": ("HSE_TRUSTED_PROXIES", ""),  # CIDRs of the proxy in front: real client IPs
    "trusted_proxies_any": ("HSE_TRUSTED_PROXIES_ANY", ""),  # 1: allow a /0 entry (trust every sender)
    "authentik_upstream": ("HSE_AUTHENTIK_UPSTREAM", ""),  # host:port of an Authentik served under /authentik
    # Who may sign in through the OIDC provider (Headscale oidc.allowed_*) and who is what in the console
    "oidc_allowed_domains": ("HSE_OIDC_ALLOWED_DOMAINS", ""),
    "oidc_allowed_users": ("HSE_OIDC_ALLOWED_USERS", ""),
    "oidc_allowed_groups": ("HSE_OIDC_ALLOWED_GROUPS", ""),
    "portal_admin_groups": ("PORTAL_ADMIN_GROUPS", ""),
    "portal_network_admin_groups": ("PORTAL_NETWORK_ADMIN_GROUPS", ""),
    "portal_auditor_groups": ("PORTAL_AUDITOR_GROUPS", ""),
    "pg_ro_user": ("HEADSCALE_PG_RO_USER", ""),  # the console reads PostgreSQL with this role, not the owner
    "pg_ro_pass": ("HEADSCALE_PG_RO_PASS", ""),
    "backup_schedule": ("BACKUP_SCHEDULE", "0 3 * * *"),
    "backup_keep_days": ("BACKUP_KEEP_DAYS", "14"),
}
CONSOLE_TUNING = ("expiry_warning_days", "inactive_days", "auto_rename_localhost", "rename_interval",
                  "audit_retention_days", "status_update_check", "signin_rate_limit", "signin_rate_window",
                  "portal_api_key_login", "backup_upload_max_mb")
# Who provides HTTPS (letsencrypt | selfsigned | front | none) and the identity provider
# (none | authentik | external) are derived from the settings, never stored.
TLS_TO_SSL_MODE = {"auto": "letsencrypt", "internal": "selfsigned", "off": "none"}
# Quotes, $, backquotes, backslashes and newlines would break the generated files
_FORBIDDEN = set("\"'$`\\\r\n")


def validate_env_text(value):
    """No quotes, $, backquotes, backslashes or newlines."""
    return not (_FORBIDDEN & set(str(value)))


def settings_file(data_dir=None):
    return os.path.join(data_dir or DATA_DIR, "config", "settings.json")


def load_settings(env=None, path=None):
    """Settings dict: environment > settings.json > defaults.

    Raises ValueError for values with quotes, newlines...
    """
    env = os.environ if env is None else env
    stored = {}
    path = path or settings_file()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            stored = data
    except (OSError, ValueError):
        pass

    out = {}
    for key, (env_name, default) in SETTINGS.items():
        value = None
        if env.get(env_name) not in (None, ""):
            value = env[env_name]
        elif stored.get(key) not in (None, ""):
            value = stored[key]
        elif default is not None:
            value = default
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        value = str(value)
        if not validate_env_text(value):
            raise ValueError("%s: quotes, $, backquotes, backslashes and newlines are not allowed" % env_name)
        out[key] = value
    # A bad schedule must stop a headless start instead of silently never backing up
    out["backup_schedule"] = check_backup_schedule(out.get("backup_schedule", ""))
    out["backup_keep_days"] = check_backup_keep_days(out.get("backup_keep_days", ""))
    return out


def check_backup_schedule(value):
    """A five-field cron expression, normalised; "off" disables scheduled backups."""
    try:  # aio/ on sys.path (the image, the wizard) or the repository root (tests)
        import cron
    except ImportError:
        from aio import cron
    text = " ".join((value or "").split())
    if cron.is_off(text):
        return "off"
    try:
        cron.parse(text)
    except ValueError as exc:
        raise ValueError("BACKUP_SCHEDULE: %s" % exc) from None
    return text


def check_backup_keep_days(value):
    text = (value or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= 3650:
        raise ValueError("BACKUP_KEEP_DAYS must be a number between 1 and 3650")
    return str(int(text))


def _domain(url):
    host = urlparse(url).hostname
    if not host:
        raise ValueError("public URL has no host: %r" % url)
    return host


_UPSTREAM = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?:[0-9]{1,5}$")
MAX_TRUSTED_PROXIES = 16


def check_authentik_upstream(value):
    """host:port of an Authentik that Caddy reaches (inside the compose network); "" if none."""
    text = (value or "").strip()
    if not text:
        return ""
    if not _UPSTREAM.match(text) or not 1 <= int(text.rsplit(":", 1)[1]) <= 65535:
        raise ValueError("HSE_AUTHENTIK_UPSTREAM must be host:port, for example authentik-server:9000")
    return text


def check_trusted_proxies(value, allow_any=False):
    """The proxies in front, as a list of normalised CIDRs ("" = none).

    Anything these senders put in X-Forwarded-For is believed, so the list is short and explicit. A /0 entry
    would trust the whole Internet: refused unless HSE_TRUSTED_PROXIES_ANY=1 says it is meant."""
    items = [x for x in re.split(r"[,\s]+", (value or "").strip()) if x]
    if len(items) > MAX_TRUSTED_PROXIES:
        raise ValueError("HSE_TRUSTED_PROXIES: at most %d entries" % MAX_TRUSTED_PROXIES)
    out = []
    for item in items:
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise ValueError("HSE_TRUSTED_PROXIES: %r is not an address or a CIDR" % item) from None
        if net.prefixlen == 0 and not allow_any:
            raise ValueError("HSE_TRUSTED_PROXIES: %s trusts every sender; set HSE_TRUSTED_PROXIES_ANY=1 if that is meant" % item)
        if str(net) not in out:
            out.append(str(net))
    return out


_ALLOWED = {
    "domains": (re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"), "domain names such as example.com"),
    "users": (re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"), "email addresses"),
    "groups": (re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,99}$"), "group names"),
}


def check_allowed(kind, value):
    """HSE_OIDC_ALLOWED_<KIND>: a comma-separated list for Headscale's oidc.allowed_<kind>; [] = no restriction.

    Without any of them everyone the provider signs in is accepted, which is only right for a provider that
    has its own closed user base (your Authentik, Keycloak). With Google, anyone with an account qualifies."""
    pattern, what = _ALLOWED[kind]
    items = []
    for item in (x.strip() for x in (value or "").split(",")):
        if not item:
            continue
        if not pattern.match(item):
            raise ValueError("HSE_OIDC_ALLOWED_%s: %r is not valid; use %s, comma-separated" % (kind.upper(), item, what))
        if item not in items:
            items.append(item)
    return items


DERP_MODES = ("embedded", "public", "custom")


def check_derp_url(value):
    """A DERP map URL (http/https, a host, no spaces); "" if none."""
    url = (value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or re.search(r"\s", url):
        raise ValueError("derp_url must be an http(s):// URL of a DERP map")
    return url


def derp_mode(settings):
    """embedded (default) | public | custom."""
    mode = (settings.get("derp_mode") or "").strip().lower()
    if not mode:
        mode = "embedded"
    if mode not in DERP_MODES:
        raise ValueError("HSE_DERP_MODE must be embedded, public or custom")
    return mode


DEFAULT_BASE_DOMAIN = "hse.net"  # slug of "Headscale Easy"
_DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def check_base_domain(value, server_host=""):
    """The MagicDNS base domain as a lowercase DNS name; "" means the default.

    Headscale refuses a base domain that is the server's own domain or a
    parent of it, so that is rejected here with a clear message."""
    name = (value or "").strip().strip(".").lower()
    if not name:
        return ""
    labels = name.split(".")
    if len(name) > 253 or len(labels) < 2 or not all(_DNS_LABEL.match(x) for x in labels):
        raise ValueError("base_domain must be a DNS name such as hse.net")
    host = (server_host or "").lower()
    if host and (host == name or host.endswith("." + name)):
        raise ValueError("base_domain must differ from the server's own domain (%s)" % host)
    return name


def to_vars(settings):
    """Template variables (SERVER_URL, DOMAIN, SSL_MODE...) from settings."""
    url = (settings.get("public_url") or "").rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("public_url must be http(s)://host[:port]: %r" % url)
    domain = parsed.hostname

    tls = settings.get("tls") or ("auto" if parsed.scheme == "https" else "off")
    if tls not in TLS_TO_SSL_MODE:
        raise ValueError("HSE_TLS must be auto, internal or off")
    if tls != "off" and parsed.scheme != "https":
        raise ValueError("HSE_TLS=%s needs an https:// public URL" % tls)
    if tls == "auto" and (re.fullmatch(r"[0-9.]+", domain) or domain == "localhost" or ":" in domain):
        raise ValueError("Let's Encrypt does not issue certificates for IPs or localhost")
    ssl_mode = TLS_TO_SSL_MODE[tls]
    if tls == "off" and parsed.scheme == "https":
        ssl_mode = "front"  # a proxy in front terminates TLS: the browser sees https, this hop is http

    issuer = settings.get("oidc_issuer") or ""
    if not issuer:
        auth = "none"
    elif "/authentik/" in issuer or settings.get("authentik_upstream"):
        auth = "authentik"  # an Authentik issuer: routed under /authentik/, emails not marked verified
    else:
        auth = "external"

    v = {
        "SERVER_URL": url,
        "DOMAIN": domain,
        "SSL_MODE": ssl_mode,
        "AUTH_PROVIDER": auth,
        "ENABLE_OIDC": "false" if auth == "none" else "true",
        "ACME_EMAIL": settings.get("acme_email", ""),
        "HEADSCALE_HTTP_PORT": settings.get("http_port", "8080"),
        "HEADSCALE_METRICS_PORT": settings.get("metrics_port", "9090"),
        "HEADSCALE_GRPC_PORT": settings.get("grpc_port", "50443"),
        "HEADSCALE_DERP_PORT": settings.get("derp_port", "3478"),
        "IP_PREFIXES_V4": settings.get("ip_prefixes_v4", "100.64.0.0/10"),
        "IP_PREFIXES_V6": settings.get("ip_prefixes_v6", "fd7a:115c:a1e0::/48"),
        "TAILNET_NAME": settings.get("tailnet_name", "myorg"),
        "BASE_DOMAIN": check_base_domain(settings.get("base_domain"), domain) or DEFAULT_BASE_DOMAIN,
        "LOG_LEVEL": settings.get("log_level", "info"),
        "NODE_KEY_EXPIRY": settings.get("node_key_expiry", "180d"),
        "HEADSCALE_DB_TYPE": settings.get("db_type", "sqlite"),
        "HEADSCALE_PG_HOST": settings.get("pg_host", ""),
        "HEADSCALE_PG_PORT": settings.get("pg_port", "5432"),
        "HEADSCALE_PG_NAME": settings.get("pg_name", "headscale"),
        "HEADSCALE_PG_USER": settings.get("pg_user", "headscale"),
        "HEADSCALE_PG_PASS": settings.get("pg_pass", ""),
        "HEADSCALE_PG_SSLMODE": settings.get("pg_sslmode", "disable"),
        "OIDC_ISSUER_URL": settings.get("oidc_issuer", ""),
        "OIDC_CLIENT_ID": settings.get("oidc_client_id", ""),
        "OIDC_CLIENT_SECRET": settings.get("oidc_client_secret", ""),
        "OIDC_SCOPE": settings.get("oidc_scope", "openid profile email"),
        "AUTHENTIK_UPSTREAM": "",
        "TRUSTED_PROXIES": [],
        "OIDC_ALLOWED": {},
    }
    v["AUTHENTIK_UPSTREAM"] = check_authentik_upstream(settings.get("authentik_upstream"))
    v["OIDC_ALLOWED"] = {k: check_allowed(k, settings.get("oidc_allowed_" + k)) for k in _ALLOWED}
    v["TRUSTED_PROXIES"] = check_trusted_proxies(settings.get("trusted_proxies"),
                                                 settings.get("trusted_proxies_any") in ("1", "true"))
    if bool(settings.get("pg_ro_user")) != bool(settings.get("pg_ro_pass")):
        raise ValueError("HEADSCALE_PG_RO_USER and HEADSCALE_PG_RO_PASS go together")
    ro = settings.get("pg_ro_user") or ""
    if ro and (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", ro) or ro == settings.get("pg_user", "headscale")):
        raise ValueError("HEADSCALE_PG_RO_USER must be a role name (letters, digits, _) other than the owner's")
    if v["HEADSCALE_DB_TYPE"] not in ("sqlite", "postgres"):
        raise ValueError("HEADSCALE_DB_TYPE must be sqlite or postgres")
    if v["HEADSCALE_DB_TYPE"] == "postgres" and not v["HEADSCALE_PG_HOST"]:
        raise ValueError("HEADSCALE_PG_HOST is required with HEADSCALE_DB_TYPE=postgres")
    if ssl_mode == "letsencrypt" and not v["ACME_EMAIL"]:
        raise ValueError("ACME_EMAIL is required with HSE_TLS=auto")
    if auth != "none" and not (v["OIDC_ISSUER_URL"] and v["OIDC_CLIENT_ID"]):
        raise ValueError("OIDC needs OIDC_ISSUER and OIDC_CLIENT_ID")
    if settings.get("signup_mode", "off") not in ("off", "invite", "open"):
        raise ValueError("HSE_SIGNUP must be off, invite or open")
    v["SIGNUP_MODE"] = settings.get("signup_mode", "off")
    v["DERP_MODE"] = derp_mode(settings)
    v["DERP_URL"] = check_derp_url(settings.get("derp_url"))
    v["DERP_USE_PUBLIC"] = "true" if v["DERP_MODE"] == "public" else "false"
    return v


# --- envsubst -----------------------------------------------------------------------
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def envsubst(text, variables):
    """Replace ${VAR} (unset = empty). Substituted values are not expanded again."""
    return _VAR.sub(lambda m: str(variables.get(m.group(1), "")), text)


# --- Marked blocks ---------------------------------------------------------------------
def _marked(existing, begin, end):
    """The marked block of an existing config, markers included.

    None when the config has no such block.
    """
    if not existing or begin not in existing:
        return None
    out, on = [], False
    for line in existing.split("\n"):
        if line == begin and not on:
            on = True
            out.append(line)
            continue
        if on:
            out.append(line)
            if line == end:
                break
    return "\n".join(out)


def _dns_setting(existing, key):
    """First ``  key: value`` inside the ``dns:`` section."""
    pattern = re.compile(r"  %s:[ \t\r\f\v]*(.*)" % key)
    in_range = False
    for line in existing.split("\n"):
        if not in_range:
            in_range = line.startswith("dns:")
            continue
        m = pattern.match(line)
        if m:
            return m.group(1)
        if re.match(r"[a-z]", line):
            in_range = False  # the range ends on this line
    return ""


def dns_block(v, existing=None):
    block = _marked(existing, DNS_BEGIN, DNS_END)
    if block is not None:
        return block.rstrip("\n")
    base = v.get("BASE_DOMAIN") or "%s.headscale.net" % v["TAILNET_NAME"]
    magic = "true"
    if existing:
        base = _dns_setting(existing, "base_domain") or base
        magic = _dns_setting(existing, "magic_dns") or magic
    return "\n".join([
        DNS_BEGIN,
        "dns:",
        "  magic_dns: %s" % magic,
        "  base_domain: %s" % base,
        "  override_local_dns: true",
        "  nameservers:",
        "    global:",
        "      - 1.1.1.1",
        "      - 1.0.0.1",
        "    split: {}",
        "  search_domains: []",
        "  extra_records: []",
        DNS_END,
    ])


def key_expiry_block(v, existing=None):
    block = _marked(existing, EXPIRY_BEGIN, EXPIRY_END)
    if block is not None:
        return block.rstrip("\n")
    return "%s\n  expiry: %s\n%s" % (EXPIRY_BEGIN, v.get("NODE_KEY_EXPIRY") or "180d", EXPIRY_END)


def derp_paths_block(v, existing=None):
    block = _marked(existing, DERP_BEGIN, DERP_END)
    if block is not None:
        return block.rstrip("\n")
    return "%s\n  paths: []\n%s" % (DERP_BEGIN, DERP_END)


def database_block(v):
    if v.get("HEADSCALE_DB_TYPE", "sqlite") != "postgres":
        return ("database:\n  type: sqlite\n  sqlite:\n    path: /var/lib/headscale/db.sqlite\n"
                "    write_ahead_log: true")
    mode = v.get("HEADSCALE_PG_SSLMODE") or "disable"
    ssl = {"disable": "false", "prefer": "true"}.get(mode, '"%s"' % mode)
    return "\n".join([
        "database:",
        "  type: postgres",
        "  postgres:",
        '    host: "%s"' % v["HEADSCALE_PG_HOST"],
        "    port: %s" % (v.get("HEADSCALE_PG_PORT") or "5432"),
        '    name: "%s"' % (v.get("HEADSCALE_PG_NAME") or "headscale"),
        '    user: "%s"' % (v.get("HEADSCALE_PG_USER") or "headscale"),
        '    pass: "%s"' % v.get("HEADSCALE_PG_PASS", ""),
        "    max_open_conns: 10",
        "    max_idle_conns: 10",
        "    conn_max_idle_time_secs: 3600",
        "    ssl: %s" % ssl,
    ])


def _template(name):
    with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as fh:
        return fh.read()


# --- Paths --------------------------------------------------------
def paths(data_dir=None):
    d = data_dir or DATA_DIR
    return {
        "config": os.path.join(d, "config", "config.yaml"),
        "caddyfile": os.path.join(d, "config", "Caddyfile"),
        "derp": os.path.join(d, "config", "derp.yaml"),
        "settings": settings_file(d),
        "api_key": os.path.join(d, "console", "api-key"),
        "hs_dir": os.path.join(d, "headscale"),
        "hs_socket": os.path.join(d, "headscale", "headscale.sock"),
        "caddy_logs": os.path.join(d, "caddy", "logs"),
    }


def render_headscale_config(v, existing=None, data_dir=None):
    """headscale config.yaml. ``existing``: the current file, whose marked blocks are kept."""
    v = dict(v)
    if v["AUTH_PROVIDER"] != "none":
        scope_list = "".join("    - %s\n" % s for s in v.get("OIDC_SCOPE", "").split())
        # Authentik does not mark emails as verified; nobody verifies them there
        email_verified = "false" if v["AUTH_PROVIDER"] == "authentik" else "true"
        oidc = "\n".join([
            "oidc:",
            "  # Headscale does not start until it can read the issuer's configuration",
            "  only_start_if_oidc_is_available: true",
            '  issuer: "%s"' % v["OIDC_ISSUER_URL"],
            '  client_id: "%s"' % v["OIDC_CLIENT_ID"],
            '  client_secret: "%s"' % v["OIDC_CLIENT_SECRET"],
            "  scope:",
            scope_list.rstrip("\n"),
            "  email_verified_required: %s" % email_verified,
        ] + [
            line for kind, items in (v.get("OIDC_ALLOWED") or {}).items() if items
            for line in ["  allowed_%s:" % kind] + ['    - "%s"' % item for item in items]
        ] + [
            "  pkce:",
            "    enabled: true",
            "    method: S256",
        ])
    else:
        oidc = "# OIDC disabled"

    # Headscale only listens on loopback behind Caddy, which runs in the same container
    trusted = "trusted_proxies:\n  - 127.0.0.1/32"
    for cidr in v.get("TRUSTED_PROXIES") or []:
        trusted += "\n  - %s" % cidr
    if v.get("DERP_USE_PUBLIC", "true") == "true":
        derp_urls = "urls:\n    - https://controlplane.tailscale.com/derpmap/default"
        derp_auto = "true"
    elif v.get("DERP_URL"):
        derp_urls = "urls:\n    - %s" % v["DERP_URL"]
        derp_auto = "true"
    else:
        derp_urls = "urls: []"
        derp_auto = "false"

    variables = dict(v)
    variables.update(
        OIDC_CONFIG=oidc,
        TRUSTED_PROXIES_CONFIG=trusted,
        DNS_CONFIG=dns_block(v, existing),
        KEY_EXPIRY_CONFIG=key_expiry_block(v, existing),
        DERP_PATHS_CONFIG=derp_paths_block(v, existing),
        DERP_URLS_CONFIG=derp_urls,
        DERP_AUTO_UPDATE=derp_auto,
        DATABASE_CONFIG=database_block(v),
    )
    tmpl = _template("headscale-config.yaml.tmpl")
    for key in ("HEADSCALE_HTTP_PORT", "HEADSCALE_METRICS_PORT", "HEADSCALE_GRPC_PORT"):
        tmpl = tmpl.replace("0.0.0.0:${%s}" % key, "127.0.0.1:${%s}" % key)
    out = envsubst(tmpl, variables)
    p = paths(data_dir)
    out = out.replace("/var/lib/headscale/", p["hs_dir"] + "/")
    out = out.replace("/var/run/headscale/headscale.sock", p["hs_socket"])
    return out


def render_caddyfile(v, data_dir=None):
    mode = v["SSL_MODE"]
    domain = v["DOMAIN"]
    if mode == "letsencrypt":
        site, tls = domain, "tls %s" % v["ACME_EMAIL"]
        hsts = 'Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"'
    elif mode == "selfsigned":
        site, tls = domain, "tls internal"
        hsts = "# HSTS disabled (self-signed certificate)"
    else:
        # ':80' rather than 'http://DOMAIN': without a certificate the site is
        # reached by several names (localhost, LAN IP, a front proxy's name)
        site, tls = ":80", "# No TLS here: Caddy only routes over HTTP"
        hsts = "# HSTS: sent by the front proxy" if mode == "front" else "# HSTS disabled (no TLS)"

    redirect = ""
    if mode in ("letsencrypt", "selfsigned"):
        redirect = "http://%s {\n    redir https://{host}{uri} permanent\n}" % domain

    upstream = v.get("AUTHENTIK_UPSTREAM") or ""
    if v["AUTH_PROVIDER"] == "authentik" and upstream:
        # Authentik builds its issuer and redirects from the request scheme.
        # With a front proxy Caddy receives HTTP, so force what the browser sees.
        proto = "# X-Forwarded-Proto: from the incoming request"
        if mode == "front":
            proto = "header_up X-Forwarded-Proto https"
        authentik = "\n".join([
            "    # Authentik (identity provider) under /authentik/ (AUTHENTIK_WEB__PATH):",
            "    # the prefix is not stripped. /authentik alone redirects so it does not",
            "    # fall into Headscale's catch-all.",
            "    redir /authentik /authentik/ 308",
            "    handle /authentik/* {",
            "        reverse_proxy %s {" % upstream,
            "            header_up X-Real-IP {remote_host}",
            "            %s" % proto,
            "        }",
            "    }",
        ])
    else:
        authentik = "    # Authentik is not part of the all-in-one image (HSE_AUTHENTIK_UPSTREAM routes /authentik to one)"

    if v["AUTH_PROVIDER"] == "none":
        # Without OIDC, 'tailscale up' prints <url>/register/<auth id>: the web
        # UI signs the person in and approves the device for them.
        register = "\n".join([
            "    # Device sign-in without OIDC: approve it in the web UI",
            "    @register path_regexp register ^/register/([A-Za-z0-9_:-]+)$",
            "    redir @register /console/register/{re.register.1} 302",
        ])
    else:
        register = "    # /register/<id>: Headscale sends the device to the OIDC provider"

    variables = dict(v)
    variables.update(CADDY_DOMAIN=site, CADDY_TLS=tls, CADDY_HSTS=hsts, HTTP_REDIRECT=redirect,
                     AUTHENTIK_ROUTE=authentik, REGISTER_ROUTE=register)
    tmpl = _template("Caddyfile.tmpl")
    tmpl = tmpl.replace("headscale-easy:8000", "127.0.0.1:8000")
    tmpl = tmpl.replace("headscale:${HEADSCALE_HTTP_PORT}", "127.0.0.1:${HEADSCALE_HTTP_PORT}")
    tmpl = tmpl.replace("/var/log/caddy/access.log", os.path.join(paths(data_dir)["caddy_logs"], "access.log"))
    trusted = v.get("TRUSTED_PROXIES") or []
    if trusted:
        # {client_ip} is the first address (from the right) in X-Forwarded-For that a trusted proxy did not add; {remote_host}
        # would be the proxy itself, and every login would be counted against one address.
        tmpl = tmpl.replace("header_up X-Real-IP {remote_host}", "header_up X-Real-IP {client_ip}")
    if mode == "front":
        # Caddy receives http from the proxy; Headscale and Authentik must see what the browser sees
        tmpl = tmpl.replace("header_up X-Real-IP", "header_up X-Forwarded-Proto https\n            header_up X-Real-IP")
    out = envsubst(tmpl, variables)
    if trusted:
        # strict: read X-Forwarded-For from the right and skip only the trusted proxies. Without it Caddy
        # believes the leftmost entry, which a client can write itself when the proxy appends to the header.
        out = ("{\n    servers {\n        trusted_proxies static %s\n        trusted_proxies_strict\n    }\n}\n\n"
               % " ".join(trusted)) + out
    return out


def render_setup_caddyfile(data_dir=None):
    """Setup mode: plain HTTP on :80, only the wizard (127.0.0.1:8000) behind it."""
    return "\n".join([
        "# Caddyfile for Headscale Easy: setup mode (the first-run wizard only)",
        "",
        ":80 {",
        "    handle /healthz {",
        '        respond "OK" 200',
        "    }",
        "",
        "    handle {",
        "        reverse_proxy 127.0.0.1:8000 {",
        "            header_up X-Real-IP {remote_host}",
        "        }",
        "    }",
        "",
        "    log {",
        "        output file %s" % os.path.join(paths(data_dir)["caddy_logs"], "access.log"),
        "        format json",
        "    }",
        "}",
        "",
    ])


def console_env(settings, data_dir=None):
    """Environment for the console process (web/app.py) in the aio image."""
    p = paths(data_dir)
    d = data_dir or DATA_DIR
    v = to_vars(settings)
    pg = v["HEADSCALE_DB_TYPE"] == "postgres"
    env = {
        "PUBLIC_URL": v["SERVER_URL"],
        "TAILNET_NAME": v["TAILNET_NAME"],
        "HSE_DERP_MODE": v["DERP_MODE"],
        "HSE_SIGNUP": v["SIGNUP_MODE"],
        "SIGNUP_MODE_FILE": os.path.join(d, "console", "signup-mode"),
        "DEFAULT_LANG": settings.get("ui_lang", "en"),
        "TZ": settings.get("tz", "UTC"),
        "SESSION_SECRET": settings.get("session_secret", ""),
        "SESSIONS_DB": os.path.join(d, "console", "sessions.db"),
        "AUDIT_DB": os.path.join(d, "console", "audit.db"),
        "ACCOUNTS_DB": os.path.join(d, "console", "accounts.db"),
        "API_KEY_FILE": p["api_key"],
        "MFA_REQUIRED": settings.get("mfa_required", "admins"),
        "HEADSCALE_URL": "http://127.0.0.1:%s" % v["HEADSCALE_HTTP_PORT"],
        "HEADSCALE_METRICS_URL": "http://127.0.0.1:%s/metrics" % v["HEADSCALE_METRICS_PORT"],
        "HEADSCALE_DB": os.path.join(d, "headscale", "db.sqlite"),
        "HEADSCALE_DB_TYPE": v["HEADSCALE_DB_TYPE"],
        "HEADSCALE_CONFIG": p["config"],
        "HEADSCALE_DERP_FILE": p["derp"],
        "HEADSCALE_DERP_FILE_IN_CONFIG": p["derp"],
        "CONTROL_SOCKET": "/run/hse/control.sock",
        "BACKUP_DIR": os.path.join(d, "backups"),
        "OIDC_ISSUER": v["OIDC_ISSUER_URL"],
        "OIDC_CLIENT_ID": v["OIDC_CLIENT_ID"],
        "OIDC_CLIENT_SECRET": v["OIDC_CLIENT_SECRET"],
        "HEADSCALE_OIDC_ISSUER": v["OIDC_ISSUER_URL"],
        "PORTAL_ADMIN_EMAILS": settings.get("portal_admin_emails") or settings.get("admin_email", ""),
        "SMTP_HOST": settings.get("smtp_host", ""),
        "SMTP_PORT": settings.get("smtp_port", "587"),
        "SMTP_USERNAME": settings.get("smtp_username", ""),
        "SMTP_PASSWORD": settings.get("smtp_password", ""),
        "SMTP_USE_TLS": settings.get("smtp_use_tls", "false"),
        "SMTP_USE_SSL": settings.get("smtp_use_ssl", "false"),
        "SMTP_FROM": settings.get("smtp_from", ""),
        "NOTIFY_URLS": settings.get("notify_urls", ""),
        "NOTIFY_EVENTS": settings.get("notify_events", ""),
        "NETWORK_ISOLATION": settings.get("network_isolation", "true"),
        "HSE_ADMIN_EMAIL": settings.get("admin_email", ""),
    }
    if pg:
        # The console only reads Hostinfo: with a read-only role it never holds the owner's credentials
        ro = bool(settings.get("pg_ro_user"))
        env.update(HEADSCALE_PG_HOST=v["HEADSCALE_PG_HOST"], HEADSCALE_PG_PORT=v["HEADSCALE_PG_PORT"],
                   HEADSCALE_PG_NAME=v["HEADSCALE_PG_NAME"],
                   HEADSCALE_PG_USER=settings["pg_ro_user"] if ro else v["HEADSCALE_PG_USER"],
                   HEADSCALE_PG_PASSWORD=settings["pg_ro_pass"] if ro else v["HEADSCALE_PG_PASS"],
                   HEADSCALE_PG_SSLMODE=v["HEADSCALE_PG_SSLMODE"])
    for key in CONSOLE_TUNING:
        if settings.get(key):  # unset: the console's own default
            env[SETTINGS[key][0]] = settings[key]
    for key, name in (("portal_admin_groups", "PORTAL_ADMIN_GROUPS"),
                      ("portal_network_admin_groups", "PORTAL_NETWORK_ADMIN_GROUPS"),
                      ("portal_auditor_groups", "PORTAL_AUDITOR_GROUPS")):
        if settings.get(key):  # empty: the console's own default
            env[name] = settings[key]
    if v["AUTH_PROVIDER"] != "none":
        env["OIDC_SCOPE"] = v["OIDC_SCOPE"]
    return {k: val for k, val in env.items() if val != ""}


def config_warnings(settings):
    """Things that work but are probably not what an operator wants; the supervisor logs them at start."""
    out = []
    if (settings.get("db_type") == "postgres") and not settings.get("pg_ro_user"):
        out.append("PostgreSQL: the console connects as Headscale's owner role. Set HEADSCALE_PG_RO_USER and "
                   "HEADSCALE_PG_RO_PASS and the image creates a read-only role for it (advanced/postgres.yaml).")
    if (settings.get("tls") or "") == "off" and (settings.get("public_url") or "").startswith("https://"):
        out.append("A proxy in front: UDP 3478 (DERP/STUN) is not HTTP and does not go through it; publish it "
                   "straight to this container.")
        if not settings.get("trusted_proxies"):
            out.append("A proxy in front without HSE_TRUSTED_PROXIES: every sign-in is counted against the proxy's "
                       "address. Set it to the proxy's own address (/32), never a whole network.")
    if settings.get("trusted_proxies") and (settings.get("tls") or "") in ("auto", "internal"):
        out.append("HSE_TRUSTED_PROXIES is set while this image terminates TLS itself: only list a load balancer "
                   "that really sits in front.")
    return out


# --- Writing -------------------------------------------------------------------------
def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def write_file(path, content, mode=0o600):
    """Atomic write (temp file + rename) with the given mode, creating 700 parents."""
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".render-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render_all(settings, data_dir=None):
    """Write config.yaml, Caddyfile and (if missing) derp.yaml under <data>/config."""
    p = paths(data_dir)
    v = to_vars(settings)
    write_file(p["config"], render_headscale_config(v, _read(p["config"]), data_dir))
    write_file(p["caddyfile"], render_caddyfile(v, data_dir))
    if _read(p["derp"]) is None:
        write_file(p["derp"], "regions: {}\n")
    return p


def render_setup(data_dir=None):
    """Write the setup-mode Caddyfile."""
    p = paths(data_dir)
    write_file(p["caddyfile"], render_setup_caddyfile(data_dir))
    return p
