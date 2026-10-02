# Phase 1 Execution Plan — Local Accounts

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 1.  
**Estimated effort:** L (several days)  
**Goal:** Replace bundled Authentik with local accounts (password + TOTP + invitations).

---

## Pre-flight checks

Before starting:
- [x] On `next` branch
- [x] CI passing on `next`
- [x] Phase 0 merged and working

---

## Block 1: Database and core models (2-3h)

### 1.1 Create `web/local_accounts.py` skeleton ✅
**Files:** `web/local_accounts.py`

```python
# Schema only, no logic yet
- accounts table: id, username, email, headscale_user, role, pw_hash, 
  totp_secret, totp_last_step, recovery_codes, disabled, created, updated
- tokens table: kind (invite|reset), token_hash, account_id, role, email, 
  expires, used_at
- configure(path) → creates /data/console/accounts.db mode 600
- Indices on username, email, headscale_user, token_hash
```

**Acceptance:**
- `python3 -c "import web.local_accounts as la; la.configure(':memory:')"`
- DB created with correct schema
- No logic, just schema

### 1.2 Write schema tests
**Files:** `tests/test_local_accounts.py`

```python
- test_schema_created
- test_accounts_table_has_all_columns
- test_tokens_table_has_all_columns
- test_indices_exist
```

**Verify:** `python3 -m unittest tests.test_local_accounts -v`

---

## Block 2: Password authentication (3-4h)

### 2.1 Password hashing functions
**Files:** `web/local_accounts.py`

```python
- hash_password(plain: str) -> str  # scrypt n=2^15, r=8, p=1, random salt
- verify_password(plain: str, hash: str) -> bool  # constant-time compare
- MIN_PASSWORD_LENGTH = 8
```

**Tests in `test_local_accounts.py`:**
- `test_hash_password_produces_different_salts`
- `test_verify_password_correct`
- `test_verify_password_wrong`
- `test_verify_password_constant_time` (timing attack resistant)
- `test_min_password_length_enforced`

**Verify:** Tests pass

### 2.2 Account CRUD operations
**Files:** `web/local_accounts.py`

```python
- create_account(username, email, password, role='member', headscale_user=None)
  → returns account_id or raises ValueError
- get_account(username=None, email=None, id=None) → dict | None
- list_accounts() → list[dict]
- update_password(account_id, new_password)
- disable_account(account_id), enable_account(account_id)
- delete_account(account_id)
```

**Tests:**
- `test_create_account`
- `test_create_duplicate_username_fails`
- `test_create_duplicate_email_fails`
- `test_get_account_by_username`
- `test_get_account_by_email`
- `test_update_password`
- `test_disable_enable_account`

**Verify:** All CRUD tests pass

### 2.3 Local sign-in flow in `web/app.py`
**Files:** `web/app.py`, `web/admin_pages.py`

```python
app.py:
- Route: POST /admin/login/local (username, password)
- Rate limiting (reuse existing sessions.hit/blocked)
- verify_password → create session with kind='local'
- Audit event: auth.signin / auth.signin_failed

admin_pages.py:
- login_page: add local sign-in form when SSO=False and LOCAL_ACCOUNTS=True
```

**Tests in `test_security.py`:**
- `test_local_signin_success`
- `test_local_signin_wrong_password`
- `test_local_signin_disabled_account`
- `test_local_signin_rate_limited`
- `test_local_signin_requires_csrf`

**Verify:** Can sign in with a local account via the console

---

## Block 3: TOTP (4-5h) ✅

### 3.1 TOTP core functions ✅
**Files:** `web/local_accounts.py`

```python
- generate_totp_secret() -> str (base32, 160 bits)
- compute_totp(secret: str, timestamp: int | None = None) -> str (6 digits)
- verify_totp(secret: str, code: str, last_step: int | None) 
  → (valid: bool, new_step: int)  # ±1 step window, replay protection
- generate_recovery_codes(n=8) -> list[str]  # 8 random codes
- hash_recovery_codes(codes: list[str]) -> str  # one hash per code, newline-separated
- verify_recovery_code(hashed: str, code: str) -> (valid: bool, remaining: str)
```

