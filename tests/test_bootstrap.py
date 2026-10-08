"""Tests for admin bootstrap functionality (web/app.py bootstrap_admin()).

Tests that the first admin account is created correctly on first run based on
environment variables HSE_ADMIN_EMAIL and HSE_ADMIN_PASSWORD.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Set required environment variables BEFORE importing app
os.environ.setdefault('PUBLIC_URL', 'http://localhost:8000')
os.environ.setdefault('SESSION_SECRET', 'test-secret-key-for-testing-only')
os.environ.setdefault('HEADSCALE_API_KEY', 'test-api-key')
os.environ.setdefault('HEADSCALE_URL', 'http://localhost:8080')

# Add web/ to path
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
sys.path.insert(0, WEB)

import local_accounts as la  # noqa: E402

import accounts_db


class BootstrapTests(unittest.TestCase):
    """Test bootstrap_admin() function."""

    def setUp(self):
        """Create a fresh in-memory database for each test."""
        accounts_db.configure(":memory:")
        # Clear any existing env vars
        for key in ('HSE_ADMIN_EMAIL', 'HSE_ADMIN_PASSWORD'):
            os.environ.pop(key, None)

    def tearDown(self):
        """Clean up environment variables."""
        for key in ('HSE_ADMIN_EMAIL', 'HSE_ADMIN_PASSWORD'):
            os.environ.pop(key, None)

    @patch('handlers.access.hs')
    @patch('handlers.shared.log')
    def test_bootstrap_creates_admin_with_password(self, mock_log, mock_hs):
        """bootstrap_admin() creates an admin account when email and password are set."""
        # Import here to avoid issues with mocking
        import app

        os.environ['HSE_ADMIN_EMAIL'] = 'admin@example.com'
        os.environ['HSE_ADMIN_PASSWORD'] = 'SecurePassword123!'

        # Run bootstrap
        app.bootstrap_admin()

        # Check that account was created
        account = la.get_account(email='admin@example.com')
        self.assertIsNotNone(account)
        self.assertEqual(account['username'], 'admin')
        self.assertEqual(account['email'], 'admin@example.com')
        self.assertEqual(account['role'], 'admin')
        self.assertEqual(account['headscale_user'], 'admin')

        # Verify password was set correctly
        self.assertTrue(la.verify_password('SecurePassword123!', account['pw_hash']))

        # Check that Headscale user creation was attempted
        mock_hs.create_user.assert_called_once_with('admin')

        # Check log message
        mock_log.info.assert_any_call("✓ Bootstrap: Created admin account '%s' (%s)", 'admin', 'admin@example.com')

    @patch('handlers.access.account_tokens')
    @patch('handlers.access.lac')
    @patch('handlers.shared.log')
    def test_bootstrap_creates_invitation_without_password(self, mock_log, mock_lac, mock_tokens):
        """bootstrap_admin() creates an invitation token when email is set but password is not."""
        # Import here to avoid issues with mocking
        import app

        os.environ['HSE_ADMIN_EMAIL'] = 'admin@example.com'
        # HSE_ADMIN_PASSWORD not set

        # Mock list_accounts to return empty list (no accounts)
        mock_lac.list_accounts.return_value = []
        mock_tokens.create_invitation.return_value = 'test-token-abc123'

        # Run bootstrap
        app.bootstrap_admin()

        # Check that invitation was created
        mock_tokens.create_invitation.assert_called_once_with(
            email='admin@example.com',
            role='admin',
            expires_hours=168
        )

        # Check log message includes the invitation URL
        log_calls = [str(call) for call in mock_log.info.call_args_list]
        self.assertTrue(any('Bootstrap invitation created' in str(call) for call in log_calls))
        self.assertTrue(any('test-token-abc123' in str(call) for call in log_calls))

    @patch('handlers.shared.log')
    def test_bootstrap_skips_when_accounts_exist(self, mock_log):
        """bootstrap_admin() does nothing if accounts already exist."""
        # Import here to avoid issues with mocking
        import app

        # Create an existing account
        la.create_account('existing', 'existing@example.com', 'password123', role='admin')

        os.environ['HSE_ADMIN_EMAIL'] = 'admin@example.com'
        os.environ['HSE_ADMIN_PASSWORD'] = 'SecurePassword123!'

        # Run bootstrap
        app.bootstrap_admin()

        # Should not create a new account
        accounts = la.list_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]['username'], 'existing')

    @patch('handlers.shared.log')
    def test_bootstrap_skips_when_no_email_set(self, mock_log):
        """bootstrap_admin() does nothing if HSE_ADMIN_EMAIL is not set."""
        # Import here to avoid issues with mocking
        import app

        # HSE_ADMIN_EMAIL not set

        # Run bootstrap
        app.bootstrap_admin()

        # Should not create any accounts
        accounts = la.list_accounts()
        self.assertEqual(len(accounts), 0)

        # Check log message
        mock_log.info.assert_called_once()
        self.assertIn('Set HSE_ADMIN_EMAIL', str(mock_log.info.call_args))

    @patch('handlers.access.hs')
    @patch('handlers.shared.log')
    def test_bootstrap_handles_headscale_user_creation_failure(self, mock_log, mock_hs):
        """bootstrap_admin() continues if Headscale user creation fails."""
        # Import here to avoid issues with mocking
        import app

        os.environ['HSE_ADMIN_EMAIL'] = 'admin@example.com'
        os.environ['HSE_ADMIN_PASSWORD'] = 'SecurePassword123!'

        # Mock Headscale user creation to fail
        mock_hs.create_user.side_effect = Exception("Headscale API error")

        # Run bootstrap (should not raise)
        app.bootstrap_admin()

        # Check that account was still created
        account = la.get_account(email='admin@example.com')
        self.assertIsNotNone(account)

        # Check that a warning was logged
        mock_log.warning.assert_called_once()
        self.assertIn('Failed to create Headscale user', str(mock_log.warning.call_args))

    @patch('handlers.shared.log')
    def test_bootstrap_handles_account_creation_failure(self, mock_log):
        """bootstrap_admin() handles account creation failures gracefully."""
        # Import here to avoid issues with mocking
        import app

        os.environ['HSE_ADMIN_EMAIL'] = 'admin@example.com'
        os.environ['HSE_ADMIN_PASSWORD'] = 'weak'  # Too short, will fail

        # Run bootstrap (should not raise)
        app.bootstrap_admin()

        # Check that error was logged
        mock_log.error.assert_called_once()
        self.assertIn('Failed to create bootstrap admin account', str(mock_log.error.call_args))


if __name__ == "__main__":
    unittest.main()
