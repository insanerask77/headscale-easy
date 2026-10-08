"""Single-use secrets of the local accounts, stored hashed: invitation and password-reset tokens, and
the keys that open self-registration in "invite" mode."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from accounts_db import _db, _now
from local_accounts import get_account

log = logging.getLogger("local_accounts")

# Token expiration defaults
DEFAULT_INVITATION_HOURS = 168  # 7 days
DEFAULT_RESET_HOURS = 24


def _hash_token(token: str) -> str:
    """Hash a token with SHA-256. Returns hex digest."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_invitation(email: str, role: str = 'member', expires_hours: int = DEFAULT_INVITATION_HOURS) -> str:
    """Create an invitation token for a new account.

    Args:
        email: Email address for the invitation
        role: Role the account will have (default: 'member')
        expires_hours: Hours until expiration (default: 168 = 7 days)

    Returns:
        Unhashed token (32 bytes urlsafe base64). Store this securely, as it
        cannot be retrieved later (only the hash is stored).
    """
    # Validate inputs
    email = email.strip().lower()
    if not email:
        raise ValueError("Email is required")

    if role not in ('admin', 'network_admin', 'auditor', 'member'):
        raise ValueError(f"Invalid role: {role}")

    # Generate random token (32 bytes = 256 bits)
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)

    # Calculate expiration
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=expires_hours)

    # Store in database
    with _db() as db:
        db.execute(
            """INSERT INTO tokens (kind, token_hash, account_id, role, email, expires, created)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ('invite', token_hash, None, role, email, expires.isoformat(), now.isoformat())
        )

    log.info(f"Created invitation for {email} (role={role}, expires in {expires_hours}h)")
    return token


def create_reset_token(account_id: int, expires_hours: int = DEFAULT_RESET_HOURS) -> str:
    """Create a password reset token for an existing account.

    Args:
        account_id: Account ID to reset password for
        expires_hours: Hours until expiration (default: 24)

    Returns:
        Unhashed token (32 bytes urlsafe base64)
    """
    # Verify account exists
    account = get_account(id=account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    # Generate random token
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)

    # Calculate expiration
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=expires_hours)

    # Store in database
    with _db() as db:
        db.execute(
            """INSERT INTO tokens (kind, token_hash, account_id, role, email, expires, created)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ('reset', token_hash, account_id, None, account['email'], expires.isoformat(), now.isoformat())
        )

    log.info(f"Created reset token for account {account_id} ({account['username']})")
    return token


def check_token(token: str, kind: str) -> dict | None:
    """Check if a token is valid WITHOUT consuming it.

    Args:
        token: Unhashed token string
        kind: 'invite' or 'reset'

    Returns:
        Dict with token data if valid (same as verify_token), or None if invalid.
        Does NOT mark the token as used.
    """
    if kind not in ('invite', 'reset'):
        raise ValueError(f"Invalid token kind: {kind}")

    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)

    with _db() as db:
        # Find the token
        row = db.execute(
            """SELECT * FROM tokens
               WHERE token_hash = ? AND kind = ?""",
            (token_hash, kind)
        ).fetchone()

        if not row:
            return None

        token_data = dict(row)

        # Check if already used
        if token_data['used_at'] is not None:
            return None

        # Check if expired
        expires = datetime.fromisoformat(token_data['expires'])
        if now > expires:
            return None

        # Return relevant fields (without consuming)
        if kind == 'invite':
            return {
                'email': token_data['email'],
                'role': token_data['role'],
                'token_hash': token_hash
            }
        else:  # reset
            return {
                'account_id': token_data['account_id'],
                'email': token_data['email'],
                'token_hash': token_hash
            }


def verify_token(token: str, kind: str) -> dict | None:
    """Verify and consume a token (invitation or reset).

    Args:
        token: Unhashed token string
        kind: 'invite' or 'reset'

    Returns:
        Dict with token data if valid:
            - For 'invite': {email, role, token_hash}
            - For 'reset': {account_id, email, token_hash}
        None if token is invalid, expired, or already used.

    Side effect: Marks the token as used (single-use).
    """
    if kind not in ('invite', 'reset'):
        raise ValueError(f"Invalid token kind: {kind}")

    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)

    with _db() as db:
        # Find the token
        row = db.execute(
            """SELECT * FROM tokens
               WHERE token_hash = ? AND kind = ?""",
            (token_hash, kind)
        ).fetchone()

        if not row:
            return None

        token_data = dict(row)

        # Check if already used
        if token_data['used_at'] is not None:
            log.warning(f"Token already used: {token_hash[:16]}...")
            return None

        # Check if expired
        expires = datetime.fromisoformat(token_data['expires'])
        if now > expires:
            log.warning(f"Token expired: {token_hash[:16]}...")
            return None

        # Mark as used
        db.execute(
            "UPDATE tokens SET used_at = ? WHERE token_hash = ?",
            (now.isoformat(), token_hash)
        )

        # Return relevant fields
        if kind == 'invite':
            return {
                'email': token_data['email'],
                'role': token_data['role'],
                'token_hash': token_hash
            }
        else:  # reset
            return {
                'account_id': token_data['account_id'],
                'email': token_data['email'],
                'token_hash': token_hash
            }