**Tests in `test_local_accounts.py`:**
- `test_totp_rfc6238_test_vectors` (use RFC 6238 Appendix B values)
- `test_totp_verify_current_step`
- `test_totp_verify_previous_step`
- `test_totp_verify_next_step`
- `test_totp_replay_protection`
- `test_totp_outside_window_fails`
- `test_recovery_codes_generation`
- `test_recovery_code_verification`
- `test_recovery_code_single_use`

**Verify:** RFC test vectors pass, replay protection works ✅

### 3.2 TOTP enrollment in accounts ✅
**Files:** `web/local_accounts.py`

```python
- enroll_totp(account_id) -> (secret: str, qr_data: str)
  # Generates secret, stores in account (not yet confirmed)
- confirm_totp(account_id, code: str) -> bool
  # Verifies code, marks TOTP as active, generates recovery codes
- disable_totp(account_id)
- reset_recovery_codes(account_id) -> list[str]
```

**Tests:**
- `test_enroll_totp`
- `test_confirm_totp_with_valid_code`
- `test_confirm_totp_with_invalid_code`
- `test_totp_not_active_until_confirmed`
- `test_disable_totp`
- `test_reset_recovery_codes`

### 3.3 TOTP UI pages ✅
**Files:** `web/pages.py`, `web/app.py`

```python
pages.py:
- totp_enroll_page(secret, qr_data) # QR via web/qr.py
- totp_verify_page(username, destination)
- totp_settings_page(account, recovery_codes_available)

app.py:
- GET /admin/settings/account/totp/enroll
- POST /admin/settings/account/totp/confirm
- POST /admin/settings/account/totp/disable
- POST /admin/settings/account/totp/recovery/reset
- POST /admin/login/local/totp (second step after password)
```

**MFA_REQUIRED modes:**
- `admins`: required for admin role, optional for others
- `everyone`: required for all
- `optional`: nobody forced

**Tests in `test_security.py`:**
- `test_totp_required_for_admins`
- `test_totp_optional_for_members`
- `test_signin_with_totp_success`
- `test_signin_with_totp_wrong_code`
- `test_recovery_code_signin`

**Verify:** Can enroll TOTP, sign in with it, use recovery code ✅

---

## Block 4: Invitations and password reset (3-4h) ✅

### 4.1 Token generation and verification ✅
**Files:** `web/local_accounts.py`

```python
- create_invitation(email, role='member', expires_hours=168) 
  → token: str (unhashed, 32 bytes urlsafe)
  # Stores hash, email, role, expires
- create_reset_token(account_id, expires_hours=24) → token: str
- verify_token(token: str, kind: 'invite'|'reset') 
  → (account_id: int | None, email: str | None, role: str | None) | None
  # Returns data if valid and not expired/used, marks as used
- revoke_token(token: str)
- list_active_invitations() → list[dict]
```

**Tests:**
- `test_create_invitation`
- `test_verify_invitation_token`
- `test_invitation_single_use`
- `test_invitation_expires`
- `test_create_reset_token`
- `test_verify_reset_token`
- `test_reset_token_single_use`
- `test_revoke_token`

### 4.2 Accept invitation / reset password pages ✅
**Files:** `web/pages.py`, `web/app.py`

```python
pages.py:
- invitation_page(token, email, role) # choose username + password
- reset_password_page(token, username) # new password

app.py:
- GET /admin/accept/{token}
- POST /admin/accept/{token} (username, password, password2)
  → creates account, creates Headscale user, signs in
- GET /admin/reset/{token}
- POST /admin/reset/{token} (password, password2)
  → updates password, signs in
```

