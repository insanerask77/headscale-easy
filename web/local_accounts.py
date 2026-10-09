"""Local accounts: create, look up and update accounts, passwords (scrypt), TOTP enrollment, roles.

Storage is in accounts_db, the TOTP maths in totp, invitation/reset tokens and sign-up keys in
account_tokens.

Standard library only, no dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3

import totp
from accounts_db import _db, _now, _row_to_dict

log = logging.getLogger(__name__)

# Password requirements
MIN_PASSWORD_LENGTH = 8


def hash_password(plain: str) -> str:
    """Hash a password with scrypt. Returns the hash in a format that includes
    the salt and parameters: scrypt$salt$hash (both base64)."""
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    # Generate a random 32-byte salt
    salt = secrets.token_bytes(32)

    # scrypt parameters: n=2^14 (16384), r=8, p=1, dklen=32
    # n=2^14 is secure and compatible with OpenSSL default memory limits
    hash_bytes = hashlib.scrypt(
        plain.encode('utf-8'),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32
    )

    # Format: scrypt$base64(salt)$base64(hash)
    import base64
    salt_b64 = base64.b64encode(salt).decode('ascii')
    hash_b64 = base64.b64encode(hash_bytes).decode('ascii')
    return f"scrypt${salt_b64}${hash_b64}"


def verify_password(plain: str, hash_str: str) -> bool:
    """Verify a password against a stored hash. Constant-time comparison."""
    try:
        # Parse the stored hash
        parts = hash_str.split('$')
        if len(parts) != 3 or parts[0] != 'scrypt':
            return False

        import base64
        salt = base64.b64decode(parts[1])
        stored_hash = base64.b64decode(parts[2])

        # Hash the provided password with the same salt
        computed_hash = hashlib.scrypt(
            plain.encode('utf-8'),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32
        )

        # Constant-time comparison
        return hmac.compare_digest(computed_hash, stored_hash)
    except (ValueError, TypeError):
        return False


def create_account(username: str, email: str, password: str, role: str = 'member',
                   headscale_user: str | None = None, must_change: bool = False) -> int:
    """Create a new account. Returns the account ID.

    Raises:
        ValueError: if username/email already exists or password is too short
    """
    # Validate inputs
    username = username.strip()
    email = email.strip().lower()

    if not username or not email:
        raise ValueError("Username and email are required")

    if role not in ('admin', 'network_admin', 'auditor', 'member'):
        raise ValueError(f"Invalid role: {role}")

    # Hash the password (this validates length)
    pw_hash = hash_password(password)

    now = _now()

    with _db() as db:
        try:
            cursor = db.execute(
                """INSERT INTO accounts
                   (username, email, headscale_user, role, pw_hash, must_change, created, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (username, email, headscale_user, role, pw_hash, 1 if must_change else 0, now, now)
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError as e:
            if 'username' in str(e).lower():
                raise ValueError(f"Username '{username}' already exists")
            elif 'email' in str(e).lower():
                raise ValueError(f"Email '{email}' already exists")
            raise


def get_account(username: str | None = None, email: str | None = None,
                id: int | None = None) -> dict | None:
    """Get an account by username, email, or id. Returns None if not found."""
    if not any([username, email, id]):
        raise ValueError("Must provide username, email, or id")

    with _db() as db:
        if id is not None:
            row = db.execute("SELECT * FROM accounts WHERE id = ?", (id,)).fetchone()
        elif username is not None:
            row = db.execute("SELECT * FROM accounts WHERE username = ?", (username.strip(),)).fetchone()
        else:  # email
            row = db.execute("SELECT * FROM accounts WHERE email = ?", (email.strip().lower(),)).fetchone()

        return _row_to_dict(row)


def list_accounts() -> list[dict]:
    """List all accounts."""
    with _db() as db:
        rows = db.execute("SELECT * FROM accounts ORDER BY created DESC").fetchall()
        return [dict(row) for row in rows]


def update_password(account_id: int, new_password: str, must_change: bool = False) -> None:
    """Update an account's password. must_change=True makes the person choose
    another one at their next sign-in (a temporary password set by an admin)."""
    pw_hash = hash_password(new_password)
    now = _now()

    with _db() as db:
        db.execute(
            "UPDATE accounts SET pw_hash = ?, must_change = ?, updated = ? WHERE id = ?",
            (pw_hash, 1 if must_change else 0, now, account_id)
        )


def get_account_by_headscale_user(name: str) -> dict | None:
    """The account linked to a Headscale user name, or None."""
    with _db() as db:
        row = db.execute("SELECT * FROM accounts WHERE headscale_user = ?", (name,)).fetchone()
        return _row_to_dict(row)


def disable_account(account_id: int) -> None:
    """Disable an account (cannot sign in)."""
    now = _now()
    with _db() as db:
        db.execute(
            "UPDATE accounts SET disabled = 1, updated = ? WHERE id = ?",
            (now, account_id)
        )


def enable_account(account_id: int) -> None:
    """Enable a previously disabled account."""
    now = _now()
    with _db() as db:
        db.execute(
            "UPDATE accounts SET disabled = 0, updated = ? WHERE id = ?",
            (now, account_id)
        )


def delete_account(account_id: int) -> None:
    """Delete an account and all its tokens (cascade)."""
    with _db() as db:
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


# -----------------------------------------------------------------------------
# TOTP enrollment (Block 3.2)
# -----------------------------------------------------------------------------

def enroll_totp(account_id: int) -> tuple[str, str]:
    """Start TOTP enrollment for an account. Generates a secret and QR data.

    The secret is stored in the account but not yet confirmed (totp_confirmed=0).
    User must call confirm_totp() with a valid code to activate TOTP.

    Returns:
        (secret, qr_data) where:
            - secret: base32-encoded secret (for manual entry)
            - qr_data: otpauth:// URL for QR code generation
    """
    account = get_account(id=account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    # Generate a new secret
    secret = totp.generate_totp_secret()

    # Store it in the account (not yet confirmed)
    now = _now()
    with _db() as db:
        db.execute(
            """UPDATE accounts
               SET totp_secret = ?, totp_confirmed = 0, totp_last_step = NULL, updated = ?
               WHERE id = ?""",
            (secret, now, account_id)
        )

    # Generate otpauth:// URL for QR code
    # Format: otpauth://totp/Issuer:username?secret=SECRET&issuer=Issuer
    import urllib.parse
    issuer = "Tailscale Console"
    username = account['username']
    label = f"{issuer}:{username}"

    qr_data = (
        f"otpauth://totp/{urllib.parse.quote(label)}"
        f"?secret={secret}"
        f"&issuer={urllib.parse.quote(issuer)}"
        f"&digits={totp.TOTP_DIGITS}"
        f"&period={totp.TOTP_PERIOD}"
    )

    return (secret, qr_data)


def confirm_totp(account_id: int, code: str) -> bool:
    """Confirm TOTP enrollment by verifying a code.

    If the code is valid, marks TOTP as confirmed and generates recovery codes.

    Returns:
        True if code was valid and TOTP is now active
    """
    account = get_account(id=account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    secret = account.get('totp_secret')
    if not secret:
        raise ValueError("TOTP not enrolled for this account")

    # Verify the code (no replay protection needed during enrollment)
    valid, new_step = totp.verify_totp(secret, code, last_step=None)

    if not valid:
        return False

    # Mark as confirmed and generate recovery codes
    recovery_codes = totp.generate_recovery_codes(8)
    recovery_hashed = totp.hash_recovery_codes(recovery_codes)

    now = _now()
    with _db() as db:
        db.execute(
            """UPDATE accounts
               SET totp_confirmed = 1, totp_last_step = ?, recovery_codes = ?, updated = ?
               WHERE id = ?""",
            (new_step, recovery_hashed, now, account_id)
        )

    log.info(f"TOTP confirmed for account {account_id} ({account['username']})")
    return True


def disable_totp(account_id: int) -> None:
    """Disable TOTP for an account. Removes secret, confirmation, and recovery codes."""
    now = _now()
    with _db() as db:
        db.execute(
            """UPDATE accounts
               SET totp_secret = NULL, totp_confirmed = 0, totp_last_step = NULL,
                   recovery_codes = NULL, updated = ?
               WHERE id = ?""",
            (now, account_id)
        )
        log.info(f"TOTP disabled for account {account_id}")


def reset_recovery_codes(account_id: int) -> list[str]:
    """Generate new recovery codes for an account with active TOTP.

    Returns:
        List of new recovery codes (plaintext, for display to user)
    """
    account = get_account(id=account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    if not account.get('totp_confirmed'):
        raise ValueError("TOTP not active for this account")

    # Generate new codes
    recovery_codes = totp.generate_recovery_codes(8)
    recovery_hashed = totp.hash_recovery_codes(recovery_codes)

    now = _now()
    with _db() as db:
        db.execute(
            "UPDATE accounts SET recovery_codes = ?, updated = ? WHERE id = ?",
            (recovery_hashed, now, account_id)
        )

    log.info(f"Recovery codes reset for account {account_id} ({account['username']})")
    return recovery_codes


# -----------------------------------------------------------------------------
# Role functions (Block 5.1)
# -----------------------------------------------------------------------------

def get_account_role(account_id: int) -> str:
    """Get the role of an account.

    Returns:
        Role string: 'admin', 'network_admin', 'auditor', or 'member'

    Raises:
        ValueError: if account not found
    """
    account = get_account(id=account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    return account['role']


def set_account_role(account_id: int, role: str) -> None:
    """Set the role of an account.

    Args:
        account_id: Account ID
        role: One of 'admin', 'network_admin', 'auditor', 'member'

    Raises:
        ValueError: if account not found or invalid role
    """
    if role not in ('admin', 'network_admin', 'auditor', 'member'):
        raise ValueError(f"Invalid role: {role}")

    account = get_account(id=account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")

    now = _now()
    with _db() as db:
        db.execute(
            "UPDATE accounts SET role = ?, updated = ? WHERE id = ?",
            (role, now, account_id)
        )

    log.info(f"Role changed for account {account_id} ({account['username']}): {account['role']} -> {role}")
