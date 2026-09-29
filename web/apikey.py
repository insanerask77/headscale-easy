"""Keeps the web UI's Headscale API key alive.

The installer creates the key with an expiry (90 days by default) and puts it
in .env, which the web UI cannot write. Without renewal the web UI would stop
working when it expires. A background thread therefore:

  - picks, among the key from .env and the one saved in KEY_FILE, the valid
    one that expires last (so re-running the installer keeps working);
  - when it has less than RENEW_BEFORE_DAYS left, creates a new key for
    RENEW_FOR_DAYS, saves it in KEY_FILE, starts using it and expires the old
    one (only that one: keys created by admins are never touched).

KEY_FILE lives in ./data/web on the host, owned by the same user as the
project files, so the installer can read it back into .env.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import headscale as hs

log = logging.getLogger("headscale-easy")

KEY_FILE = os.environ.get("API_KEY_FILE", "/data/api-key")
RENEW_BEFORE_DAYS = int(os.environ.get("API_KEY_RENEW_BEFORE_DAYS", "15"))
RENEW_FOR_DAYS = int(os.environ.get("API_KEY_RENEW_FOR_DAYS", "90"))
CHECK_EVERY = 6 * 3600  # seconds

# Set when no usable key is left: the UI explains how to fix it
problem = ""


def _prefix(key: str) -> str:
    return hs.api_key_prefix(key)


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, dict):  # {"seconds": ..., "nanos": ...}
        return datetime.fromtimestamp(int(value.get("seconds", 0)), timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _saved_key() -> str:
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _save_key(key: str) -> None:
    tmp = KEY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, KEY_FILE)


def _status(key: str) -> tuple[datetime | None, dict | None]:
    """(expiry, list entry) of a key, or (None, None) if Headscale rejects it."""
    try:
        keys = hs.http_json("GET", f"{hs.HEADSCALE_URL}/api/v1/apikey",
                            headers={"Authorization": f"Bearer {key}"}).get("apiKeys", [])
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return None, None
        raise
    prefix = _prefix(key)
    for entry in keys:
        if prefix and prefix in (entry.get("prefix") or ""):
            expiry = _parse_time(entry.get("expiration"))
            if expiry and expiry <= datetime.now(timezone.utc):
                return None, None
            return expiry, entry
    return None, None


def check() -> None:
    """Choose the best key and renew it if it is about to expire."""
    global problem
    candidates = []
    for key in dict.fromkeys([_saved_key(), os.environ.get("HEADSCALE_API_KEY", "")]):
        if key:
            expiry, entry = _status(key)
            if entry is not None:
                candidates.append((expiry or datetime.max.replace(tzinfo=timezone.utc), key, entry))
    if not candidates:
        problem = "expired"
        log.error("no valid Headscale API key left: run ./install.sh on the server to create one")
        return
    expiry, key, entry = max(candidates, key=lambda c: c[0])
    if key != hs.HEADSCALE_API_KEY:
        log.info("using API key %s (expires %s)", _prefix(key), expiry.date())
        hs.HEADSCALE_API_KEY = key
    problem = ""

    if expiry - datetime.now(timezone.utc) > timedelta(days=RENEW_BEFORE_DAYS):
        return
    until = datetime.now(timezone.utc) + timedelta(days=RENEW_FOR_DAYS)
    new_key = hs.api("POST", "/apikey", {"expiration": until.strftime("%Y-%m-%dT%H:%M:%SZ")})["apiKey"]
    _save_key(new_key)
    hs.HEADSCALE_API_KEY = new_key
    try:
        hs.api("POST", "/apikey/expire", {"id": entry["id"]})
    except urllib.error.HTTPError as exc:
        log.warning("renewed the API key but could not expire the old one (%s): %s", _prefix(key), hs.api_error(exc))
    log.info("renewed the API key: %s expires %s, %s expired", _prefix(new_key), until.date(), _prefix(key))


def _loop() -> None:
    while True:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - keep the thread alive
            log.warning("API key check failed: %s", exc)
        time.sleep(CHECK_EVERY)


def start() -> None:
    # Use the saved (renewed) key right away if there is one; check() confirms
    # it is still the best choice a moment later.
    saved = _saved_key()
    if saved:
        hs.HEADSCALE_API_KEY = saved
    threading.Thread(target=_loop, name="api-key", daemon=True).start()