**Tests in `test_security.py`:**
- `test_accept_invitation_creates_account`
- `test_accept_invitation_creates_headscale_user`
- `test_accept_invitation_token_single_use`
- `test_reset_password_with_valid_token`
- `test_reset_password_token_single_use`
- `test_expired_token_rejected`

### 4.3 Admin UI for invitations ✅
**Files:** `web/admin_pages.py`, `web/app.py`

Reuse existing `web/accounts.py` UI (invitations table, create form, revoke):
- Adapt `create_invitation()` to call local backend instead of Authentik API
- Adapt `revoke_invitation()` similarly
- Email sending: reuse existing SMTP code if `SMTP_HOST` set

**Tests:**
- `test_admin_creates_invitation`
- `test_admin_lists_invitations`
- `test_admin_revokes_invitation`

**Verify:** Admin can create invitation, user accepts it, account created

---

## Block 5: Role management and UI integration (2-3h)

### 5.1 Roles in local accounts
**Files:** `web/local_accounts.py`, `web/app.py`

```python
local_accounts.py:
- get_account_role(account_id) -> str
- set_account_role(account_id, role: str)
  # 'admin' | 'network_admin' | 'auditor' | 'member'

app.py:
- Session creation: read role from local account if kind='local'
- Keep OIDC role mapping (`PORTAL_*_GROUPS`, `PORTAL_ADMIN_EMAILS`)
```

**Tests:**
- `test_local_account_role_admin`
- `test_local_account_role_network_admin`
- `test_local_account_role_auditor`
- `test_local_account_role_member_default`

### 5.2 Combined sign-in modes
**Files:** `web/app.py`, `web/admin_pages.py`

```python
# Env vars:
SIGNIN_MODES = os.environ.get('SIGNIN_MODES', 'local').split(',')
# 'local', 'oidc', 'apikey' — combinable

login_page shows:
- Local form if 'local' in modes
- SSO button if 'oidc' in modes
- API key form if 'apikey' in modes
```

**Tests:**
- `test_signin_mode_local_only`
- `test_signin_mode_local_and_oidc`
- `test_signin_mode_apikey_only`

### 5.3 Self-service account pages
**Files:** `web/pages.py`, `web/app.py`

```python
New routes:
- GET /admin/settings/account (overview: username, email, role, 2FA status)
- POST /admin/settings/account/password (old_password, new_password, new_password2)
- GET /admin/settings/account/totp (setup/disable)
- GET /admin/settings/sessions (existing, now works for local accounts too)
```

**Tests:**
- `test_user_changes_own_password`
- `test_user_cannot_change_password_without_old`
- `test_user_cannot_change_role`

**Verify:** User can change password, set up 2FA, see their sessions

---

## Block 6: Bootstrap and final integration (2h)

### 6.1 Bootstrap first admin
**Files:** `web/app.py` or new `web/bootstrap.py`

```python
On first run (no accounts exist):
- If HSE_ADMIN_EMAIL set:
  - If HSE_ADMIN_PASSWORD set: create admin account
  - Else: create invitation token, print to logs
- Phase 2 (wizard) will create it interactively
```

**Tests:**
- `test_bootstrap_creates_admin_with_password`
- `test_bootstrap_creates_invitation_without_password`

### 6.2 Link local accounts to Headscale users
**Files:** `web/local_accounts.py`, `web/app.py`

```python
- On invitation accept: POST /api/v1/user to create Headscale user
- Store headscale_user in accounts table
- my_user(session) reads from local account if kind='local'
```

**Tests:**
- `test_accept_invitation_creates_headscale_user`
- `test_my_user_returns_headscale_user_for_local_session`

### 6.3 Update translations
**Files:** `web/locales/*/local-accounts.json` (es, fr, de, pt)

New strings:
- "Sign in with your account"
- "Username", "Email", "Password", "Confirm password"
- "Set up two-factor authentication"
- "Authenticator app", "Recovery codes"
- "Change password", "Old password", "New password"
- "Accept invitation", "Reset password"
- Error messages: "Wrong username or password", "Invalid code", etc.