def revoke_token(token: str) -> None:
    """Revoke a token by marking it as used.

    Args:
        token: Unhashed token string
    """
    token_hash = _hash_token(token)
    now = _now()

    with _db() as db:
        result = db.execute(
            "UPDATE tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now, token_hash)
        )

        if result.rowcount > 0:
            log.info(f"Revoked token: {token_hash[:16]}...")


def revoke_token_by_hash(token_hash: str) -> bool:
    """Revoke a token by its hash (for admin UI).

    Args:
        token_hash: SHA-256 hash of the token

    Returns:
        True if token was revoked, False if not found or already used
    """
    now = _now()

    with _db() as db:
        result = db.execute(
            "UPDATE tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now, token_hash)
        )

        if result.rowcount > 0:
            log.info(f"Revoked token: {token_hash[:16]}...")
            return True
        return False


def list_active_invitations() -> list[dict]:
    """List all active (not used, not expired) invitations.

    Returns:
        List of dicts with: {id, email, role, expires, created}
    """
    now = datetime.now(timezone.utc).isoformat()

    with _db() as db:
        rows = db.execute(
            """SELECT id, email, role, expires, created, token_hash
               FROM tokens
               WHERE kind = 'invite' AND used_at IS NULL AND expires > ?
               ORDER BY created DESC""",
            (now,)
        ).fetchall()

        return [dict(row) for row in rows]


# -----------------------------------------------------------------------------
# Sign-up keys (self-registration in "invite" mode)
# -----------------------------------------------------------------------------

def create_signup_key(label: str = "", max_uses: int = 1, expires_hours: int | None = None) -> str:
    """Create a sign-up key. max_uses 0 = unlimited; expires_hours None = never.
    Returns the key; only its hash is stored, so it cannot be shown again."""
    if max_uses < 0 or (expires_hours is not None and expires_hours <= 0):
        raise ValueError("Invalid key limits")
    from datetime import timedelta
    key = "hse-" + secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=expires_hours)).isoformat() if expires_hours else None
    with _db() as db:
        db.execute(
            "INSERT INTO signup_keys (key_hash, label, max_uses, expires, created) VALUES (?, ?, ?, ?, ?)",
            (_hash_token(key), label.strip()[:60], max_uses, expires, now.isoformat()))
    return key


def use_signup_key(key: str) -> int | None:
    """Spend one use of a key. Returns its id, or None if it is unknown,
    revoked, expired or used up (the caller cannot tell which: one error)."""
    now = _now()
    with _db() as db:
        # one statement: check and spend together, so two requests cannot both take the last use
        cur = db.execute(
            """UPDATE signup_keys SET uses = uses + 1
               WHERE key_hash = ? AND revoked = 0 AND (expires IS NULL OR expires > ?)
                 AND (max_uses = 0 OR uses < max_uses)""",
            (_hash_token(key or ""), now))
        if cur.rowcount != 1:
            return None
        row = db.execute("SELECT id FROM signup_keys WHERE key_hash = ?", (_hash_token(key),)).fetchone()
        return row["id"]


def release_signup_key(key_id: int) -> None:
    """Give a use back (the account could not be created after all)."""
    with _db() as db:
        db.execute("UPDATE signup_keys SET uses = MAX(uses - 1, 0) WHERE id = ?", (key_id,))


def list_signup_keys() -> list[dict]:
    """All keys, newest first, with an `active` flag. Never includes the key."""
    now = _now()
    with _db() as db:
        rows = db.execute(
            "SELECT id, label, max_uses, uses, expires, revoked, created FROM signup_keys ORDER BY id DESC").fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["active"] = (not d["revoked"] and (d["expires"] is None or d["expires"] > now)
                       and (d["max_uses"] == 0 or d["uses"] < d["max_uses"]))
        out.append(d)
    return out


def revoke_signup_key(key_id: int) -> bool:
    with _db() as db:
        return db.execute("UPDATE signup_keys SET revoked = 1 WHERE id = ? AND revoked = 0", (key_id,)).rowcount > 0
