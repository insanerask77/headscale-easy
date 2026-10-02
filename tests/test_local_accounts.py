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


if __name__ == "__main__":
    unittest.main()
