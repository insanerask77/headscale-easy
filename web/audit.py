"""Activity log: who changed what in the tailnet, and what happened to devices.

The feasible part of Tailscale's "Logs" (network flow logs need the
coordination server's data plane, which Headscale does not have):

  - Configuration events: every change made from the web UI (machines, users,
    keys, access control policy, DNS, settings), with the actor, the client IP
    and the details. Secrets are never stored: keys are reduced to a prefix.
  - Sign-in events: console sign-ins, sign-outs and failed API key sign-ins.
  - Device events: a background thread compares Headscale's state every
    INTERVAL seconds and logs devices that register, disappear, connect,
    disconnect, whose key expires, whose Tailscale version changes, or that
    are renamed outside the web UI.

Events live in an append-only SQLite table at AUDIT_DB (/data/audit.db, i.e.
./data/web/audit.db on the host). Events older than AUDIT_RETENTION_DAYS
(default 90, 0 = keep forever) are purged once an hour.

Other modules log with one line:

    audit.request_event(self, session, "user.create", name, {"display_name": ...})   # in a Handler
    audit.record("system", "machine.rename", name, {...}, ref="node:12")            # no request

record() and request_event() never raise: a broken log must not break the UI.
"""

from __future__ import annotations

import csv
import difflib
import io
import ipaddress
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from i18n import _

log = logging.getLogger("headscale-easy")

DB_PATH = os.environ.get("AUDIT_DB", "/data/audit.db")


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


RETENTION_DAYS = _int_env("AUDIT_RETENTION_DAYS", 90)  # 0 = keep forever
INTERVAL = 30          # seconds between two looks at Headscale's devices
PURGE_EVERY = 3600     # seconds
PAGE_SIZE = 50
CSV_MAX_ROWS = 100_000
MAX_DETAILS = 60_000   # bytes of JSON per event
MAX_DIFF = 20_000      # characters of an ACL diff

CATEGORIES = ("config", "devices", "signin")
SYSTEM = "system"          # actor of automatic changes made by the web UI
HEADSCALE = "headscale"    # actor of device events (seen in Headscale's state)


def category_of(action: str) -> str:
    if action.startswith("auth."):
        return "signin"
    if action.startswith("device."):
        return "devices"
    return "config"


def category_labels() -> dict[str, str]:
    return {"config": _("Configuration"), "devices": _("Devices"), "signin": _("Sign-in")}


def action_labels() -> dict[str, str]:
    """Readable text of each action code, in the viewer's language. Unknown
    codes are shown as they are."""
    return {
        # sign-in
        "auth.signin": _("Signed in to the console"),
        "auth.signin_failed": _("Failed sign-in"),
        "auth.signout": _("Signed out of the console"),
        "auth.rate_limited": _("Sign-in blocked: too many attempts"),
        "auth.session_revoked": _("Revoked a session"),
        "auth.sessions_revoked_all": _("Signed out everywhere"),
        # machines
        "machine.rename": _("Renamed machine"),
        "machine.delete": _("Removed machine"),
        "machine.expire": _("Expired machine key"),
        "machine.expiry_disable": _("Disabled key expiry"),
        "machine.expiry_enable": _("Enabled key expiry"),
        "machine.routes": _("Changed approved routes"),
        "machine.tags": _("Changed tags"),
        "machine.register": _("Registered machine with Auth ID"),
        "machines.remove_inactive": _("Removed inactive machines"),
        "machines.bulk_expire": _("Expired keys on several machines"),
        "machines.bulk_remove": _("Removed several machines"),
        "machines.bulk_tags": _("Added a tag to several machines"),
        # users
        "user.create": _("Created user"),
        "user.rename": _("Renamed user"),
        "user.delete": _("Deleted user"),
        "user.password_reset": _("Reset user password"),
        # keys
        "authkey.create": _("Generated auth key"),
        "authkey.revoke": _("Revoked auth key"),
        "apikey.create": _("Created API key"),
        "apikey.expire": _("Expired API key"),
        # invitations
        "invite.create": _("Created invitation"),
        "invite.revoke": _("Revoked invitation"),
        "invite.accept": _("Accepted invitation"),
        # policy, DNS and settings
        "acl.save": _("Saved access control policy"),
        "acl.rule_save": _("Saved an ACL rule"),
        "acl.rule_delete": _("Deleted an ACL rule"),
        "acl.group_save": _("Saved an ACL group"),
        "acl.group_delete": _("Deleted an ACL group"),
        "acl.tag_save": _("Saved an ACL tag owner"),
        "acl.tag_delete": _("Deleted an ACL tag owner"),
        "acl.auto_route_save": _("Saved an auto-approved route"),
        "acl.auto_route_delete": _("Deleted an auto-approved route"),
        "acl.auto_exit_save": _("Saved the auto-approved exit node approvers"),
        "acl.ssh_rule_save": _("Saved an SSH rule"),
        "acl.ssh_rule_delete": _("Deleted an SSH rule"),
        "dns.save": _("Changed DNS settings"),
        "settings.key_expiry": _("Changed device key expiry"),
        "settings.mfa": _("Changed two-factor authentication"),
        "settings.notify_test": _("Sent a test notification"),
        "backup.run": _("Started a backup"),
        "backup.settings": _("Changed the backup settings"),
        "backup.restore": _("Restored a backup"),
        "backup.download": _("Downloaded a backup"),
        "backup.upload": _("Uploaded a backup"),
        # devices (seen in Headscale's state)
        "device.registered": _("Device registered"),
        "device.removed": _("Device removed"),
        "device.connected": _("Device connected"),
        "device.disconnected": _("Device disconnected"),
        "device.key_expired": _("Device key expired"),
        "device.version": _("Tailscale version changed"),
        "device.renamed": _("Device renamed outside the console"),
    }


