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
                   headscale_user: str | None = None) -> int:
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
                   (username, email, headscale_user, role, pw_hash, created, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (username, email, headscale_user, role, pw_hash, now, now)
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


def update_password(account_id: int, new_password: str) -> None:
    """Update an account's password."""
    pw_hash = hash_password(new_password)
    now = _now()

    with _db() as db:
        db.execute(
            "UPDATE accounts SET pw_hash = ?, updated = ? WHERE id = ?",
            (pw_hash, now, account_id)
        )


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
# TOTP functions (Block 3.1)
# -----------------------------------------------------------------------------

def generate_totp_secret() -> str:
    """Generate a random TOTP secret (160 bits, base32-encoded)."""
    import base64
    # 160 bits = 20 bytes
    random_bytes = secrets.token_bytes(20)
    # base32 encode (no padding needed for 20 bytes)
    return base64.b32encode(random_bytes).decode('ascii')


def compute_totp(secret: str, timestamp: int | None = None) -> str:
    """Compute a 6-digit TOTP code for the given secret and timestamp.

    Implements RFC 6238 (TOTP: Time-Based One-Time Password Algorithm).

    Args:
        secret: base32-encoded secret key
        timestamp: Unix timestamp (defaults to current time)

    Returns:
        6-digit TOTP code as a string
    """
    import base64
    import struct

    if timestamp is None:
        timestamp = int(time.time())

    # Step 1: Compute time step (T = floor(Unix_time / X))
    step = timestamp // TOTP_PERIOD

    # Step 2: Decode secret from base32
    try:
        key = base64.b32decode(secret, casefold=True)
    except Exception:
        raise ValueError("Invalid base32 secret")

    # Step 3: Compute HOTP (HMAC-based One-Time Password)
    # Counter is the time step, encoded as 8-byte big-endian integer
    counter_bytes = struct.pack('>Q', step)

    # HMAC-SHA1
    hmac_digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()

    # Step 4: Dynamic truncation (extract 4 bytes)
    offset = hmac_digest[-1] & 0x0f
    code_bytes = hmac_digest[offset:offset + 4]
    code_int = struct.unpack('>I', code_bytes)[0]

    # Step 5: Strip the most significant bit and compute modulo 10^6
    code_int &= 0x7fffffff
    code = code_int % (10 ** TOTP_DIGITS)

    # Step 6: Return as zero-padded string
    return str(code).zfill(TOTP_DIGITS)


def verify_totp(secret: str, code: str, last_step: int | None = None) -> tuple[bool, int]:
    """Verify a TOTP code with ±1 step window and replay protection.

    Args:
        secret: base32-encoded secret key
        code: 6-digit code to verify
        last_step: the last time step that was used (for replay protection)

    Returns:
        (valid, new_step) where:
            - valid: True if code is correct and not replayed
            - new_step: the step that was used (save this to prevent replay)
    """
    if not code or not code.isdigit() or len(code) != TOTP_DIGITS:
        return (False, last_step or 0)

    current_time = int(time.time())
    current_step = current_time // TOTP_PERIOD

    # Try current step and ±TOTP_WINDOW steps
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        test_step = current_step + offset

        # Replay protection: don't accept a step we've already used
        if last_step is not None and test_step <= last_step:
            continue

        # Compute TOTP for this step
        test_timestamp = test_step * TOTP_PERIOD
        expected_code = compute_totp(secret, test_timestamp)

        # Constant-time comparison
        if hmac.compare_digest(code, expected_code):
            return (True, test_step)

    return (False, last_step or current_step)


def generate_recovery_codes(n: int = 8) -> list[str]:
    """Generate n random recovery codes (8 characters each, alphanumeric)."""
    codes = []
    # Use alphanumeric characters (uppercase, easy to read)
    # Avoid ambiguous characters: 0, O, 1, I, l
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

    for _ in range(n):
        # 8 characters from the alphabet
        code = ''.join(secrets.choice(alphabet) for _ in range(8))
        codes.append(code)

    return codes


def hash_recovery_codes(codes: list[str]) -> str:
    """Hash a list of recovery codes. Returns newline-separated hashes."""
    hashes = []
    for code in codes:
        # SHA-256 hash of each code
        code_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()
        hashes.append(code_hash)

    return '\n'.join(hashes)


def verify_recovery_code(hashed: str, code: str) -> tuple[bool, str]:
    """Verify a recovery code and remove it from the list (single-use).

    Args:
        hashed: newline-separated hashes of remaining codes
        code: code to verify

    Returns:
        (valid, remaining) where:
            - valid: True if code matched one of the hashes
            - remaining: newline-separated hashes with the used code removed
    """
    if not hashed or not code:
        return (False, hashed)

    code_hash = hashlib.sha256(code.upper().encode('utf-8')).hexdigest()
    hashes = hashed.split('\n')

    # Check if the code hash exists
    if code_hash in hashes:
        # Remove the used hash
        hashes.remove(code_hash)
        remaining = '\n'.join(hashes)
        return (True, remaining)

    return (False, hashed)


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
    secret = generate_totp_secret()

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
        f"&digits={TOTP_DIGITS}"
        f"&period={TOTP_PERIOD}"
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
    valid, new_step = verify_totp(secret, code, last_step=None)

    if not valid:
        return False

    # Mark as confirmed and generate recovery codes
    recovery_codes = generate_recovery_codes(8)
    recovery_hashed = hash_recovery_codes(recovery_codes)

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
    recovery_codes = generate_recovery_codes(8)
    recovery_hashed = hash_recovery_codes(recovery_codes)

    now = _now()
    with _db() as db:
        db.execute(
            "UPDATE accounts SET recovery_codes = ?, updated = ? WHERE id = ?",
            (recovery_hashed, now, account_id)
        )

    log.info(f"Recovery codes reset for account {account_id} ({account['username']})")
    return recovery_codes


# -----------------------------------------------------------------------------
# Token functions (Block 4.1)
# -----------------------------------------------------------------------------

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
