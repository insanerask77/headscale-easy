"""SQLite plumbing for the local accounts: schema, connections, timestamps.

Accounts live in /data/console/accounts.db (mode 600). ``configure()`` must be called once at start-up.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

# Schema version for migrations
SCHEMA_VERSION = 1

# Database path and connection (set by configure())
_db_path: str | None = None
_memory_conn: sqlite3.Connection | None = None  # Shared connection for :memory:

# -----------------------------------------------------------------------------
# Database setup
# -----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    headscale_user TEXT,  -- links to Headscale user.name
    role TEXT NOT NULL DEFAULT 'member',  -- admin, network_admin, auditor, member
    pw_hash TEXT NOT NULL,
    totp_secret TEXT,  -- base32-encoded secret, NULL = not enrolled
    totp_confirmed INTEGER NOT NULL DEFAULT 0,  -- 0 = enrolled but not confirmed, 1 = active
    totp_last_step INTEGER,  -- replay protection: last TOTP step used
    recovery_codes TEXT,  -- newline-separated hashed codes
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change INTEGER NOT NULL DEFAULT 0,  -- 1 = choose a new password at the next sign-in
    created TEXT NOT NULL,  -- ISO8601 UTC
    updated TEXT NOT NULL   -- ISO8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts(username);
CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);
CREATE INDEX IF NOT EXISTS idx_accounts_headscale_user ON accounts(headscale_user);

CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,  -- 'invite' or 'reset'
    token_hash TEXT NOT NULL UNIQUE,  -- SHA-256 hex
    account_id INTEGER,  -- NULL for invitations (account not created yet)
    role TEXT,  -- for invitations: the role the account will have
    email TEXT,  -- for invitations: the email the account will have
    expires TEXT NOT NULL,  -- ISO8601 UTC
    used_at TEXT,  -- ISO8601 UTC, NULL = not used yet
    created TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_kind ON tokens(kind);
CREATE INDEX IF NOT EXISTS idx_tokens_account_id ON tokens(account_id);

-- Keys that allow self-registration when the sign-up mode is "invite"
CREATE TABLE IF NOT EXISTS signup_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL UNIQUE,  -- SHA-256 hex; the key itself is shown once
    label TEXT NOT NULL DEFAULT '',
    max_uses INTEGER NOT NULL DEFAULT 1,  -- 0 = unlimited
    uses INTEGER NOT NULL DEFAULT 0,
    expires TEXT,  -- ISO8601 UTC, NULL = never
    revoked INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def _migrate(conn) -> None:
    """Columns added after the first release (CREATE IF NOT EXISTS skips them)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "must_change" not in cols:  # 1 = must choose a new password at the next sign-in
        conn.execute("ALTER TABLE accounts ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")


def configure(path: str = "/data/console/accounts.db") -> None:
    """Initialize the database at the given path. Creates the file with mode
    600 if it does not exist. Call this once at startup."""
    global _db_path, _memory_conn
    _db_path = path

    # For :memory:, create and keep a persistent connection
    if path == ":memory:":
        _memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
        _memory_conn.row_factory = sqlite3.Row
        _memory_conn.execute("PRAGMA foreign_keys = ON")
        _memory_conn.executescript(SCHEMA)
        _migrate(_memory_conn)
        version = _memory_conn.execute("SELECT version FROM schema_version").fetchone()
        if version is None:
            _memory_conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        _memory_conn.commit()
        return

    # Create parent directory if needed
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)

    # Create database file with restricted permissions if it doesn't exist
    if not os.path.exists(path):
        open(path, 'a').close()
        os.chmod(path, 0o600)

    # Create tables
    with _db() as db:
        db.executescript(SCHEMA)
        _migrate(db)
        # Schema version tracking
        version = db.execute("SELECT version FROM schema_version").fetchone()
        if version is None:
            db.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        db.commit()


@contextmanager
def _db():
    """Context manager for database connections. Commits on success, rolls back
    on exception. For :memory:, reuses the persistent connection."""
    if _db_path is None:
        raise RuntimeError("local_accounts.configure() not called")

    # For :memory:, use the persistent connection
    if _db_path == ":memory:":
        if _memory_conn is None:
            raise RuntimeError("configure() not properly initialized for :memory:")
        try:
            yield _memory_conn
            _memory_conn.commit()
        except Exception:
            _memory_conn.rollback()
            raise
        return

    # For file-based databases, create a new connection
    conn = sqlite3.connect(_db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    """Current timestamp in ISO8601 UTC format."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a Row to a dict, or None."""
    return dict(row) if row is not None else None
