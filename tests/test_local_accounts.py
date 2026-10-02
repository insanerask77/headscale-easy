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


if __name__ == "__main__":
    unittest.main()
