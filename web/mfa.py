"""Two-factor authentication mode of the built-in Authentik, changed live.

The mode lives in the first line of the Authentik expression policy
"Headscale Easy: two-factor required for this user" (mode = "admins"). The
blueprint creates that policy once, from MFA_REQUIRED in .env, with
'state: created', so re-applying it never reverts a change made here. This
module rewrites only that line through Authentik's API
(PATCH /api/v3/policies/expression/<pk>/); Authentik clears its policy cache
on save, so the next sign-in already uses the new mode.

The API token (AUTHENTIK_API_TOKEN, PORTAL_AUTHENTIK_TOKEN in .env) belongs to
the service account 'headscale-easy-web', which may only read and change that
policy. It never leaves the server.

The last mode saved is also written to MODE_FILE (./data/web/mfa-required on
the host), so ./install.sh offers it as the default instead of the older value
in .env.

From the command line (install.sh uses it):
    python mfa.py get
    python mfa.py set admins|everyone|optional
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse

import headscale as hs
from i18n import _

log = logging.getLogger("headscale-easy")

MODES = ("admins", "everyone", "optional")
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "http://authentik-server:9000/authentik").rstrip("/")
TOKEN = os.environ.get("AUTHENTIK_API_TOKEN", "")
POLICY_NAME = "Headscale Easy: two-factor required for this user"
MODE_FILE = os.environ.get("MFA_MODE_FILE", "/data/mfa-required")
# The installer's choice: shown when the live mode cannot be read
DEFAULT = os.environ.get("MFA_REQUIRED", "admins") if os.environ.get("MFA_REQUIRED") in MODES else "admins"

MODE_LINE = re.compile(r'^mode = "([a-z]*)"$', re.M)


class MfaError(Exception):
    """A readable reason why the mode could not be read or changed."""


def available() -> bool:
    """Is there a token to change the mode with?"""
    return bool(TOKEN)


def _api(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        return hs.http_json(method, f"{AUTHENTIK_URL}/api/v3{path}", headers=headers, body=data, timeout=8)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise MfaError(_("Authentik rejected the web UI's token. Run ./install.sh once to set it up again.")) from exc
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:200]
        except OSError:
            pass
        log.warning("Authentik API %s %s: HTTP %s %s", method, path, exc.code, detail)
        raise MfaError(_("Authentik answered with an error (HTTP {code}).", code=exc.code)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("Authentik API %s %s: %s", method, path, exc)
        raise MfaError(_("Could not reach Authentik. Try again in a few seconds.")) from exc


def _policy() -> dict:
    found = _api("GET", "/policies/expression/?" + urllib.parse.urlencode({"name": POLICY_NAME}))
    for policy in found.get("results") or []:
        if policy.get("name") == POLICY_NAME:
            return policy
    raise MfaError(_("The two-factor policy was not found in Authentik. Run ./install.sh once to create it."))


def _mode_of(expression: str) -> str:
    m = MODE_LINE.search(expression or "")
    if not m or m.group(1) not in MODES:
        raise MfaError(_("The two-factor policy in Authentik was edited by hand: change it there."))
    return m.group(1)


def current() -> str:
    """The mode Authentik is using now."""
    return _mode_of(_policy().get("expression", ""))


def set_mode(mode: str) -> bool:
    """Apply a mode. True if it changed, False if it already was that one."""
    if mode not in MODES:
        raise MfaError(_("Choose one of the options."))
    if not available():
        raise MfaError(_("The web UI has no access to Authentik."))
    policy = _policy()
    expression = policy.get("expression", "")
    changed = _mode_of(expression) != mode
    if changed:
        new = MODE_LINE.sub(f'mode = "{mode}"', expression, count=1)
        _api("PATCH", f"/policies/expression/{urllib.parse.quote(str(policy['pk']))}/", {"expression": new})
    _save(mode)
    return changed


def saved() -> str:
    """The last mode saved from here ("" if none)."""
    try:
        with open(MODE_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return ""
    return value if value in MODES else ""


def _save(mode: str) -> None:
    try:
        tmp = MODE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(mode + "\n")
        os.replace(tmp, MODE_FILE)
    except OSError as exc:  # only a hint for the installer: not fatal
        log.warning("could not save the two-factor mode in %s: %s", MODE_FILE, exc)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if len(argv) == 1 and argv[0] == "get":
        action = "get"
    elif len(argv) == 2 and argv[0] == "set":
        action = "set"
    else:
        print("usage: mfa.py get | set admins|everyone|optional", file=sys.stderr)
        return 2
    if not available():
        print("no Authentik API token (AUTHENTIK_API_TOKEN)", file=sys.stderr)
        return 1
    try:
        if action == "get":
            print(current())
        else:
            print("changed" if set_mode(argv[1]) else "unchanged")
    except MfaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
