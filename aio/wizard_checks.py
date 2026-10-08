"""Validation of the setup wizard's form fields: each check returns the cleaned value or raises
SetupError with a readable, already translated reason."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import cron
import render
import signup
from i18n import _

TLS_MODES = ("auto", "internal", "off")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
TAILNET_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")


class SetupError(Exception):
    """A readable reason a step failed (already translated)."""


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

def valid_text(value: str) -> bool:
    return render.validate_env_text(value)


def check_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or port == -1
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username
            or not valid_text(url) or len(url) > 253 or " " in url):
        raise SetupError(_("Enter the public URL as http(s)://host[:port], without a path."))
    return url


def check_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254 or not valid_text(email):
        raise SetupError(_("Enter a valid email address."))
    return email


def check_server(url: str, tls: str, acme_email: str) -> dict:
    url = check_url(url)
    scheme = urlparse(url).scheme
    if tls not in TLS_MODES:
        raise SetupError(_("Choose one of the options."))
    if tls != "off" and scheme != "https":
        raise SetupError(_("Automatic and internal certificates need an https:// URL."))
    out = {"public_url": url, "tls": tls, "acme_email": ""}
    if tls == "auto":
        host = urlparse(url).hostname or ""
        if re.fullmatch(r"[0-9.]+", host) or host == "localhost" or ":" in host:
            raise SetupError(_("Let's Encrypt does not issue certificates for IP addresses or localhost."))
        out["acme_email"] = check_email(acme_email)
    return out


def check_signup(mode: str, first_key: bool = False) -> dict:
    if mode not in signup.MODES:
        raise SetupError(_("Choose who can register."))
    return {"signup_mode": mode, "first_key": first_key and mode == "invite"}


def check_derp(mode: str, url: str = "") -> dict:
    if mode not in render.DERP_MODES:
        raise SetupError(_("Choose how devices relay traffic."))
    if mode != "custom":
        return {"derp_mode": mode, "derp_url": ""}
    try:
        return {"derp_mode": mode, "derp_url": render.check_derp_url(url)}
    except ValueError:
        raise SetupError(_("The DERP map URL must start with http:// or https://."))


def check_network(tailnet: str, isolation: bool, base_domain: str = "", server_url: str = "") -> dict:
    tailnet = tailnet.strip().lower()
    if not TAILNET_RE.match(tailnet):
        raise SetupError(_("The tailnet name may only have lowercase letters, numbers and hyphens."))
    host = urlparse(server_url).hostname or ""
    wanted = base_domain.strip().strip(".").lower()
    if wanted and host and (host == wanted or host.endswith("." + wanted)):
        raise SetupError(_("The base domain must differ from the server's own domain."))
    try:
        base = render.check_base_domain(wanted)
    except ValueError:
        raise SetupError(_("The base domain must be a DNS name such as hse.net."))
    return {"tailnet_name": tailnet, "network_isolation": "true" if isolation else "false", "base_domain": base}


def check_backups(schedule: str, keep_days: str, enabled: bool = True) -> dict:
    """Schedule (five cron fields, or ``off``) and retention; ``enabled=False`` stores ``off``."""
    schedule = " ".join(schedule.split())
    if not keep_days.strip().isdigit() or not 1 <= int(keep_days) <= 3650:
        raise SetupError(_("Days to keep backups must be a number between 1 and 3650."))
    if not enabled or cron.is_off(schedule):
        schedule = "off"
    else:
        try:
            cron.parse(schedule)
        except ValueError:
            raise SetupError(_("Enter the schedule as five cron fields, for example 0 3 * * *.")) from None
    return {"backup_schedule": schedule, "backup_keep_days": str(int(keep_days))}