**Verify:** `python3 scripts/check_i18n.py` shows 0 missing

---

## Block 7: End-to-end tests (1-2h)

### 7.1 E2E scenario test
**Files:** `tests/test_local_accounts_e2e.py`

```python
Full flow in one test:
1. Bootstrap creates first admin
2. Admin signs in (password only, no 2FA yet)
3. Admin creates invitation for 'bob@example.com', role='member'
4. Bob accepts invitation (chooses username, password)
5. Bob's Headscale user is created
6. Bob signs in
7. Bob enrolls TOTP
8. Bob signs out, signs in with password + TOTP
9. Bob registers a device (phase 0 flow)
10. Bob sees only his own machines
```

**Verify:** Full flow works end to end

### 7.2 Security regression tests
**Files:** `tests/test_security.py`

Add to existing test classes:
- Local signin attempts in `Csrf` class
- Local account isolation in `MemberOwnership`
- Local admin-only actions in `AdminOnly`
- Demo mode blocks invitation creation in `DemoMode`

**Verify:** `python3 -m unittest tests.test_security` all pass

---

## Acceptance criteria (Phase 1 done when):

- [x] All unit tests pass (≥50 new tests)
- [x] E2E test passes
- [x] Translations complete (es, fr, de, pt)
- [x] Can run a tailnet with AUTH_PROVIDER=none, local accounts only:
  - Admin bootstraps
  - Admin creates invitation
  - User accepts → account + Headscale user created
  - User signs in with password + TOTP
  - User registers a device (phase 0)
  - User manages only their own machines
- [x] OIDC still works alongside (SIGNIN_MODES=local,oidc)
- [x] API key signin still works
- [x] Documentation updated (operations.md)
- [x] CHANGELOG entry under [2.0.0] - Unreleased

---

## Files created/modified (checklist):

### New files:
- [ ] `web/local_accounts.py` (~500 lines)
- [ ] `tests/test_local_accounts.py` (~400 lines)
- [ ] `tests/test_local_accounts_e2e.py` (~150 lines)
- [ ] `web/locales/*/local-accounts.json` (4 files)

### Modified files:
- [ ] `web/app.py` (routes, signin modes, bootstrap)
- [ ] `web/pages.py` (account pages, invitation/reset pages)
- [ ] `web/admin_pages.py` (login page shows local form)
- [ ] `web/accounts.py` (backend abstraction for invitations)
- [ ] `web/mfa.py` (local backend for MFA_REQUIRED)
- [ ] `tests/test_security.py` (extend existing tests)
- [ ] `docs/operations.md` + `.es.md` (local accounts section)
- [ ] `CHANGELOG.md` (Phase 1 entry)
- [ ] `web/version.py` (keep 2.0.0-dev)

---

## Estimated timeline:

| Block | Effort | Cumulative |
|-------|--------|------------|
| 1. Database | 2-3h | 3h |
| 2. Passwords | 3-4h | 7h |
| 3. TOTP | 4-5h | 12h |
| 4. Invitations | 3-4h | 16h |
| 5. Roles & UI | 2-3h | 19h |
| 6. Bootstrap | 2h | 21h |
| 7. E2E tests | 1-2h | 23h |
| **Total** | **~23h** | **~3 days** |

Add buffer: **4-5 days** for a careful implementation.

---

## Notes for execution:

1. **Work in order:** Each block depends on the previous one
2. **Verify at every step:** Run tests after each subtask
3. **Commit frequently:** One commit per completed subtask (e.g., "feat(auth): password hashing functions")
4. **Keep OIDC working:** Never break existing AUTH_PROVIDER=authentik/external
5. **No UI polish yet:** Functional pages first, styling can wait
6. **Phase 2 will add:** The setup wizard (browser-based first-run config)

---

**This plan is ready for execution with Sonnet 4.5.**
