"""Console settings read from the environment.

``Settings.from_env`` is a pure function of the mapping it receives, so the parsing rules can be tested
without touching ``os.environ``. ``app`` builds one instance at start-up.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass

MFA_MODES = ("admins", "everyone", "optional")


def csv_set(environ: Mapping[str, str], name: str, default: str = "") -> set[str]:
    return {x.strip() for x in environ.get(name, default).split(",") if x.strip()}


def oidc_scope(raw: str | None) -> str:
    """'openid' first and always present, then what was asked for (default: profile email), no repeats."""
    return " ".join(dict.fromkeys(["openid"] + (raw or "profile email").split()))


@dataclass(frozen=True)
class Settings:
    public_url: str
    session_secret: bytes
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_scope: str
    api_key_login: bool
    admin_groups: frozenset[str]
    admin_emails: frozenset[str]
    network_admin_groups: frozenset[str]
    auditor_groups: frozenset[str]
    tailnet_name: str
    mfa_required: str

    @property
    def sso(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def server_host(self) -> str:
        return urllib.parse.urlparse(self.public_url).hostname or ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Settings:
        """Raises ``KeyError`` when PUBLIC_URL or SESSION_SECRET is missing, as the console always has."""
        issuer = environ.get("OIDC_ISSUER", "")
        client_id = environ.get("OIDC_CLIENT_ID", "")
        mfa = environ.get("MFA_REQUIRED", "admins")
        return cls(
            public_url=environ["PUBLIC_URL"].rstrip("/"),
            session_secret=environ["SESSION_SECRET"].encode(),
            oidc_issuer=issuer,
            oidc_client_id=client_id,
            oidc_client_secret=environ.get("OIDC_CLIENT_SECRET", ""),
            oidc_scope=oidc_scope(environ.get("OIDC_SCOPE")),
            api_key_login=environ.get("PORTAL_API_KEY_LOGIN", "false").lower() == "true"
            or not (issuer and client_id),
            admin_groups=frozenset(csv_set(environ, "PORTAL_ADMIN_GROUPS", "vpn-admins")),
            admin_emails=frozenset(e.lower() for e in csv_set(environ, "PORTAL_ADMIN_EMAILS")),
            network_admin_groups=frozenset(csv_set(environ, "PORTAL_NETWORK_ADMIN_GROUPS")),
            auditor_groups=frozenset(csv_set(environ, "PORTAL_AUDITOR_GROUPS")),
            tailnet_name=environ.get("TAILNET_NAME", ""),
            mfa_required=mfa if mfa in MFA_MODES else "admins",
        )
