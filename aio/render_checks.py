"""Validation of the values behind the settings (render.SETTINGS): each check returns the normalised
value or raises ValueError with a message that names the setting."""
import ipaddress
import re
from urllib.parse import urlparse


# Quotes, $, backquotes, backslashes and newlines would break the generated files
_FORBIDDEN = set("\"'$`\\\r\n")


def validate_env_text(value):
    """No quotes, $, backquotes, backslashes or newlines."""
    return not (_FORBIDDEN & set(str(value)))


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