# -----------------------------------------------------------------------------
# Store
# -----------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,              -- UTC, 2026-09-29T10:00:00.000000Z (sorts as text)
    category TEXT NOT NULL,              -- config | devices | signin
    action   TEXT NOT NULL,              -- e.g. machine.rename
    actor    TEXT NOT NULL DEFAULT '',
    target   TEXT NOT NULL DEFAULT '',
    ref      TEXT NOT NULL DEFAULT '',   -- stable id of the target, e.g. node:12
    details  TEXT NOT NULL DEFAULT '{}', -- JSON
    ip       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_cat_ts ON events (category, ts);
CREATE INDEX IF NOT EXISTS events_actor ON events (actor);
CREATE INDEX IF NOT EXISTS events_ref ON events (ref, action);
-- Append-only: rows are never changed (only purged by age)
CREATE TRIGGER IF NOT EXISTS events_append_only BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'the activity log is append-only'); END;
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path = ""
_last_error = 0.0


def configure(path: str) -> None:
    """Use another database file (tests)."""
    global DB_PATH, _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        DB_PATH, _conn, _conn_path = path, None, ""


def _db() -> sqlite3.Connection:
    """The shared connection (opened on first use). Callers hold _lock."""
    global _conn, _conn_path
    if _conn is None or _conn_path != DB_PATH:
        conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        # Emails and client IPs: readable only by the web UI's user (the WAL
        # and shared-memory files take the database's permissions)
        for path in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm"):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        _conn, _conn_path = conn, DB_PATH
    return _conn


def available() -> bool:
    try:
        with _lock:
            _db()
        return True
    except (sqlite3.Error, OSError):
        return False


