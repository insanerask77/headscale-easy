"""Sign-in, sessions, accounts and two-factor handlers (mixin of app.Handler)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
import urllib.error
import urllib.parse

import admin_pages
import audit
import headscale as hs
import local_accounts as lac
import account_tokens
import accounts_db
import totp
import pages
import sessions
from handlers import shared as sh
from i18n import _
from ui import BASE


class AccessHandlers:
    # --- sign in ---
    def start_sso(self):
        wait = self.rate_limited("sso")
        if wait:
            return self.too_many(wait)
        sessions.hit(f"sso:{audit.client_ip(self)}")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        url = sh.discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": sh.OIDC_CLIENT_ID,
            "redirect_uri": sh.REDIRECT_URI,
            "scope": sh.OIDC_SCOPE,
            "state": state,
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        tx = sh.sign({"state": state, "verifier": verifier, "exp": time.time() + 600})
        self.redirect(url, [self.set_cookie("hse_oidc", tx, 600)])

    def callback(self, params: dict):
        tx = sh.unsign(self.cookie("hse_oidc"))
        wait = self.rate_limited("sso")
        if wait:
            return self.too_many(wait)
        if not tx or not params.get("code") or not hmac.compare_digest(params.get("state", ""), tx["state"]):
            sessions.hit(f"sso:{audit.client_ip(self)}")
            audit.request_event(self, None, "auth.signin_failed", "", {"method": "oidc", "reason": params.get("error", "invalid state")[:100]})
            return self.fail(400, _("Could not sign in"), _("The sign-in expired or is not valid. Please try again."))

        d = sh.discovery()
        basic = base64.b64encode(f"{sh.OIDC_CLIENT_ID}:{sh.OIDC_CLIENT_SECRET}".encode()).decode()
        token = hs.http_json("POST", d["token_endpoint"], headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, body=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": sh.REDIRECT_URI,
            "code_verifier": tx["verifier"],
        }).encode())
        # Identity comes from userinfo, requested directly from the provider
        # with the access token: no need to verify the ID token signature.
        info = hs.http_json("GET", d["userinfo_endpoint"], headers={"Authorization": f"Bearer {token['access_token']}"})
        groups = sorted(info.get("groups") or [])
        email = (info.get("email") or "").lower()
        # PORTAL_ADMIN_EMAILS (your own OIDC provider): never trust an email
        # the provider says it has not verified, or anyone who can set that
        # address on their account would become an admin
        verified = info.get("email_verified") is not False
        if not verified and email in sh.ADMIN_EMAILS:
            sh.log.warning("%s is in PORTAL_ADMIN_EMAILS but the provider says the email is not verified: "
                        "not made an admin by email", email)
        role = sh.role_of(groups, email, verified)
        self.start_session({
            "kind": "oidc",
            "sub": info["sub"],
            "username": info.get("preferred_username", ""),
            "name": info.get("name", ""),
            "email": info.get("email", ""),
            "groups": groups,
            "admin": role == "admin",
            "role": role,
            "idt": token.get("id_token", ""),
        })

    def apikey_login(self, form: dict):
        wait = self.rate_limited("apikey")
        if wait:
            return self.too_many(wait, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Too many sign-in attempts. Try again in a few minutes.")))
        key = str(form.get("api_key", "")).strip()
        valid = False
        if key.startswith("hskey-api-") and len(key) < 200:
            try:
                hs.http_json("GET", f"{hs.HEADSCALE_URL}/api/v1/apikey",
                             headers={"Authorization": f"Bearer {key}"}, timeout=5)
                valid = True
            except (urllib.error.URLError, OSError):
                valid = False
        if not valid:
            sessions.hit(f"apikey:{audit.client_ip(self)}")
            time.sleep(1)  # slow down guessing
            sh.log.warning("API key sign-in rejected from %s", self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "", {"method": "apikey", "prefix": hs.api_key_prefix(key)}, actor="")
            return self.send(401, admin_pages.login_page(sh.SSO, sh.API_KEY_LOGIN, _("Invalid or expired API key.")))
        sessions.reset(f"apikey:{audit.client_ip(self)}")
        sh.log.info("sign-in with API key (%s…)", key[:14])
        self.start_session({"kind": "apikey", "sub": "", "username": "", "name": _("Administrator"),
                            "email": "", "groups": [], "admin": True, "role": "admin", "key": hs.api_key_prefix(key)})

    def local_login(self, form: dict):
        """Sign in with a local account (username + password)."""
        wait = self.rate_limited("local")
        if wait:
            return self.too_many(wait, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Too many sign-in attempts. Try again in a few minutes.")))

        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))

        if not username or not password:
            sessions.hit(f"local:{audit.client_ip(self)}")
            return self.send(401, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Username and password are required.")))

        # Get account
        account = lac.get_account(username=username)
        if not account:
            sessions.hit(f"local:{audit.client_ip(self)}")
            time.sleep(1)  # slow down enumeration
            sh.log.warning("Local sign-in rejected: unknown user '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local", "username": username, "reason": "unknown_user"}, actor="")
            return self.send(401, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Wrong username or password.")))

        # Verify password
        if not lac.verify_password(password, account['pw_hash']):
            sessions.hit(f"local:{audit.client_ip(self)}")
            time.sleep(1)  # slow down guessing
            sh.log.warning("Local sign-in rejected: wrong password for '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local", "username": username, "reason": "wrong_password"}, actor="")
            return self.send(401, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Wrong username or password.")))

        # Check if account is disabled
        if account['disabled']:
            sessions.hit(f"local:{audit.client_ip(self)}")
            sh.log.warning("Local sign-in rejected: disabled account '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local", "username": username, "reason": "disabled"}, actor="")
            return self.send(403, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("This account is disabled.")))

        # Check if TOTP is required
        totp_required = self.totp_required_for(account)
        if totp_required and account.get('totp_confirmed'):
            # TOTP is enabled and confirmed: redirect to second step
            sessions.reset(f"local:{audit.client_ip(self)}")
            # Create a temporary "pending TOTP" token (expires in 5 minutes)
            pending = {
                "account_id": account['id'],
                "username": username,
                "exp": time.time() + 300,  # 5 minutes
            }
            return self.redirect(f"{BASE}/login/totp", [
                self.set_cookie("hse_totp_pending", sh.sign(pending), 300)
            ])
        elif totp_required and not account.get('totp_confirmed'):
            # TOTP is required but not enrolled: force enrollment after login
            sessions.reset(f"local:{audit.client_ip(self)}")
            self.start_session({
                "kind": "local",
                "sub": f"local:{account['id']}",
                "username": username,
                "name": account.get('email', username),
                "email": account.get('email', ''),
                "groups": [],
                "admin": account['role'] == 'admin',
                "role": account['role'],
                "totp_enrollment_required": True,
            })
            return  # start_session redirects

        # Success (password-only login)
        sessions.reset(f"local:{audit.client_ip(self)}")
        sh.log.info("Local sign-in: %s", username)

        # Create session
        self.start_session({
            "kind": "local",
            "sub": f"local:{account['id']}",
            "username": username,
            "name": account.get('email', username),
            "email": account.get('email', ''),
            "groups": [],
            "admin": account['role'] == 'admin',
            "role": account['role'],
        })

    def totp_required_for(self, account: dict) -> bool:
        """Check if TOTP is required for this account based on MFA_REQUIRED setting."""
        if sh.MFA_REQUIRED == "everyone":
            return True
        elif sh.MFA_REQUIRED == "admins":
            return account['role'] == 'admin'
        else:  # optional
            return False

    def totp_verify_page(self):
        """Show the TOTP verification page (second step after password)."""
        pending = sh.unsign(self.cookie("hse_totp_pending"))
        if not pending:
            return self.redirect(f"{BASE}/login")

        username = pending.get("username", "")
        return self.send(200, pages.totp_verify_page(username, error=None))

    def verify_totp_login(self, form: dict):
        """Verify TOTP code during login (second step)."""
        pending = sh.unsign(self.cookie("hse_totp_pending"))
        if not pending:
            return self.redirect(f"{BASE}/login")

        account_id = pending.get("account_id")
        username = pending.get("username", "")

        account = lac.get_account(id=account_id)
        if not account:
            return self.redirect(f"{BASE}/login")

        code = str(form.get("code", "")).strip()
        use_recovery = form.get("use_recovery") == "1"

        if not code:
            return self.send(401, pages.totp_verify_page(username, error=_("Code is required.")))

        valid = False
        if use_recovery:
            # Verify recovery code
            recovery_codes = account.get('recovery_codes', '')
            valid, remaining = totp.verify_recovery_code(recovery_codes, code)
            if valid:
                # Update the account with remaining codes
                with accounts_db._db() as db:
                    db.execute("UPDATE accounts SET recovery_codes = ?, updated = ? WHERE id = ?",
                             (remaining, accounts_db._now(), account_id))
                sh.log.info("Recovery code used for account %s (%s)", account_id, username)
        else:
            # Verify TOTP code
            secret = account.get('totp_secret', '')
            last_step = account.get('totp_last_step')
            valid, new_step = totp.verify_totp(secret, code, last_step)
            if valid:
                # Update last_step to prevent replay
                with accounts_db._db() as db:
                    db.execute("UPDATE accounts SET totp_last_step = ?, updated = ? WHERE id = ?",
                             (new_step, accounts_db._now(), account_id))

        if not valid:
            sessions.hit(f"totp:{audit.client_ip(self)}")
            sh.log.warning("TOTP verification failed for '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local+totp", "username": username, "reason": "wrong_totp"}, actor="")
            return self.send(401, pages.totp_verify_page(username, error=_("Invalid code. Try again.")))

        # Success
        sessions.reset(f"totp:{audit.client_ip(self)}")
        sh.log.info("TOTP verified for: %s", username)

        # Create session
        self.start_session({
            "kind": "local",
            "sub": f"local:{account['id']}",
            "username": username,
            "name": account.get('email', username),
            "email": account.get('email', ''),
            "groups": [],
            "admin": account['role'] == 'admin',
            "role": account['role'],
        })
        # Clear the pending cookie
        return  # start_session handles redirect

    def accept_invitation_page(self, token: str):
        """Show the invitation acceptance page (GET /accept/{token})."""
        # Check the token (don't consume it yet)
        data = account_tokens.check_token(token, kind='invite')
        if not data:
            return self.send(400, pages.invitation_page(token, "", "", _("This invitation link is invalid or has expired.")))

        return self.send(200, pages.invitation_page(token, data['email'], data['role'], ""))

    def accept_invitation(self, token: str, form: dict):
        """Accept an invitation and create account (POST /accept/{token})."""
        # Verify and consume the token
        data = account_tokens.verify_token(token, kind='invite')
        if not data:
            return self.send(400, pages.invitation_page(token, "", "", _("This invitation link is invalid or has expired.")))

        email = data['email']
        role = data['role']

        # Get form data
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        password2 = str(form.get("password2", ""))

        # Validate inputs
        if not username or not password:
            return self.send(400, pages.invitation_page(token, email, role, _("Username and password are required.")))

        if password != password2:
            return self.send(400, pages.invitation_page(token, email, role, _("Passwords do not match.")))

        # Validate username format (3-32 chars, alphanumeric + - and _)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{3,32}", username):
            return self.send(400, pages.invitation_page(token, email, role,
                _("Username must be 3-32 characters: letters, numbers, - and _")))

        # Create Headscale user
        try:
            hs.api("POST", "/user", {"name": username})
            sh.log.info("Created Headscale user for invitation: %s", username)
        except Exception as e:
            sh.log.error("Failed to create Headscale user '%s': %s", username, e)
            return self.send(500, pages.invitation_page(token, email, role,
                _("Failed to create user. The username may already exist.")))

        # Create local account
        try:
            account_id = lac.create_account(username, email, password, role=role, headscale_user=username)
            sh.log.info("Created local account from invitation: %s (role=%s)", username, role)
        except ValueError as e:
            # If account creation fails, try to delete the Headscale user
            try:
                hs.api("DELETE", f"/user/{username}")
            except Exception:
                pass
            return self.send(400, pages.invitation_page(token, email, role, str(e)))

        # Sign in automatically
        self.start_session({
            "kind": "local",
            "sub": f"local:{account_id}",
            "username": username,
            "name": email,
            "email": email,
            "groups": [],
            "admin": role == 'admin',
            "role": role,
        })

    def reset_password_page(self, token: str):
        """Show the password reset page (GET /reset/{token})."""
        # Check the token (don't consume it yet)
        data = account_tokens.check_token(token, kind='reset')
        if not data:
            return self.send(400, pages.reset_password_page(token, "", _("This password reset link is invalid or has expired.")))

        # Get the account
        account = lac.get_account(id=data['account_id'])
        if not account:
            return self.send(400, pages.reset_password_page(token, "", _("Account not found.")))

        return self.send(200, pages.reset_password_page(token, account['username'], ""))

    def reset_password(self, token: str, form: dict):
        """Reset password (POST /reset/{token})."""
        # Verify and consume the token
        data = account_tokens.verify_token(token, kind='reset')
        if not data:
            return self.send(400, pages.reset_password_page(token, "", _("This password reset link is invalid or has expired.")))

        # Get the account
        account = lac.get_account(id=data['account_id'])
        if not account:
            return self.send(400, pages.reset_password_page(token, "", _("Account not found.")))

        username = account['username']

        # Get form data
        password = str(form.get("password", ""))
        password2 = str(form.get("password2", ""))

        # Validate inputs
        if not password:
            return self.send(400, pages.reset_password_page(token, username, _("Password is required.")))

        if password != password2:
            return self.send(400, pages.reset_password_page(token, username, _("Passwords do not match.")))

        # Update password
        try:
            lac.update_password(data['account_id'], password)
            sh.log.info("Password reset for account: %s", username)
        except ValueError as e:
            return self.send(400, pages.reset_password_page(token, username, str(e)))

        # Sign in automatically
        self.start_session({
            "kind": "local",
            "sub": f"local:{data['account_id']}",
            "username": username,
            "name": account['email'],
            "email": account['email'],
            "groups": [],
            "admin": account['role'] == 'admin',
            "role": account['role'],
        })

    def start_session(self, data: dict):
        data.update(csrf=secrets.token_urlsafe(24), exp=time.time() + sh.SESSION_TTL)
        role = data.get("role") or ("admin" if data["admin"] else "member")
        data["sid"] = sessions.create(data, audit.client_ip(self), self.headers.get("User-Agent", ""), sh.SESSION_TTL)
        audit.request_event(self, data, "auth.signin", "", {"method": data["kind"], "role": role})
        sh.log.info("sign-in: %s (%s, %s)", data["username"] or data["name"], data["kind"], role)
        # Only a device approval page can be the destination (no open redirect)
        nxt = sh.unsign(self.cookie("hse_next")) or {}
        dest = nxt.get("path", "") if sh.REGISTER_PATH_RE.fullmatch(str(nxt.get("path", ""))) else f"{BASE}/machines"
        self.redirect(dest, [
            self.set_cookie("hse_session", sh.sign(data), sh.SESSION_TTL),
            self.set_cookie("hse_oidc", "", 0),
            self.set_cookie("hse_next", "", 0),
            self.set_cookie("hse_totp_pending", "", 0),  # Clear TOTP pending cookie
        ])

    def logout(self, session: dict):
        # With OIDC, also end the provider session; otherwise "Log out" would
        # not let another user sign in on the same browser.
        target = f"{BASE}/login?m=signed-out"
        if session.get("kind") == "oidc" and sh.SSO:
            end = sh.discovery().get("end_session_endpoint")
            if end:
                target = end + "?" + urllib.parse.urlencode({
                    "id_token_hint": session.get("idt", ""),
                    "post_logout_redirect_uri": f"{sh.PUBLIC_URL}{BASE}/",
                    "client_id": sh.OIDC_CLIENT_ID,
                })
        sessions.revoke(session.get("sid", ""))
        audit.request_event(self, session, "auth.signout")
        self.redirect(target, [self.set_cookie("hse_session", "", 0)])

    def revoke_session(self, session: dict, form: dict):
        """Sign out one session: your own, or (admins) anyone's."""
        sid = str(form.get("sid", ""))
        target = sessions.get(sid)
        own = sid == session.get("sid")
        mine = bool(target) and (target["sub"] == session.get("sub") if session.get("sub") else own)
        if not target or not (mine or session.get("admin")):
            return self.redirect(f"{BASE}/settings/sessions?m=session-not-found")
        sessions.revoke(sid)
        audit.request_event(self, session, "auth.session_revoked", target["name"], {"kind": target["kind"], "ip": target["ip"]})
        if own:
            return self.redirect(f"{BASE}/login?m=signed-out", [self.set_cookie("hse_session", "", 0)])
        return self.redirect(f"{BASE}/settings/sessions?m=session-revoked")

    def revoke_all_sessions(self, session: dict, form: dict):
        """Sign out everywhere: all of the caller's sessions (including this one),
        or, for admins with scope=everyone, everybody else's."""
        if form.get("scope") == "everyone":
            if not session.get("admin"):
                return self.fail(403, _("No permission"), _("This action is for admins only."))
            count = sessions.revoke_everyone(keep=session.get("sid", ""))
            audit.request_event(self, session, "auth.sessions_revoked_all", "", {"scope": "everyone", "count": count})
            return self.redirect(f"{BASE}/settings/sessions?m=sessions-revoked")
        if session.get("sub"):
            count = sessions.revoke_user(session["sub"])
        else:
            count = int(sessions.revoke(session.get("sid", "")))
        audit.request_event(self, session, "auth.sessions_revoked_all", "", {"scope": "mine", "count": count})
        return self.redirect(f"{BASE}/login?m=signed-out", [self.set_cookie("hse_session", "", 0)])

    # --- TOTP and account settings (Block 3.3) ---
    def change_password(self, session: dict, form: dict):
        """Change password for local account."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        old_password = str(form.get("old_password", ""))
        new_password = str(form.get("new_password", ""))
        new_password2 = str(form.get("new_password2", ""))

        if not old_password or not new_password:
            return self.redirect(f"{BASE}/settings/account?m=password-required")

        if new_password != new_password2:
            return self.redirect(f"{BASE}/settings/account?m=password-mismatch")

        # Verify old password
        if not lac.verify_password(old_password, account['pw_hash']):
            return self.redirect(f"{BASE}/settings/account?m=wrong-password")

        # Update password
        try:
            lac.update_password(account_id, new_password)
            audit.request_event(self, session, "account.password_changed", "", {})
            sh.log.info("Password changed for account %s (%s)", account_id, account['username'])
            return self.redirect(f"{BASE}/settings/account?m=password-changed")
        except ValueError as e:
            return self.redirect(f"{BASE}/settings/account?m=" + urllib.parse.quote(str(e)))

    def confirm_totp_enrollment(self, session: dict, form: dict):
        """Confirm TOTP enrollment with a valid code."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        code = str(form.get("code", "")).strip()
        if not code:
            return self.redirect(f"{BASE}/settings/account/totp/enroll?m=code-required")

        # Confirm TOTP
        valid = lac.confirm_totp(account_id, code)
        if not valid:
            return self.redirect(f"{BASE}/settings/account/totp/enroll?m=invalid-code")

        audit.request_event(self, session, "account.totp_enabled", "", {})
        sh.log.info("TOTP enabled for account %s (%s)", account_id, account['username'])
        return self.redirect(f"{BASE}/settings/account?m=totp-enabled")

    def disable_totp_account(self, session: dict, form: dict):
        """Disable TOTP for the account."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        # Disable TOTP
        lac.disable_totp(account_id)
        audit.request_event(self, session, "account.totp_disabled", "", {})
        sh.log.info("TOTP disabled for account %s (%s)", account_id, account['username'])
        return self.redirect(f"{BASE}/settings/account?m=totp-disabled")

    def reset_totp_recovery(self, session: dict, form: dict):
        """Generate new recovery codes."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        if not account.get('totp_confirmed'):
            return self.redirect(f"{BASE}/settings/account?m=totp-not-enabled")

        # Generate new recovery codes
        new_codes = lac.reset_recovery_codes(account_id)
        audit.request_event(self, session, "account.recovery_codes_reset", "", {})
        sh.log.info("Recovery codes reset for account %s (%s)", account_id, account['username'])

        # Show the new codes to the user
        return self.send(200, pages.recovery_codes_page(session, sh.CTX, new_codes))


