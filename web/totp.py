"""RFC 6238 time-based one-time passwords and single-use recovery codes. Pure functions, no storage."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

# TOTP settings (RFC 6238)
TOTP_PERIOD = 30  # seconds
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # accept ±1 step (±30s)


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