def now_ts(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- secrets never reach the log ---
_SECRET_RE = re.compile(r"hskey-(api|auth|authreq)-([A-Za-z0-9_-]{1,12})[A-Za-z0-9_-]*")
_SECRET_KEYS = {"key", "api_key", "apikey", "secret", "token", "password", "auth_key", "authkey"}


def prefix(secret: str) -> str:
    """Loggable part of a key: 'hskey-auth-abcdef123456…'. Older formats
    (plain hex) keep their first 6 characters."""
    secret = str(secret or "")
    m = _SECRET_RE.match(secret)
    if m:
        return f"hskey-{m.group(1)}-{m.group(2)}…"
    return (secret[:6] + "…") if secret else ""


def _scrub(value, key: str = ""):
    if isinstance(value, dict):
        return {str(k): _scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_scrub(v, key) for v in value]
    if isinstance(value, str):
        if key.lower() in _SECRET_KEYS and value and not value.endswith("…"):
            return prefix(value)
        return _SECRET_RE.sub(lambda m: f"hskey-{m.group(1)}-{m.group(2)}…", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


# Functions called with (action, target, details) after every stored event. notify.py registers itself at
# start-up, so this module does not depend on it.
_listeners: list = []


def subscribe(listener) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def _notify(action: str, target: str, details: dict | None) -> None:
    for listener in list(_listeners):
        try:
            listener(action, target, details)
        except Exception:  # noqa: BLE001 - a listener never breaks the log
            pass


def record(actor: str, action: str, target: str = "", details: dict | None = None, ip: str = "",
           ref: str = "", ts: str | None = None) -> int | None:
    """Append one event. Returns its id, or None if it could not be stored."""
    global _last_error
    try:
        data = json.dumps(_scrub(details or {}), ensure_ascii=False, sort_keys=True, default=str)
        if len(data) > MAX_DETAILS:
            data = json.dumps({"truncated": True, "size": len(data)})
        row = (ts or now_ts(), category_of(action), action[:100], str(actor or "")[:200],
               _scrub(str(target or ""))[:300], str(ref or "")[:100], data, str(ip or "")[:64])
        with _lock:
            cur = _db().execute(
                "INSERT INTO events (ts, category, action, actor, target, ref, details, ip) VALUES (?,?,?,?,?,?,?,?)",
                row)
            rowid = cur.lastrowid
        _notify(action, target, details)
        return rowid
    except Exception as exc:  # noqa: BLE001 - never break the caller
        if time.time() - _last_error > 300:  # do not flood the container log
            log.warning("could not write to the activity log (%s): %s", DB_PATH, exc)
            _last_error = time.time()
        return None


# --- request helpers (web/app.py) ---
# Only the internal reverse proxy (Caddy, on the Docker network) may tell us
# the client IP. The web UI's port is not published, so nobody else reaches it
# from these addresses. AUDIT_TRUSTED_PROXIES (comma-separated CIDRs) overrides.
TRUSTED_PROXIES = [ipaddress.ip_network(n.strip(), strict=False) for n in os.environ.get(
    "AUDIT_TRUSTED_PROXIES", "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7").split(",")
    if n.strip()]


def _trusted_peer(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in TRUSTED_PROXIES)


def client_ip(handler) -> str:
    peer = (getattr(handler, "client_address", None) or ("",))[0]
    if not _trusted_peer(peer):
        return peer
    candidates = [handler.headers.get("X-Real-IP", "")]
    # Rightmost entry: added by the proxy in front of us, not by the client
    candidates += [x.strip() for x in reversed((handler.headers.get("X-Forwarded-For") or "").split(","))][:1]
    for value in candidates:
        value = (value or "").strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return peer


def actor_of(session: dict | None) -> str:
    """Who is acting: the OIDC user name (or e-mail) or the API key prefix."""
    if not session:
        return ""
    if session.get("kind") == "apikey":
        key = session.get("key") or ""
        return f"api-key:{key}" if key else "api-key"
    return str(session.get("username") or session.get("email") or session.get("name") or session.get("sub") or "")


def request_event(handler, session: dict | None, action: str, target: str = "", details: dict | None = None,
                  ref: str = "", actor: str | None = None) -> int | None:
    """record() with the actor taken from the session and the client IP from the request."""
    try:
        ip = client_ip(handler)
    except Exception:  # noqa: BLE001
        ip = ""
    return record(actor if actor is not None else actor_of(session), action, target, details, ip=ip, ref=ref)


# --- before/after helpers ---
def changes(before: dict, after: dict) -> dict:
    """{field: {"from": old, "to": new}} for the fields that changed."""
    out = {}
    for k in sorted(set(before or {}) | set(after or {})):
        a, b = (before or {}).get(k), (after or {}).get(k)
        if a != b:
            out[k] = {"from": a, "to": b}
    return out


def text_diff(before: str, after: str) -> dict:
    """Unified diff of two texts (the ACL policy), capped in size."""
    lines = list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                      "before", "after", lineterm="", n=2))
    added = sum(1 for x in lines if x.startswith("+") and not x.startswith("+++"))
    removed = sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))
    text = "\n".join(lines)
    if len(text) > MAX_DIFF:
        text = text[:MAX_DIFF] + "\n…"
    return {"diff": text, "added": added, "removed": removed}


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------

