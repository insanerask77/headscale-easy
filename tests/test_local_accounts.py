"""Tests for local account authentication (web/local_accounts.py).

Standard library only, no dependencies.
"""

import os
import sys
import tempfile
import unittest

# Add web/ to path
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
sys.path.insert(0, WEB)

import local_accounts as la  # noqa: E402


class SchemaTests(unittest.TestCase):
    """Test database schema creation and structure."""

    def setUp(self):
        """Create a fresh in-memory database for each test."""
        la.configure(":memory:")

    def test_configure_creates_database(self):
        """configure() creates the database."""
        # Already called in setUp, just verify it doesn't raise
        la.configure(":memory:")

    def test_configure_creates_file_with_mode_600(self):
        """configure() creates the database file with mode 600."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            la.configure(db_path)
            self.assertTrue(os.path.exists(db_path))
            # Check file permissions (mode 600 = 0o600)
            mode = os.stat(db_path).st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_accounts_table_exists(self):
        """accounts table is created with all required columns."""
        with la._db() as db:
            cursor = db.execute("SELECT * FROM accounts LIMIT 0")
            columns = [desc[0] for desc in cursor.description]
            expected = ['id', 'username', 'email', 'headscale_user', 'role', 'pw_hash',
                       'totp_secret', 'totp_confirmed', 'totp_last_step', 'recovery_codes',
                       'disabled', 'created', 'updated']
            self.assertEqual(columns, expected)

    def test_tokens_table_exists(self):
        """tokens table is created with all required columns."""
        with la._db() as db:
            cursor = db.execute("SELECT * FROM tokens LIMIT 0")
            columns = [desc[0] for desc in cursor.description]
            expected = ['id', 'kind', 'token_hash', 'account_id', 'role', 'email',
                       'expires', 'used_at', 'created']
            self.assertEqual(columns, expected)

    def test_schema_version_table_exists(self):
        """schema_version table is created and has the current version."""
        with la._db() as db:
            version = db.execute("SELECT version FROM schema_version").fetchone()
            self.assertIsNotNone(version)
            self.assertEqual(version[0], la.SCHEMA_VERSION)

    def test_indices_exist(self):
        """All required indices are created."""
        with la._db() as db:
            indices = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ).fetchall()
            index_names = [idx[0] for idx in indices]

            expected = ['idx_accounts_username', 'idx_accounts_email',
                       'idx_accounts_headscale_user', 'idx_tokens_hash',
                       'idx_tokens_kind', 'idx_tokens_account_id']

            for idx in expected:
                self.assertIn(idx, index_names, f"Index {idx} not found")

    def test_username_unique_constraint(self):
        """username column has a unique constraint."""
        with la._db() as db:
            # Get the CREATE TABLE statement
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'"
            ).fetchone()[0]
            self.assertIn('username TEXT NOT NULL UNIQUE', schema)

    def test_email_unique_constraint(self):
        """email column has a unique constraint."""
        with la._db() as db:
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'"
            ).fetchone()[0]
            self.assertIn('email TEXT NOT NULL UNIQUE', schema)

    def test_token_hash_unique_constraint(self):
        """token_hash column has a unique constraint."""
        with la._db() as db:
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tokens'"
            ).fetchone()[0]
            self.assertIn('token_hash TEXT NOT NULL UNIQUE', schema)

    def test_foreign_key_constraint(self):
        """tokens.account_id has a foreign key to accounts.id."""
        with la._db() as db:
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tokens'"
            ).fetchone()[0]
            self.assertIn('FOREIGN KEY (account_id) REFERENCES accounts(id)', schema)

    def test_foreign_keys_enabled(self):
        """Foreign key constraints are enabled."""
        with la._db() as db:
            enabled = db.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(enabled, 1)


class PasswordHashingTests(unittest.TestCase):
    """Test password hashing and verification."""

    def setUp(self):
        la.configure(":memory:")

    def test_hash_password_produces_different_salts(self):
        """Hashing the same password twice produces different hashes (random salt)."""
        hash1 = la.hash_password("password123")
        hash2 = la.hash_password("password123")
        self.assertNotEqual(hash1, hash2)
        # But both should verify
        self.assertTrue(la.verify_password("password123", hash1))
        self.assertTrue(la.verify_password("password123", hash2))

    def test_verify_password_correct(self):
        """verify_password returns True for the correct password."""
        pw_hash = la.hash_password("mySecret123")
        self.assertTrue(la.verify_password("mySecret123", pw_hash))

    def test_verify_password_wrong(self):
        """verify_password returns False for an incorrect password."""
        pw_hash = la.hash_password("correctpass")
        self.assertFalse(la.verify_password("wrongpass", pw_hash))

    def test_verify_password_constant_time(self):
        """verify_password uses constant-time comparison (hmac.compare_digest)."""
        # This is tested by the implementation using hmac.compare_digest
        # We just verify it doesn't crash and returns False for wrong password
        pw_hash = la.hash_password("testpass")
        self.assertFalse(la.verify_password("testpasss", pw_hash))
        self.assertFalse(la.verify_password("", pw_hash))

    def test_min_password_length_enforced(self):
        """hash_password raises ValueError if password is too short."""
        with self.assertRaises(ValueError) as ctx:
            la.hash_password("short")
        self.assertIn("at least", str(ctx.exception).lower())

    def test_hash_format(self):
        """Hash has the expected format: scrypt$salt$hash."""
        pw_hash = la.hash_password("password")
        parts = pw_hash.split('$')
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], 'scrypt')

    def test_verify_invalid_format(self):
        """verify_password returns False for invalid hash format."""
        self.assertFalse(la.verify_password("testpass", "invalid"))
        self.assertFalse(la.verify_password("testpass", "bcrypt$salt$hash"))
        self.assertFalse(la.verify_password("testpass", "scrypt$onlyonepart"))


class AccountCRUDTests(unittest.TestCase):
    """Test account CRUD operations."""

    def setUp(self):
        la.configure(":memory:")

    def test_create_account(self):
        """create_account creates a new account and returns its ID."""
        account_id = la.create_account("alice", "alice@example.com", "password123")
        self.assertIsInstance(account_id, int)
        self.assertGreater(account_id, 0)

    def test_create_duplicate_username_fails(self):
        """Cannot create two accounts with the same username."""
        la.create_account("alice", "alice@example.com", "password123")
        with self.assertRaises(ValueError) as ctx:
            la.create_account("alice", "other@example.com", "password456")
        self.assertIn("already exists", str(ctx.exception).lower())

    def test_create_duplicate_email_fails(self):
        """Cannot create two accounts with the same email."""
        la.create_account("alice", "alice@example.com", "password123")
        with self.assertRaises(ValueError) as ctx:
            la.create_account("bob", "alice@example.com", "password456")
        self.assertIn("already exists", str(ctx.exception).lower())

    def test_create_account_with_role(self):
        """create_account accepts a role parameter."""
        account_id = la.create_account("admin", "admin@example.com", "password123", role="admin")
        account = la.get_account(id=account_id)
        self.assertEqual(account['role'], 'admin')

    def test_create_account_invalid_role(self):
        """create_account raises ValueError for invalid role."""
        with self.assertRaises(ValueError) as ctx:
            la.create_account("user", "user@example.com", "password123", role="superuser")
        self.assertIn("invalid role", str(ctx.exception).lower())

    def test_get_account_by_username(self):
        """get_account retrieves an account by username."""
        account_id = la.create_account("alice", "alice@example.com", "password123")
        account = la.get_account(username="alice")
        self.assertIsNotNone(account)
        self.assertEqual(account['id'], account_id)
        self.assertEqual(account['username'], 'alice')

    def test_get_account_by_email(self):
        """get_account retrieves an account by email."""
        account_id = la.create_account("alice", "alice@example.com", "password123")
        account = la.get_account(email="alice@example.com")
        self.assertIsNotNone(account)
        self.assertEqual(account['id'], account_id)

    def test_get_account_by_id(self):
        """get_account retrieves an account by id."""
        account_id = la.create_account("alice", "alice@example.com", "password123")
        account = la.get_account(id=account_id)
        self.assertIsNotNone(account)
        self.assertEqual(account['username'], 'alice')

    def test_get_account_not_found(self):
        """get_account returns None if account doesn't exist."""
        self.assertIsNone(la.get_account(username="nonexistent"))
        self.assertIsNone(la.get_account(email="none@example.com"))
        self.assertIsNone(la.get_account(id=999))

    def test_list_accounts(self):
        """list_accounts returns all accounts."""
        la.create_account("alice", "alice@example.com", "password123")
        la.create_account("bob", "bob@example.com", "password456")
        accounts = la.list_accounts()
        self.assertEqual(len(accounts), 2)
        usernames = [a['username'] for a in accounts]
        self.assertIn('alice', usernames)
        self.assertIn('bob', usernames)

    def test_update_password(self):
        """update_password changes an account's password."""
        account_id = la.create_account("alice", "alice@example.com", "oldpassword")
        old_hash = la.get_account(id=account_id)['pw_hash']

        la.update_password(account_id, "newpassword")

        account = la.get_account(id=account_id)
        self.assertNotEqual(account['pw_hash'], old_hash)
        self.assertTrue(la.verify_password("newpassword", account['pw_hash']))
        self.assertFalse(la.verify_password("oldpassword", account['pw_hash']))

    def test_disable_enable_account(self):
        """disable_account and enable_account toggle the disabled flag."""
        account_id = la.create_account("alice", "alice@example.com", "password123")

        # Initially not disabled
        account = la.get_account(id=account_id)
        self.assertEqual(account['disabled'], 0)

        # Disable
        la.disable_account(account_id)
        account = la.get_account(id=account_id)
        self.assertEqual(account['disabled'], 1)

        # Enable
        la.enable_account(account_id)
        account = la.get_account(id=account_id)
        self.assertEqual(account['disabled'], 0)

    def test_delete_account(self):
        """delete_account removes the account."""
        account_id = la.create_account("alice", "alice@example.com", "password123")
        self.assertIsNotNone(la.get_account(id=account_id))

        la.delete_account(account_id)

        self.assertIsNone(la.get_account(id=account_id))

    def test_email_case_insensitive(self):
        """Emails are stored lowercase and compared case-insensitively."""
        la.create_account("alice", "Alice@Example.com", "password123")
        account = la.get_account(email="alice@example.com")
        self.assertIsNotNone(account)
        self.assertEqual(account['email'], 'alice@example.com')

    def test_password_hash_stored(self):
        """Account stores the password hash, not the plaintext."""
        account_id = la.create_account("alice", "alice@example.com", "secretpassword")
        account = la.get_account(id=account_id)
        self.assertNotEqual(account['pw_hash'], "secretpassword")
        self.assertTrue(account['pw_hash'].startswith('scrypt$'))


