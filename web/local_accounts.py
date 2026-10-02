"""Local account authentication: users, passwords, TOTP, invitations.

Replaces the bundled Authentik (phase 1 of the simplification plan). Accounts
are stored in /data/console/accounts.db (mode 600), passwords are hashed with
scrypt, TOTP follows RFC 6238, and invitation/reset tokens are single-use.

Standard library only, no dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Database path and connection (set by configure())
_db_path: str | None = None
_memory_conn: sqlite3.Connection | None = None  # Shared connection for :memory:

# Schema version for migrations
SCHEMA_VERSION = 1

# Password requirements
MIN_PASSWORD_LENGTH = 8

# TOTP settings (RFC 6238)
TOTP_PERIOD = 30  # seconds
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # accept ±1 step (±30s)

# Token expiration defaults
DEFAULT_INVITATION_HOURS = 168  # 7 days
DEFAULT_RESET_HOURS = 24


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

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


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


# -----------------------------------------------------------------------------
# Placeholder functions (implemented in later blocks)
# -----------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Hash a password with scrypt. Returns the hash in a format that includes
    the salt and parameters."""
    raise NotImplementedError("Block 2")


def verify_password(plain: str, hash_str: str) -> bool:
    """Verify a password against a stored hash. Constant-time comparison."""
    raise NotImplementedError("Block 2")


def create_account(username: str, email: str, password: str, role: str = 'member',
                   headscale_user: str | None = None) -> int:
    """Create a new account. Returns the account ID."""
    raise NotImplementedError("Block 2")


def get_account(username: str | None = None, email: str | None = None,
                id: int | None = None) -> dict | None:
    """Get an account by username, email, or id. Returns None if not found."""
    raise NotImplementedError("Block 2")


def list_accounts() -> list[dict]:
    """List all accounts."""
    raise NotImplementedError("Block 2")


def update_password(account_id: int, new_password: str) -> None:
    """Update an account's password."""
    raise NotImplementedError("Block 2")


def disable_account(account_id: int) -> None:
    """Disable an account (cannot sign in)."""
    raise NotImplementedError("Block 2")


def enable_account(account_id: int) -> None:
    """Enable a previously disabled account."""
    raise NotImplementedError("Block 2")


def delete_account(account_id: int) -> None:
    """Delete an account and all its tokens."""
    raise NotImplementedError("Block 2")


# TOTP functions (Block 3)
def generate_totp_secret() -> str:
    raise NotImplementedError("Block 3")


def compute_totp(secret: str, timestamp: int | None = None) -> str:
    raise NotImplementedError("Block 3")


def verify_totp(secret: str, code: str, last_step: int | None = None) -> tuple[bool, int]:
    raise NotImplementedError("Block 3")


def generate_recovery_codes(n: int = 8) -> list[str]:
    raise NotImplementedError("Block 3")


def hash_recovery_codes(codes: list[str]) -> str:
    raise NotImplementedError("Block 3")


def verify_recovery_code(hashed: str, code: str) -> tuple[bool, str]:
    raise NotImplementedError("Block 3")


def enroll_totp(account_id: int) -> tuple[str, str]:
    raise NotImplementedError("Block 3")


def confirm_totp(account_id: int, code: str) -> bool:
    raise NotImplementedError("Block 3")


def disable_totp(account_id: int) -> None:
    raise NotImplementedError("Block 3")


def reset_recovery_codes(account_id: int) -> list[str]:
    raise NotImplementedError("Block 3")


# Token functions (Block 4)
def create_invitation(email: str, role: str = 'member', expires_hours: int = DEFAULT_INVITATION_HOURS) -> str:
    raise NotImplementedError("Block 4")


def create_reset_token(account_id: int, expires_hours: int = DEFAULT_RESET_HOURS) -> str:
    raise NotImplementedError("Block 4")


def verify_token(token: str, kind: str) -> dict | None:
    raise NotImplementedError("Block 4")


def revoke_token(token: str) -> None:
    raise NotImplementedError("Block 4")


def list_active_invitations() -> list[dict]:
    raise NotImplementedError("Block 4")


# Role functions (Block 5)
def get_account_role(account_id: int) -> str:
    raise NotImplementedError("Block 5")


def set_account_role(account_id: int, role: str) -> None:
    raise NotImplementedError("Block 5")