def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _where(f: dict) -> tuple[str, list]:
    clauses, params = [], []
    if f.get("category") in CATEGORIES:
        clauses.append("category = ?")
        params.append(f["category"])
    if f.get("actor"):
        clauses.append("actor = ?")
        params.append(f["actor"])
    if f.get("action"):
        clauses.append("action = ?")
        params.append(f["action"])
    if f.get("target"):
        clauses.append("(target = ? OR ref = ?)")
        params += [f["target"], f["target"]]
    if f.get("since"):
        clauses.append("ts >= ?")
        params.append(f["since"])
    if f.get("until"):
        clauses.append("ts < ?")
        params.append(f["until"])
    if f.get("text"):
        like = _like(f["text"])
        parts = ["action LIKE ? ESCAPE '\\'", "actor LIKE ? ESCAPE '\\'", "target LIKE ? ESCAPE '\\'",
                 "details LIKE ? ESCAPE '\\'", "ip LIKE ? ESCAPE '\\'"]
        params += [like] * len(parts)
        # Also match the readable text of the action in the viewer's language
        needle = f["text"].lower()
        codes = [code for code, text in action_labels().items() if needle in text.lower()]
        if codes:
            parts.append(f"action IN ({','.join('?' * len(codes))})")
            params += codes
        clauses.append("(" + " OR ".join(parts) + ")")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _row(r: sqlite3.Row) -> dict:
    ev = dict(r)
    try:
        ev["details"] = json.loads(ev.get("details") or "{}")
    except ValueError:
        ev["details"] = {}
    return ev


