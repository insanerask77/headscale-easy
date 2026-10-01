"""Server-side sessions: the signed cookie carries a session id (sid) and this
table decides whether it is still valid, so a session can be revoked at once
(sign out everywhere, a role change, a deleted user).

Rows live in an SQLite file (SESSIONS_DB, /data/sessions.db, mode 600 like the
activity log): sid, sub, kind, role, ip, ua, created, last_seen, revoked.
If the file cannot be opened, an in-memory database is used (sessions then
last until the web UI restarts) and a warning is logged.

Also here: the per-IP sliding-window rate limiter used by the sign-in routes.

Headscale Easy · https://github.com/insanerask77/headscale-easy
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
import time

log = logging.getLogger("headscale-easy")

DB_PATH = os.environ.get("SESSIONS_DB", "/data/sessions.db")
TOUCH_EVERY = 60  # seconds between last_seen updates
PURGE_AFTER = 7 * 86400  # revoked or expired rows are kept this long, then deleted

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid       TEXT PRIMARY KEY,
    sub       TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT '',
    role      TEXT NOT NULL DEFAULT '',
    name      TEXT NOT NULL DEFAULT '',
    ip        TEXT NOT NULL DEFAULT '',
    ua        TEXT NOT NULL DEFAULT '',
    created   REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires   REAL NOT NULL,
    revoked   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sessions_sub ON sessions (sub);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path = ""


def configure(path: str) -> None:
    """Use another database file (tests)."""
    global DB_PATH, _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        DB_PATH, _conn, _conn_path = path, None, ""


def _open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    if path != ":memory:":
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
    return conn


def _db() -> sqlite3.Connection:
    """The shared connection (opened on first use). Callers hold _lock."""
    global _conn, _conn_path
    if _conn is None or _conn_path != DB_PATH:
        if _conn is not None:
            _conn.close()
        try:
            _conn = _open(DB_PATH)
        except (sqlite3.Error, OSError) as exc:
            log.warning("sessions: cannot open %s (%s): using memory, sessions end on restart", DB_PATH, exc)
            _conn = _open(":memory:")
        _conn_path = DB_PATH
    return _conn


def create(data: dict, ip: str = "", ua: str = "", ttl: float = 0) -> str:
    """Register a new session for the cookie payload `data`; returns its sid.
    Older sessions of the same user with another role are revoked (the role
    changed since they signed in)."""
    sid = secrets.token_urlsafe(24)
    now = time.time()
    role = data.get("role") or ("admin" if data.get("admin") else "member")
    sub = str(data.get("sub") or "")
    with _lock:
        db = _db()
        if sub:
            db.execute("UPDATE sessions SET revoked = 1 WHERE sub = ? AND role != ? AND revoked = 0", (sub, role))
        db.execute("DELETE FROM sessions WHERE (revoked = 1 OR expires < ?) AND last_seen < ?",
                   (now, now - PURGE_AFTER))
        db.execute("INSERT INTO sessions (sid, sub, kind, role, name, ip, ua, created, last_seen, expires) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (sid, sub, data.get("kind", ""), role,
                    str(data.get("username") or data.get("name") or "")[:100], ip[:64], ua[:200],
                    now, now, data.get("exp") or (now + (ttl or 0))))
    return sid


def validate(session: dict | None) -> bool:
    """True when the cookie payload names a live, unrevoked session with the same role."""
    sid = (session or {}).get("sid")
    if not sid or not isinstance(sid, str):
        return False  # cookie from before revocable sessions: sign in again
    now = time.time()
    with _lock:
        row = _db().execute("SELECT role, revoked, expires, last_seen FROM sessions WHERE sid = ?", (sid,)).fetchone()
        if row is None or row["revoked"] or row["expires"] < now:
            return False
        role = session.get("role") or ("admin" if session.get("admin") else "member")
        if row["role"] != role:
            return False
        if now - row["last_seen"] >= TOUCH_EVERY:
            _db().execute("UPDATE sessions SET last_seen = ? WHERE sid = ?", (now, sid))
    return True


def _rows(where: str, args: tuple) -> list[dict]:
    with _lock:
        cur = _db().execute(
            f"SELECT sid, sub, kind, role, name, ip, ua, created, last_seen, expires FROM sessions "
            f"WHERE revoked = 0 AND expires >= ? {where} ORDER BY last_seen DESC", (time.time(),) + args)
        return [dict(r) for r in cur.fetchall()]


def list_for(session: dict) -> list[dict]:
    """The caller's own sessions (same user; API-key sessions are matched by sid only)."""
    sub = str(session.get("sub") or "")
    if sub:
        return _rows("AND sub = ?", (sub,))
    return _rows("AND sid = ?", (session.get("sid", ""),))


def list_all() -> list[dict]:
    return _rows("", ())


def get(sid: str) -> dict | None:
    rows = _rows("AND sid = ?", (sid,))
    return rows[0] if rows else None


def revoke(sid: str) -> bool:
    with _lock:
        return _db().execute("UPDATE sessions SET revoked = 1 WHERE sid = ? AND revoked = 0", (sid,)).rowcount > 0


def revoke_user(sub: str, keep: str = "") -> int:
    """Revoke every session of a user (except `keep`, the caller's own)."""
    if not sub:
        return 0
    with _lock:
        return _db().execute("UPDATE sessions SET revoked = 1 WHERE sub = ? AND sid != ? AND revoked = 0",
                             (sub, keep)).rowcount


def revoke_named(name: str) -> int:
    """Revoke the sessions of a user by name (e.g. when the account is deleted)."""
    if not name:
        return 0
    with _lock:
        return _db().execute("UPDATE sessions SET revoked = 1 WHERE name = ? AND kind = 'oidc' AND revoked = 0",
                             (name,)).rowcount


def revoke_everyone(keep: str = "") -> int:
    with _lock:
        return _db().execute("UPDATE sessions SET revoked = 1 WHERE sid != ? AND revoked = 0", (keep,)).rowcount


# -----------------------------------------------------------------------------
# Rate limiting: sliding window per key (an IP address), in memory
# -----------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


LIMIT = _env_int("SIGNIN_RATE_LIMIT", 10)
WINDOW = _env_int("SIGNIN_RATE_WINDOW", 600)

_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def _recent(key: str, now: float) -> list[float]:
    hits = [t for t in _hits.get(key, []) if now - t < WINDOW]
    if hits:
        _hits[key] = hits
    else:
        _hits.pop(key, None)
    return hits


def blocked(key: str) -> int:
    """0 when the key may try; otherwise the seconds until it may again."""
    now = time.time()
    with _hits_lock:
        hits = _recent(key, now)
        if len(hits) < LIMIT:
            return 0
        return max(1, int(hits[0] + WINDOW - now) + 1)


def hit(key: str) -> None:
    now = time.time()
    with _hits_lock:
        hits = _recent(key, now)
        hits.append(now)
        _hits[key] = hits
        if len(_hits) > 10000:  # bound the memory under a flood of addresses
            for k in list(_hits):
                _recent(k, now)


def reset(key: str) -> None:
    with _hits_lock:
        _hits.pop(key, None)
