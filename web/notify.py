"""Webhook notifications: Slack, Telegram, ntfy and a generic JSON webhook.

Configured with two environment variables (see .env.example):

  NOTIFY_URLS    destinations, separated by commas, spaces or new lines:
                   slack:https://hooks.slack.com/services/T000/B000/XXXX
                   telegram:<bot token>@<chat id>      e.g. telegram:123:ABC@-100123
                   ntfy:https://ntfy.sh/my-topic       (or ntfy:my-topic for ntfy.sh)
                   webhook:https://example.com/hook    (generic JSON; a bare
                                                        https:// URL works too)
  NOTIFY_EVENTS  comma-separated events to send (default: all of these):
                   device.registered   a new device joined
                   device.key_expired  a device's key expired
                   device.expiring     a device's key expires soon (EXPIRY_WARNING_DAYS)
                   device.removed      a device was deleted
                   backup.failed       a scheduled backup failed (all-in-one image)

Sending never blocks the caller and never raises: every message goes out in a
daemon thread with a timeout and a few retries; failures are only logged.
audit.record() calls event() for each event it stores, and a small loop
(start()) raises "device.expiring" once per device and expiry date.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import audit

log = logging.getLogger("headscale-easy")

ALL_EVENTS = ("device.registered", "device.key_expired", "device.expiring", "device.removed", "backup.failed")
TIMEOUT = 10  # seconds per attempt
ATTEMPTS = 3
RETRY_DELAY = 2  # seconds, multiplied by the attempt number
CHECK_INTERVAL = 900  # seconds between "expiring soon" checks
KINDS = ("slack", "telegram", "ntfy", "webhook")

_TELEGRAM_RE = re.compile(r"^(\d+:[A-Za-z0-9_-]+)@(-?\d+|@[A-Za-z0-9_]+)$")


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

def parse_destinations(text: str) -> list[dict]:
    """[{"kind", "url", "label"}] from NOTIFY_URLS. Invalid entries are skipped."""
    out = []
    for item in re.split(r"[,\s]+", text or ""):
        if not item:
            continue
        kind, _sep, rest = item.partition(":")
        kind = kind.lower()
        if kind not in KINDS:  # a bare URL
            kind, rest = ("slack" if urllib.parse.urlparse(item).hostname == "hooks.slack.com" else "webhook"), item
        dest = _destination(kind, rest)
        if dest:
            out.append(dest)
        else:
            log.warning("notifications: ignoring invalid destination %r", kind)
    return out


def _destination(kind: str, rest: str) -> dict | None:
    if kind == "telegram":
        m = _TELEGRAM_RE.fullmatch(rest)
        if not m:
            return None
        return {"kind": kind, "token": m.group(1), "chat": m.group(2),
                "url": f"https://api.telegram.org/bot{m.group(1)}/sendMessage", "label": "Telegram"}
    if kind == "ntfy" and re.fullmatch(r"[A-Za-z0-9_-]+", rest or ""):
        rest = f"https://ntfy.sh/{rest}"
    parsed = urllib.parse.urlparse(rest)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    label = {"slack": "Slack", "ntfy": "ntfy", "webhook": "Webhook"}[kind]
    return {"kind": kind, "url": rest, "label": f"{label} ({parsed.hostname})"}


def parse_events(text: str | None) -> set[str]:
    """Enabled events from NOTIFY_EVENTS; empty or missing means all."""
    chosen = {e.strip().lower() for e in re.split(r"[,\s]+", text or "") if e.strip()}
    chosen &= set(ALL_EVENTS)
    return chosen or set(ALL_EVENTS)


def destinations() -> list[dict]:
    return parse_destinations(os.environ.get("NOTIFY_URLS", ""))


def events() -> set[str]:
    return parse_events(os.environ.get("NOTIFY_EVENTS"))


def configured() -> bool:
    return bool(destinations())


# -----------------------------------------------------------------------------
# Messages and payloads
# -----------------------------------------------------------------------------

def message(action: str, target: str = "", details: dict | None = None) -> str:
    details = details or {}
    user = f" ({details['user']})" if details.get("user") else ""
    name = target or "?"
    if action == "device.registered":
        return f"New device: {name}{user} joined the tailnet."
    if action == "device.key_expired":
        return f"Key expired: {name}{user} cannot connect until it signs in again."
    if action == "device.expiring":
        when = f" on {details['expiry']}" if details.get("expiry") else " soon"
        return f"Key expiring: {name}{user} expires{when}."
    if action == "device.removed":
        return f"Device removed: {name}{user} is no longer in the tailnet."
    if action == "backup.failed":
        why = f": {details['error']}" if details.get("error") else "."
        return f"Backup failed{why}"
    if action == "test":
        return "Test notification from Headscale Easy: this destination works."
    return f"{action}: {name}"


def build_request(dest: dict, action: str, target: str = "", details: dict | None = None,
                  ts: str | None = None) -> tuple[str, dict, bytes]:
    """(url, headers, body) for one destination."""
    text = message(action, target, details)
    kind = dest["kind"]
    if kind == "slack":
        return dest["url"], {"Content-Type": "application/json"}, json.dumps({"text": text}).encode()
    if kind == "telegram":
        body = {"chat_id": dest["chat"], "text": text, "disable_web_page_preview": True}
        return dest["url"], {"Content-Type": "application/json"}, json.dumps(body).encode()
    if kind == "ntfy":
        headers = {"Title": "Headscale Easy", "Tags": "computer",
                   "Priority": "high" if action in ("device.key_expired", "device.expiring") else "default"}
        return dest["url"], headers, text.encode()
    body = {"source": "headscale-easy", "event": action, "target": target, "message": text,
            "details": details or {}, "timestamp": ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return dest["url"], {"Content-Type": "application/json"}, json.dumps(body, ensure_ascii=False).encode()


# -----------------------------------------------------------------------------
# Delivery
# -----------------------------------------------------------------------------

def _post(url: str, headers: dict, body: bytes) -> None:
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "headscale-easy", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - admin-configured URL
        resp.read(1024)


def deliver(dest: dict, action: str, target: str = "", details: dict | None = None) -> bool:
    """Send one message with retries. Returns whether it got through; never raises."""
    try:
        url, headers, body = build_request(dest, action, target, details)
    except Exception as exc:  # noqa: BLE001
        log.warning("notifications: could not build the %s message: %s", dest.get("label"), exc)
        return False
    for attempt in range(1, ATTEMPTS + 1):
        try:
            _post(url, headers, body)
            return True
        except Exception as exc:  # noqa: BLE001 - network, HTTP errors, anything
            # The URL holds secrets (tokens): log the destination label only
            log.warning("notifications: %s attempt %d/%d failed: %s", dest.get("label"), attempt, ATTEMPTS,
                        type(exc).__name__)
            if attempt < ATTEMPTS:
                time.sleep(RETRY_DELAY * attempt)
    return False


def event(action: str, target: str = "", details: dict | None = None) -> bool:
    """Notify about an event if it is enabled. Non blocking; returns whether
    something was scheduled. Safe to call from anywhere (audit.record())."""
    try:
        if action not in events():
            return False
        dests = destinations()
        if not dests:
            return False
        for dest in dests:
            threading.Thread(target=deliver, args=(dest, action, target, details), name="notify",
                             daemon=True).start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("notifications: %s", exc)
        return False


def send_test() -> list[tuple[str, bool]]:
    """Synchronous test message to every destination: [(label, ok)]."""
    return [(d["label"], deliver(d, "test")) for d in destinations()]


# -----------------------------------------------------------------------------
# "Expiring soon" (the other events come from audit.record())
# -----------------------------------------------------------------------------

def new_expiring(nodes: list[dict], notified: dict, now: datetime | None = None) -> tuple[list[dict], dict]:
    """Machines that entered the "expires soon" window and were not notified
    for this expiry date. notified is {node id: expiry}; returns (nodes, the
    updated dict, pruned of machines that are no longer in the window)."""
    import expiry  # imported here: it pulls the UI modules

    soon = {str(n.get("id")): n for n in nodes if expiry.expires_soon(n, now)}
    fresh = [n for nid, n in soon.items() if notified.get(nid) != n.get("expiry")]
    return fresh, {nid: n.get("expiry") for nid, n in soon.items()}


def check_expiring(nodes: list[dict], now: datetime | None = None) -> int:
    """One pass: notify machines about to expire. Remembers what was sent in
    the activity log database so a restart does not repeat it."""
    saved = audit.get_state("notify.expiring")
    fresh, notified = new_expiring(nodes, saved or {}, now)
    audit.set_state("notify.expiring", notified)
    for node in fresh:
        details = {"user": (node.get("user") or {}).get("name", ""), "expiry": str(node.get("expiry", ""))[:10]}
        name = node.get("givenName") or node.get("name") or str(node.get("id"))
        event("device.expiring", name, details)
    return len(fresh)


def new_backup_failure(backup: dict | None, seen: str | None) -> tuple[dict | None, str | None]:
    """The scheduled backup that just failed, if any, and the `at` of the last
    run seen. A failure is reported once; manual runs are not (the person who
    clicked already sees the result). `seen` None = first look: remember, say nothing."""
    last = (backup or {}).get("last") or {}
    at = last.get("at")
    if not at:
        return None, seen
    if seen is None or at == seen:
        return None, at
    failed = not last.get("ok") and last.get("trigger") == "scheduled"
    return (last if failed else None), at


def check_backup(backup: dict | None) -> bool:
    """One pass over the supervisor's backup summary (status page data). Remembers
    the last run seen in the activity log database so a restart does not repeat it."""
    failure, seen = new_backup_failure(backup, audit.get_state("notify.backup"))
    if seen is not None:
        audit.set_state("notify.backup", seen)
    if failure:
        event("backup.failed", "backup", {"error": str(failure.get("error") or "")[:200]})
    return bool(failure)


def _loop() -> None:
    import headscale as hs  # needs HEADSCALE_API_KEY: imported here, not by the tests

    while True:
        try:
            if "device.expiring" in events():
                check_expiring(hs.all_nodes())
            if "backup.failed" in events():
                check_backup((hs.control_status() or {}).get("backup"))
        except Exception as exc:  # noqa: BLE001 - keep the thread alive
            log.warning("notifications: expiry check failed: %s", exc)
        time.sleep(CHECK_INTERVAL)


def on_audit_event(action: str, target: str, details: dict | None) -> None:
    """Registered with audit.subscribe() at start-up: every stored event is offered to the notifier."""
    event(action, target, details)


def start() -> None:
    dests = destinations()
    if not dests:
        return
    log.info("notifications: %s; events: %s", ", ".join(d["label"] for d in dests), ", ".join(sorted(events())))
    threading.Thread(target=_loop, name="notify-expiry", daemon=True).start()