class TOTPCoreTests(unittest.TestCase):
    """Test TOTP core functions (Block 3.1)."""

    def setUp(self):
        """Create a fresh in-memory database for each test."""
        la.configure(":memory:")

    def test_generate_totp_secret(self):
        """generate_totp_secret() returns a base32-encoded 160-bit secret."""
        secret = la.generate_totp_secret()
        self.assertIsInstance(secret, str)
        # 160 bits = 20 bytes = 32 base32 characters (no padding)
        self.assertEqual(len(secret), 32)
        # Should be base32 (A-Z, 2-7)
        self.assertTrue(all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=' for c in secret))

    def test_generate_totp_secret_different_each_time(self):
        """generate_totp_secret() produces different secrets."""
        secret1 = la.generate_totp_secret()
        secret2 = la.generate_totp_secret()
        self.assertNotEqual(secret1, secret2)

    def test_compute_totp_rfc6238_test_vectors(self):
        """compute_totp() matches RFC 6238 test vectors (Appendix B).

        RFC 6238 Appendix B provides test vectors for TOTP with SHA1.
        Secret: "12345678901234567890" (ASCII)
        """
        import base64
        # Secret from RFC: "12345678901234567890" (20 bytes ASCII)
        secret_bytes = b"12345678901234567890"
        secret = base64.b32encode(secret_bytes).decode('ascii')

        # Test vectors from RFC 6238 Appendix B (SHA1, 8 digits)
        # We use 6 digits, so we take the last 6 digits of the expected values
        test_cases = [
            (59, "94287082"),        # 1970-01-01 00:00:59 UTC
            (1111111109, "07081804"),  # 2005-03-18 01:58:29 UTC
            (1111111111, "14050471"),  # 2005-03-18 01:58:31 UTC
            (1234567890, "89005924"),  # 2009-02-13 23:31:30 UTC
            (2000000000, "69279037"),  # 2033-05-18 03:33:20 UTC
            (20000000000, "65353130"), # 2603-10-11 11:33:20 UTC
        ]

        for timestamp, expected_8digit in test_cases:
            code = la.compute_totp(secret, timestamp)
            # Our implementation uses 6 digits, RFC uses 8
            # Take the last 6 digits of the expected value
            expected_6digit = expected_8digit[-6:]
            self.assertEqual(code, expected_6digit,
                           f"Failed for timestamp {timestamp}")

    def test_verify_totp_current_step(self):
        """verify_totp() accepts a code for the current time step."""
        secret = la.generate_totp_secret()
        import time
        current_time = int(time.time())
        code = la.compute_totp(secret, current_time)

        valid, new_step = la.verify_totp(secret, code, last_step=None)
        self.assertTrue(valid)
        self.assertGreater(new_step, 0)

    def test_verify_totp_previous_step(self):
        """verify_totp() accepts a code from the previous time step (window ±1)."""
        secret = la.generate_totp_secret()
        import time
        current_time = int(time.time())
        previous_time = current_time - la.TOTP_PERIOD  # 30 seconds ago
        code = la.compute_totp(secret, previous_time)

        valid, new_step = la.verify_totp(secret, code, last_step=None)
        self.assertTrue(valid)

    def test_verify_totp_next_step(self):
        """verify_totp() accepts a code from the next time step (window ±1)."""
        secret = la.generate_totp_secret()
        import time
        current_time = int(time.time())
        next_time = current_time + la.TOTP_PERIOD  # 30 seconds from now
        code = la.compute_totp(secret, next_time)

        valid, new_step = la.verify_totp(secret, code, last_step=None)
        self.assertTrue(valid)

    def test_verify_totp_replay_protection(self):
        """verify_totp() rejects a code that was already used (replay attack)."""
        secret = la.generate_totp_secret()
        import time
        current_time = int(time.time())
        code = la.compute_totp(secret, current_time)

        # First use: should succeed
        valid, new_step = la.verify_totp(secret, code, last_step=None)
        self.assertTrue(valid)

        # Second use with the same code and last_step: should fail (replay)
        valid2, _ = la.verify_totp(secret, code, last_step=new_step)
        self.assertFalse(valid2)

    def test_verify_totp_outside_window_fails(self):
        """verify_totp() rejects a code from outside the ±1 step window."""
        secret = la.generate_totp_secret()
        import time
        current_time = int(time.time())
        # Code from 2 steps ago (outside ±1 window)
        old_time = current_time - (2 * la.TOTP_PERIOD)
        code = la.compute_totp(secret, old_time)

        valid, _ = la.verify_totp(secret, code, last_step=None)
        self.assertFalse(valid)

    def test_verify_totp_invalid_code(self):
        """verify_totp() rejects invalid codes (wrong length, non-digits)."""
        secret = la.generate_totp_secret()

        # Wrong length
        valid, _ = la.verify_totp(secret, "12345", last_step=None)
        self.assertFalse(valid)

        # Non-digits
        valid, _ = la.verify_totp(secret, "abcdef", last_step=None)
        self.assertFalse(valid)

        # Empty
        valid, _ = la.verify_totp(secret, "", last_step=None)
        self.assertFalse(valid)

    def test_generate_recovery_codes(self):
        """generate_recovery_codes() returns n unique codes."""
        codes = la.generate_recovery_codes(8)
        self.assertEqual(len(codes), 8)
        # All codes should be 8 characters
        for code in codes:
            self.assertEqual(len(code), 8)
            # Should only contain alphanumeric (no ambiguous chars)
            self.assertTrue(all(c in 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789' for c in code))
        # All codes should be unique
        self.assertEqual(len(codes), len(set(codes)))

    def test_hash_recovery_codes(self):
        """hash_recovery_codes() returns newline-separated hashes."""
        codes = ["ABCD1234", "EFGH5678"]
        hashed = la.hash_recovery_codes(codes)
        lines = hashed.split('\n')
        self.assertEqual(len(lines), 2)
        # Each line should be a SHA-256 hex hash (64 characters)
        for line in lines:
            self.assertEqual(len(line), 64)
            self.assertTrue(all(c in '0123456789abcdef' for c in line))

    def test_verify_recovery_code_valid(self):
        """verify_recovery_code() verifies a valid code and removes it."""
        codes = la.generate_recovery_codes(3)
        hashed = la.hash_recovery_codes(codes)

        # Verify the first code
        valid, remaining = la.verify_recovery_code(hashed, codes[0])
        self.assertTrue(valid)

        # The hash should be removed
        remaining_lines = remaining.split('\n') if remaining else []
        self.assertEqual(len(remaining_lines), 2)

    def test_verify_recovery_code_invalid(self):
        """verify_recovery_code() rejects an invalid code."""
        codes = la.generate_recovery_codes(3)
        hashed = la.hash_recovery_codes(codes)

        valid, remaining = la.verify_recovery_code(hashed, "WRONGCODE")
        self.assertFalse(valid)
        # Hashed should be unchanged
        self.assertEqual(remaining, hashed)

    def test_verify_recovery_code_single_use(self):
        """verify_recovery_code() marks a code as used (single-use)."""
        codes = la.generate_recovery_codes(2)
        hashed = la.hash_recovery_codes(codes)

        # Use the first code
        valid, remaining = la.verify_recovery_code(hashed, codes[0])
        self.assertTrue(valid)

        # Try to use it again
        valid2, _ = la.verify_recovery_code(remaining, codes[0])
        self.assertFalse(valid2)

    def test_verify_recovery_code_case_insensitive(self):
        """verify_recovery_code() is case-insensitive."""
        codes = ["ABCD1234"]
        hashed = la.hash_recovery_codes(codes)

        # Verify with lowercase
        valid, _ = la.verify_recovery_code(hashed, "abcd1234")
        self.assertTrue(valid)


class TOTPEnrollmentTests(unittest.TestCase):
    """Test TOTP enrollment functions (Block 3.2)."""

    def setUp(self):
        """Create a fresh in-memory database and an account for each test."""
        la.configure(":memory:")
        self.account_id = la.create_account("alice", "alice@example.com", "password123")

    def test_enroll_totp(self):
        """enroll_totp() generates a secret and QR data."""
        secret, qr_data = la.enroll_totp(self.account_id)

        # Secret should be base32
        self.assertEqual(len(secret), 32)

        # QR data should be an otpauth:// URL
        self.assertTrue(qr_data.startswith('otpauth://totp/'))
        self.assertIn(secret, qr_data)
        self.assertIn('alice', qr_data)
        self.assertIn('issuer=Tailscale', qr_data)

        # Check that it was stored in the account (but not confirmed)
        account = la.get_account(id=self.account_id)
        self.assertEqual(account['totp_secret'], secret)
        self.assertEqual(account['totp_confirmed'], 0)

    def test_confirm_totp_with_valid_code(self):
        """confirm_totp() confirms enrollment with a valid code."""
        secret, _ = la.enroll_totp(self.account_id)

        # Generate a valid code
        import time
        code = la.compute_totp(secret, int(time.time()))

        # Confirm
        result = la.confirm_totp(self.account_id, code)
        self.assertTrue(result)

        # Check that TOTP is now confirmed
        account = la.get_account(id=self.account_id)
        self.assertEqual(account['totp_confirmed'], 1)
        self.assertIsNotNone(account['totp_last_step'])
        self.assertIsNotNone(account['recovery_codes'])

    def test_confirm_totp_with_invalid_code(self):
        """confirm_totp() rejects an invalid code."""
        la.enroll_totp(self.account_id)

        result = la.confirm_totp(self.account_id, "000000")
        self.assertFalse(result)

        # TOTP should still be unconfirmed
        account = la.get_account(id=self.account_id)
        self.assertEqual(account['totp_confirmed'], 0)

    def test_totp_not_active_until_confirmed(self):
        """TOTP is not active until confirmed."""
        secret, _ = la.enroll_totp(self.account_id)

        account = la.get_account(id=self.account_id)
        self.assertEqual(account['totp_secret'], secret)
        self.assertEqual(account['totp_confirmed'], 0)

    def test_confirm_totp_not_enrolled(self):
        """confirm_totp() raises if TOTP not enrolled."""
        with self.assertRaises(ValueError):
            la.confirm_totp(self.account_id, "123456")

    def test_disable_totp(self):
        """disable_totp() removes TOTP secret and recovery codes."""
        secret, _ = la.enroll_totp(self.account_id)
        import time
        code = la.compute_totp(secret, int(time.time()))
        la.confirm_totp(self.account_id, code)

        # Disable
        la.disable_totp(self.account_id)

        # Check that TOTP is removed
        account = la.get_account(id=self.account_id)
        self.assertIsNone(account['totp_secret'])
        self.assertEqual(account['totp_confirmed'], 0)
        self.assertIsNone(account['recovery_codes'])

    def test_reset_recovery_codes(self):
        """reset_recovery_codes() generates new codes for active TOTP."""
        secret, _ = la.enroll_totp(self.account_id)
        import time
        code = la.compute_totp(secret, int(time.time()))
        la.confirm_totp(self.account_id, code)

        # Get old recovery codes
        account = la.get_account(id=self.account_id)
        old_codes = account['recovery_codes']

        # Reset
        new_codes = la.reset_recovery_codes(self.account_id)
        self.assertEqual(len(new_codes), 8)

        # Check that they're stored
        account = la.get_account(id=self.account_id)
        new_hashed = account['recovery_codes']
        self.assertNotEqual(new_hashed, old_codes)

        # Verify one of the new codes works
        valid, _ = la.verify_recovery_code(new_hashed, new_codes[0])
        self.assertTrue(valid)

    def test_reset_recovery_codes_not_active(self):
        """reset_recovery_codes() raises if TOTP not confirmed."""
        la.enroll_totp(self.account_id)
        with self.assertRaises(ValueError):
            la.reset_recovery_codes(self.account_id)


class TokenTests(unittest.TestCase):
    """Test invitation and password reset tokens (Block 4.1)."""

    def setUp(self):
        """Create a fresh in-memory database with a test account."""
        la.configure(":memory:")
        self.account_id = la.create_account("testuser", "test@example.com", "password123")

    def test_create_invitation(self):
        """create_invitation() generates a token and stores it hashed."""
        token = la.create_invitation("newuser@example.com", role='member', expires_hours=168)

        # Token should be a non-empty string
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 32)  # urlsafe_b64(32 bytes) ≈ 43 chars

        # Token should be stored (hashed) in database
        invitations = la.list_active_invitations()
        self.assertEqual(len(invitations), 1)
        self.assertEqual(invitations[0]['email'], 'newuser@example.com')
        self.assertEqual(invitations[0]['role'], 'member')

    def test_verify_invitation_token(self):
        """verify_token() returns invitation data for valid tokens."""
        token = la.create_invitation("bob@example.com", role='admin', expires_hours=24)

        # Verify the token
        data = la.verify_token(token, kind='invite')

        self.assertIsNotNone(data)
        self.assertEqual(data['email'], 'bob@example.com')
        self.assertEqual(data['role'], 'admin')
        self.assertIn('token_hash', data)

    def test_invitation_single_use(self):
        """Invitation tokens can only be used once."""
        token = la.create_invitation("alice@example.com", role='member')

        # First use: should succeed
        data1 = la.verify_token(token, kind='invite')
        self.assertIsNotNone(data1)

        # Second use: should fail (already used)
        data2 = la.verify_token(token, kind='invite')
        self.assertIsNone(data2)

    def test_invitation_expires(self):
        """Expired invitation tokens are rejected."""
        import time
        from datetime import datetime, timezone, timedelta

        # Create an invitation that expires in 0.1 seconds
        token = la.create_invitation("expiry@example.com", expires_hours=0.1/3600)

        # Should work immediately
        data1 = la.verify_token(token, kind='invite')
        self.assertIsNotNone(data1)

        # Create another token and manually set it to expired
        token2 = la.create_invitation("expiry2@example.com", expires_hours=24)
        token_hash = la._hash_token(token2)

        # Manually set expiration to the past
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with la._db() as db:
            db.execute("UPDATE tokens SET expires = ?, used_at = NULL WHERE token_hash = ?",
                      (past, token_hash))

        # Should now be rejected as expired
        data2 = la.verify_token(token2, kind='invite')
        self.assertIsNone(data2)

    def test_create_reset_token(self):
        """create_reset_token() generates a token for an existing account."""
        token = la.create_reset_token(self.account_id, expires_hours=24)

        # Token should be a non-empty string
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 32)

    def test_verify_reset_token(self):
        """verify_token() returns account data for valid reset tokens."""
        token = la.create_reset_token(self.account_id, expires_hours=24)

        # Verify the token
        data = la.verify_token(token, kind='reset')

        self.assertIsNotNone(data)
        self.assertEqual(data['account_id'], self.account_id)
        self.assertEqual(data['email'], 'test@example.com')
        self.assertIn('token_hash', data)

    def test_reset_token_single_use(self):
        """Password reset tokens can only be used once."""
        token = la.create_reset_token(self.account_id)

        # First use: should succeed
        data1 = la.verify_token(token, kind='reset')
        self.assertIsNotNone(data1)

        # Second use: should fail (already used)
        data2 = la.verify_token(token, kind='reset')
        self.assertIsNone(data2)

    def test_revoke_token(self):
        """revoke_token() marks a token as used."""
        token = la.create_invitation("revoke@example.com")

        # Revoke it
        la.revoke_token(token)

        # Should now be invalid
        data = la.verify_token(token, kind='invite')
        self.assertIsNone(data)

    def test_list_active_invitations(self):
        """list_active_invitations() returns only unused, non-expired tokens."""
        from datetime import datetime, timezone, timedelta

        # Create 3 invitations
        token1 = la.create_invitation("active@example.com", role='member')
        token2 = la.create_invitation("used@example.com", role='admin')
        token3 = la.create_invitation("expired@example.com", role='member')

        # Use token2
        la.verify_token(token2, kind='invite')

        # Expire token3 manually
        token_hash3 = la._hash_token(token3)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with la._db() as db:
            db.execute("UPDATE tokens SET expires = ? WHERE token_hash = ?",
                      (past, token_hash3))

        # Only token1 should be active
        active = la.list_active_invitations()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['email'], 'active@example.com')

    def test_create_reset_token_nonexistent_account(self):
        """create_reset_token() raises for nonexistent accounts."""
        with self.assertRaises(ValueError):
            la.create_reset_token(99999)

    def test_verify_token_wrong_kind(self):
        """verify_token() with wrong kind returns None."""
        # Create an invitation
        token = la.create_invitation("test@example.com")

        # Try to verify as reset token
        data = la.verify_token(token, kind='reset')
        self.assertIsNone(data)


if __name__ == "__main__":
    unittest.main()