def query(filters: dict | None = None, limit: int = PAGE_SIZE, offset: int = 0) -> tuple[list[dict], int]:
    """(events newest first, total matching). filters: category, actor, action,
    target (label or ref), since/until (timestamps), text."""
    where, params = _where(filters or {})
    with _lock:
        db = _db()
        total = db.execute(f"SELECT COUNT(*) FROM events{where}", params).fetchone()[0]
        rows = db.execute(f"SELECT * FROM events{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                          params + [int(limit), int(offset)]).fetchall()
    return [_row(r) for r in rows], total


def actors() -> list[str]:
    with _lock:
        rows = _db().execute("SELECT actor FROM events WHERE actor != '' GROUP BY actor "
                             "ORDER BY MAX(ts) DESC LIMIT 200").fetchall()
    return sorted((r[0] for r in rows), key=str.lower)


def purge(days: int | None = None, now: datetime | None = None) -> int:
    """Delete events older than the retention. Returns how many."""
    days = RETENTION_DAYS if days is None else days
    if days <= 0:
        return 0
    cutoff = now_ts((now or datetime.now(timezone.utc)) - timedelta(days=days))
    with _lock:
        n = _db().execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
    if n:
        log.info("activity log: purged %d event(s) older than %d days", n, days)
    return n


def get_state(key: str):
    with _lock:
        row = _db().execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_state(key: str, value) -> None:
    with _lock:
        _db().execute("INSERT INTO state (key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


# -----------------------------------------------------------------------------
# CSV export
# -----------------------------------------------------------------------------

def to_csv(events: list[dict]) -> str:
    labels, cats = action_labels(), category_labels()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_utc", "category", "action", "description", "actor", "target", "ref", "ip", "details"])
    for ev in events:
        w.writerow([_csv_safe(v) for v in (
            ev["ts"], cats.get(ev["category"], ev["category"]), ev["action"],
            labels.get(ev["action"], ev["action"]), ev["actor"], ev["target"], ev["ref"], ev["ip"],
            json.dumps(ev["details"], ensure_ascii=False, sort_keys=True))])
    return out.getvalue()


def _csv_safe(value) -> str:
    """No spreadsheet formulas from user-controlled text (CSV injection)."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


# -----------------------------------------------------------------------------
# Device events: diff of Headscale's state
# -----------------------------------------------------------------------------

def _expired(expiry: str | None, now: datetime) -> bool:
    if not expiry or expiry.startswith("0001-"):
        return False
    try:
        return datetime.fromisoformat(expiry.replace("Z", "+00:00")) < now
    except ValueError:
        return False


def snapshot(nodes: list[dict], details: dict[str, dict] | None = None, now: datetime | None = None) -> dict:
    """What we compare: {node_id: {name, user, online, expired, version, ips}}."""
    now = now or datetime.now(timezone.utc)
    details = details or {}
    snap = {}
    for n in nodes:
        nid = str(n.get("id"))
        hi = (details.get(nid) or {}).get("hostinfo") or {}
        snap[nid] = {
            "name": n.get("givenName") or n.get("name") or "",
            "user": (n.get("user") or {}).get("name") or "",
            "online": bool(n.get("online")),
            "expired": _expired(n.get("expiry"), now),
            "version": (hi.get("IPNVersion") or "").split("-")[0],
            "ips": list(n.get("ipAddresses") or []),
        }
    return snap


def diff(prev: dict, cur: dict, console_renamed: set[tuple[str, str]] | None = None) -> list[tuple[str, str, dict, str]]:
    """Device events between two snapshots: [(action, target, details, ref)].
    console_renamed: (ref, new name) pairs, e.g. ("node:12", "laptop"), of
    renames made from the web UI since prev: already logged as configuration
    events."""
    console_renamed = console_renamed or set()
    events = []
    for nid in sorted(set(cur) - set(prev), key=_num):
        n = cur[nid]
        events.append(("device.registered", n["name"], {"user": n["user"], "ips": n["ips"], "version": n["version"]},
                       f"node:{nid}"))
    for nid in sorted(set(prev) - set(cur), key=_num):
        n = prev[nid]
        events.append(("device.removed", n["name"], {"user": n["user"]}, f"node:{nid}"))
    for nid in sorted(set(prev) & set(cur), key=_num):
        a, b, ref = prev[nid], cur[nid], f"node:{nid}"
        if a["name"] != b["name"] and (ref, b["name"]) not in console_renamed:
            events.append(("device.renamed", b["name"], {"from": a["name"], "to": b["name"], "user": b["user"]}, ref))
        if a["online"] != b["online"]:
            events.append(("device.connected" if b["online"] else "device.disconnected", b["name"],
                           {"user": b["user"]}, ref))
        if b["expired"] and not a["expired"]:
            events.append(("device.key_expired", b["name"], {"user": b["user"]}, ref))
        if a.get("version") and b.get("version") and a["version"] != b["version"]:
            events.append(("device.version", b["name"], {"from": a["version"], "to": b["version"],
                                                         "user": b["user"]}, ref))
    return events


def _num(value: str):
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def console_renames(since: str) -> set[tuple[str, str]]:
    with _lock:
        rows = _db().execute("SELECT ref, details FROM events WHERE action = 'machine.rename' AND ts >= ?",
                             (since,)).fetchall()
    out = set()
    for ref, details in rows:
        try:
            out.add((ref, str(json.loads(details).get("to") or "")))
        except ValueError:
            continue
    return out


def watch_once(nodes: list[dict], details: dict[str, dict] | None = None, now: datetime | None = None) -> int:
    """One pass of the device watcher with Headscale's current state. The
    previous state is kept in the database, so a restart does not lose (or
    repeat) events. Returns how many events were logged."""
    now = now or datetime.now(timezone.utc)
    cur = snapshot(nodes, details, now)
    saved = get_state("devices")
    set_state("devices", {"ts": now_ts(now), "nodes": cur})
    if saved is None:
        return 0  # first run: this is the baseline
    since = now_ts(datetime.fromisoformat(saved["ts"].replace("Z", "+00:00")) - timedelta(seconds=INTERVAL))
    events = diff(saved.get("nodes") or {}, cur, console_renames(since))
    for action, target, det, ref in events:
        record(HEADSCALE, action, target, det, ref=ref)
    return len(events)


def _loop() -> None:
    import headscale as hs  # needs HEADSCALE_API_KEY: imported here, not by the tests

    last_purge = 0.0
    while True:
        try:
            if time.time() - last_purge > PURGE_EVERY:
                purge()
                last_purge = time.time()
            nodes = hs.all_nodes()
            watch_once(nodes, hs.host_details([str(n["id"]) for n in nodes]))
        except Exception as exc:  # noqa: BLE001 - keep the thread alive
            log.warning("activity log: device check failed: %s", exc)
        time.sleep(INTERVAL)


def start() -> None:
    if not available():
        log.warning("activity log: cannot open %s (is /data writable?): disabled", DB_PATH)
        return
    log.info("activity log: %s, retention %s", DB_PATH,
             f"{RETENTION_DAYS} days" if RETENTION_DAYS else "forever")
    threading.Thread(target=_loop, name="audit", daemon=True).start()
