# Testing Local Accounts (Phase 1)

Quick guide to test the new local accounts authentication system in development.

## Prerequisites

- Docker and Docker Compose installed
- A running Headscale Easy stack (or follow the setup below)

## Quick Setup for Local Development

### 1. Create a minimal `.env` for local accounts testing

```bash
# Copy the example and edit it
cp .env.example .env.local-test
```

Edit `.env.local-test` with these essential settings:

```bash
# Basic configuration
UI_LANG=en
DOMAIN=vpn.127.0.0.1.nip.io
URL_SCHEME=http
SERVER_URL=http://vpn.127.0.0.1.nip.io
HEADSCALE_PUBLIC_URL=http://vpn.127.0.0.1.nip.io

# NO SSL for local testing
SSL_MODE=none
HTTP_PORT=80

# Tailnet
TAILNET_NAME=devnet
ADMIN_USER=admin
IP_PREFIXES_V4=100.64.0.0/10
IP_PREFIXES_V6=fd7a:115c:a1e0::/48
LOG_LEVEL=debug
NETWORK_ISOLATION=false
NODE_KEY_EXPIRY=180d

# Database
HEADSCALE_DB_TYPE=sqlite

# === LOCAL ACCOUNTS MODE ===
# No Authentik, no external OIDC
AUTH_PROVIDER=none
ENABLE_OIDC=false
COMPOSE_PROFILES=

# Web UI with local accounts
PORTAL_SESSION_SECRET=$(openssl rand -hex 32)
PORTAL_API_KEY_LOGIN=true  # Keep API key as emergency access
DEMO_MODE=false

# Optional: Bootstrap first admin account
HSE_ADMIN_EMAIL=admin@example.com
HSE_ADMIN_PASSWORD=changeme123  # Will create admin account on first run

# Other settings
TZ=UTC
NETWORK_NAME=headscale-net
HSE_VERSION=edge
HEADSCALE_IMAGE_TAG=latest
CADDY_IMAGE_TAG=2-alpine

# You'll need to set this after first run (or manually)
HEADSCALE_API_KEY=
```

### 2. Build and start the stack

```bash
# Use the local test config
cp .env.local-test .env

# Build the web container with latest changes
docker compose build web

# Start the stack (without Authentik)
docker compose up -d

# Watch the logs
docker compose logs -f web
```

### 3. Access the console

Open your browser to: **http://vpn.127.0.0.1.nip.io/admin**

You should see the login page with:
- **Local sign-in form** (username + password)
- **API key sign-in form** (if PORTAL_API_KEY_LOGIN=true)

### 4. Bootstrap the first admin (two methods)

#### Method A: Environment variables (recommended for dev)

If you set `HSE_ADMIN_EMAIL` and `HSE_ADMIN_PASSWORD` in `.env`, the first admin account is created automatically on startup.

Sign in with:
- Username: `admin` (or the part before @ in HSE_ADMIN_EMAIL)
- Password: `changeme123` (or whatever you set)

#### Method B: Using Headscale API key (emergency access)

If no accounts exist yet:

```bash
# Create an API key
docker exec headscale headscale apikeys create

# Copy the key (starts with "hskey-api-...")
# Sign in at http://vpn.127.0.0.1.nip.io/admin with the API key
```

Once signed in as admin via API key, you can create the first local account from the UI.

## Testing the Features

### 5.1 Role Management
```bash
# Create accounts with different roles via the UI or API
# Test that each role has correct permissions
```

### 5.2 Sign-in Modes
Test the login page shows the correct options:

- **Local only**: `.env` has `AUTH_PROVIDER=none`, `PORTAL_API_KEY_LOGIN=false`
  - Should show: username/password form only
  
- **Local + API key**: `.env` has `AUTH_PROVIDER=none`, `PORTAL_API_KEY_LOGIN=true`
  - Should show: username/password form + API key form + separator

- **Local + OIDC**: `.env` has OIDC configured + local accounts
  - Should show: username/password + SSO button + separator

### 5.3 Self-Service Account Pages

1. Sign in with a local account
2. Go to **Settings → Account**
3. Test:
   - View account info (username, email, role)
   - Change password (requires old password)
   - Enable 2FA (TOTP)
   - View/reset recovery codes

### Testing Password Changes
```bash
# Sign in as a user
# Navigate to /admin/settings/account
# Fill the change password form:
# - Old password: (current password)
# - New password: (at least 8 chars)
# - Confirm new password: (must match)
# Click "Change password"
# Sign out and sign in with new password
```