def bootstrap_admin() -> None:
    """Bootstrap the first admin account on first run.

    If no accounts exist:
    - If HSE_ADMIN_EMAIL and HSE_ADMIN_PASSWORD are set: create admin account
    - If HSE_ADMIN_EMAIL is set but not password: create invitation token and log it
    - Otherwise: do nothing (the admin is created by the setup wizard)
    """
    accounts = lac.list_accounts()
    if accounts:
        return  # Accounts already exist, nothing to bootstrap

    admin_email = os.environ.get("HSE_ADMIN_EMAIL", "").strip()
    if not admin_email:
        sh.log.info("No accounts exist yet. Set HSE_ADMIN_EMAIL to bootstrap the first admin.")
        return

    admin_password = os.environ.get("HSE_ADMIN_PASSWORD", "").strip()

    if admin_password:
        # Create admin account with the given credentials
        username = admin_email.split("@")[0]  # Use email prefix as username
        try:
            lac.create_account(
                username=username,
                email=admin_email,
                password=admin_password,
                role="admin",
                headscale_user=username
            )
            sh.log.info("✓ Bootstrap: Created admin account '%s' (%s)", username, admin_email)

            # Create corresponding Headscale user
            try:
                hs.create_user(username)
                sh.log.info("✓ Bootstrap: Created Headscale user '%s'", username)
            except Exception as e:
                sh.log.warning("Failed to create Headscale user '%s': %s (will retry on first login)", username, e)
        except Exception as e:
            sh.log.error("Failed to create bootstrap admin account: %s", e)
    else:
        # Create invitation token and log it
        try:
            token = account_tokens.create_invitation(email=admin_email, role="admin", expires_hours=168)
            invite_url = f"{sh.PUBLIC_URL}{BASE}/accept/{token}"
            sh.log.info("=" * 80)
            sh.log.info("Bootstrap invitation created for admin: %s", admin_email)
            sh.log.info("Invitation URL (valid for 7 days):")
            sh.log.info("")
            sh.log.info("    %s", invite_url)
            sh.log.info("")
            sh.log.info("=" * 80)
        except Exception as e:
            sh.log.error("Failed to create bootstrap invitation: %s", e)