### Testing TOTP (2FA)
```bash
# As a user: go to /admin/settings/account
# Click "Enable 2FA"
# Scan QR code with Google Authenticator / Authy
# Enter 6-digit code to confirm
# Sign out
# Sign in again → you'll be asked for TOTP code
# Try using a recovery code instead
```

## Rebuilding After Code Changes

```bash
# After editing files in web/
docker compose up -d --build web

# Watch logs
docker compose logs -f web

# Or rebuild and restart in one command
docker compose build web && docker compose up -d web && docker compose logs -f web
```

## Running Tests

```bash
# Run all local accounts tests
python3 -m unittest tests.test_local_accounts -v

# Run specific test classes
python3 -m unittest tests.test_local_accounts.RoleTests -v
python3 -m unittest tests.test_local_accounts.TOTPTests -v
python3 -m unittest tests.test_local_accounts.TokenTests -v

# Run security tests
python3 -m unittest tests.test_security.LocalAccountSignin -v
python3 -m unittest tests.test_security.SelfServiceAccount -v
python3 -m unittest tests.test_security.SignInModes -v

# Run ALL tests
make test
```

## Debugging

### Check if local accounts DB exists
```bash
docker exec -it headscale-easy ls -la /data/console/
# Should see accounts.db (mode 600)
```

### View accounts in the database
```bash
docker exec -it headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la
la.configure('/data/console/accounts.db')
accounts = la.list_accounts()
for acc in accounts:
    print(f\"{acc['username']} ({acc['email']}) - {acc['role']} - TOTP: {bool(acc.get('totp_confirmed'))}\")
"
```

### Manually create a test account
```bash
docker exec -it headscale-easy python3 -c "
import sys
sys.path.insert(0, '/app')
import local_accounts as la
la.configure('/data/console/accounts.db')
account_id = la.create_account('testuser', 'test@example.com', 'password123', role='member')
print(f'Created account ID: {account_id}')
"
```

### Reset everything and start fresh
```bash
# Stop and remove containers + volumes
docker compose down -v

# Remove .env and data
rm -rf data/

# Start setup again from step 1
```

## Common Issues

### "Account not found" when trying to sign in
- Check if the account was created: use the debugging commands above
- Verify `HSE_ADMIN_EMAIL` and `HSE_ADMIN_PASSWORD` are set in `.env`
- Check web container logs: `docker compose logs web`

### "Wrong username or password"
- Password must be at least 8 characters
- Username is case-sensitive
- Check if account is disabled: `disabled=1` in database

### TOTP code not working
- Ensure device clock is synchronized (TOTP depends on time)
- Try the previous or next code (±30 second window)
- Use a recovery code instead

### Can't access /admin/settings/account
- This page is only for local accounts (kind='local')
- OIDC users will be redirected to /admin/settings/general

## Environment Variables Reference (Local Accounts)

```bash
# Bootstrap first admin
HSE_ADMIN_EMAIL=admin@example.com     # Admin email
HSE_ADMIN_PASSWORD=changeme123        # Admin password (or leave empty to generate invitation)

# MFA (Two-Factor Auth) requirement
MFA_REQUIRED=admins                   # admins | everyone | optional

# Sign-in methods
AUTH_PROVIDER=none                    # Use local accounts instead of Authentik/OIDC
PORTAL_API_KEY_LOGIN=true             # Also allow API key sign-in

# Session
PORTAL_SESSION_SECRET=<random-hex>    # openssl rand -hex 32

# SMTP (optional, for password reset emails)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=yourname@gmail.com
SMTP_PASSWORD=yourapppassword
SMTP_USE_TLS=true
SMTP_FROM="Headscale Easy <vpn@example.com>"
```

## What's Working (Phase 1 - Blocks 1-5)

✅ Block 1: Database schema and core models  
✅ Block 2: Password authentication (scrypt)  
✅ Block 3: TOTP (RFC 6238) with recovery codes  
✅ Block 4: Invitations and password reset tokens  
✅ Block 5: Role management and self-service UI

**77 unit tests** passing in `test_local_accounts.py`  
**18 security tests** passing for local accounts

## What's Next (Phase 1 - Blocks 6-7)

⏳ Block 6: Bootstrap and final integration  
⏳ Block 7: End-to-end tests  

See `PHASE1_EXECUTION_PLAN.md` for the full roadmap.
