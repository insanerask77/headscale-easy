"""Users, invitations, sign-up and API-key handlers (mixin of app.Handler)."""

from __future__ import annotations

import hmac
import re
import secrets
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

import admin_pages
import audit
import headscale as hs
import local_accounts as lac
import account_tokens
import mailer
import sessions
import signup
import signup_pages
from handlers import shared as sh
from handlers.base import HandlerBase
from i18n import _
from ui import BASE


class UsersHandlers(HandlerBase):
    def create_apikey(self, session: dict, form: dict):
        days = str(form.get("days", "90"))
        days = days if days in sh.APIKEY_DAYS else "90"
        key = hs.api("POST", "/apikey", {"expiration": sh.iso_in(int(days))})["apiKey"]
        sh.log.info("%s created an API key (%s d)", session["username"], days)
        audit.request_event(self, session, "apikey.create", hs.api_key_prefix(key), {"days": int(days)}, f"apikey:{hs.api_key_prefix(key)}")
        self.keys_view(session, "", new_apikey=key)

    def expire_apikey(self, key_id: str, session: dict | None = None):
        key = next((k for k in hs.api_keys() if str(k.get("id")) == key_id), None)
        if key is None:
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        own = hs.own_api_key_prefix()
        if own and own in (key.get("prefix") or ""):
            return self.redirect(f"{BASE}/settings/keys?m=apikey-own")
        hs.api("POST", "/apikey/expire", {"id": key_id})
        audit.request_event(self, session, "apikey.expire", hs.api_key_prefix(key.get("prefix") or "") or f"#{key_id}", ref=f"apikey:{hs.api_key_prefix(key.get('prefix') or '') or key_id}")
        return self.redirect(f"{BASE}/settings/keys?m=apikey-expired")

    # --- users ---
    def users_view(self, session: dict, status: int = 200, flash: str = "", error: str = "",
                   result: dict | None = None, new_key: tuple | None = None):
        users = hs.all_users()
        signins = {a["headscale_user"]: a for a in lac.list_accounts() if a["headscale_user"]}
        extra = (signup_pages.key_result_box(*new_key) if new_key else "") + \
            signup_pages.keys_section(session, account_tokens.list_signup_keys(), signup.mode())
        return self.send(status, admin_pages.users_page(session, sh.CTX, users, hs.all_nodes(), flash, error=error,
                                                        result=result, signins=signins, extra=extra,
                                                        invites=account_tokens.list_active_invitations(),
                                                        can_mail=mailer.enabled()))

    def users_error(self, session: dict, msg: str):
        return self.users_view(session, 400, error=msg)

    def create_user(self, session: dict, form: dict):
        name = str(form.get("name", "")).strip().lower()
        if not sh.USER_NAME_RE.fullmatch(name):
            return self.redirect(f"{BASE}/users?m=bad-user")
        body = {"name": name}
        if form.get("display_name"):
            body["displayName"] = str(form["display_name"]).strip()[:100]
        password = str(form.get("password", ""))
        email = str(form.get("email", "")).strip().lower()
        role = str(form.get("role", "member"))
        must_change = form.get("must_change") == "1"
        with_account = bool(password)
        if with_account:  # a person who can sign in, not only a Headscale user
            if not signup.USERNAME_RE.fullmatch(name):
                return self.users_error(session, _("The user name of an account has 3-32 characters: lowercase letters, numbers, - and _"))
            if not signup.EMAIL_RE.fullmatch(email):
                return self.users_error(session, _("Enter a valid email address."))
            if role not in ("admin", "network_admin", "auditor", "member"):
                return self.users_error(session, _("Choose one of the options."))
            if len(password) < lac.MIN_PASSWORD_LENGTH:
                return self.users_error(session, _("The password must have at least {n} characters.", n=lac.MIN_PASSWORD_LENGTH))
            if lac.get_account(username=name) or lac.get_account(email=email):
                return self.users_error(session, _("That user name or email is already in use."))
        try:
            hs.api("POST", "/user", body)
        except urllib.error.HTTPError as exc:
            return self.users_error(session, _("Could not create it: {reason}", reason=hs.api_error(exc)))
        if with_account:
            try:
                lac.create_account(name, email, password, role=role, headscale_user=name, must_change=must_change)
            except ValueError as exc:
                self.drop_headscale_user(name)
                return self.users_error(session, str(exc))
        sh.log.info("%s created %s %s", session["username"], "account" if with_account else "local user", name)
        # Never the password
        audit.request_event(self, session, "user.create", name, {
            "display_name": body.get("displayName", ""), **({"account": True, "role": role, "must_change": must_change} if with_account else {})})
        return self.redirect(f"{BASE}/users?m=user-created")

    def drop_headscale_user(self, name: str):
        """Undo a Headscale user created a moment ago (the account behind it failed)."""
        try:
            user = next((u for u in hs.all_users() if u.get("name") == name), None)
            if user:
                hs.api("DELETE", f"/user/{user['id']}")
        except Exception:  # noqa: BLE001
            sh.log.warning("could not remove the Headscale user %s after a failed account", name)

    def set_user_password(self, session: dict, user_id: str, form: dict):
        user = next((u for u in hs.all_users() if str(u["id"]) == user_id), None)
        account = lac.get_account_by_headscale_user(user["name"]) if user else None
        if not account:
            return self.redirect(f"{BASE}/users?m=not-found")
        password = str(form.get("password", ""))
        if len(password) < lac.MIN_PASSWORD_LENGTH:
            return self.users_error(session, _("The password must have at least {n} characters.", n=lac.MIN_PASSWORD_LENGTH))
        lac.update_password(account["id"], password, must_change=form.get("must_change") == "1")
        # Their open sessions end: whoever had the old password is signed out
        sessions.revoke_user(f"local:{account['id']}", keep=session.get("sid", ""))
        audit.request_event(self, session, "user.password_set", account["username"],
                            {"must_change": form.get("must_change") == "1"}, f"user:{user_id}")
        return self.redirect(f"{BASE}/users?m=password-set")

    def create_reset_link(self, session: dict, user_id: str):
        """A single-use password reset link for a person with a local account (shown once)."""
        user = next((u for u in hs.all_users() if str(u["id"]) == user_id), None)
        account = lac.get_account_by_headscale_user(user["name"]) if user else None
        if not account:
            return self.redirect(f"{BASE}/users?m=not-found")
        hours = 24
        token = account_tokens.create_reset_token(account["id"], expires_hours=hours)
        expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        audit.request_event(self, session, "user.reset_link", account["username"], {}, f"user:{user_id}")
        return self.users_view(session, result={
            "kind": "reset", "link": f"{sh.PUBLIC_URL}{BASE}/reset/{token}", "expires": expires,
            "email": account.get("email") or "", "sent": False})

    def send_link_mail(self, session: dict, form: dict):
        """"Send by e-mail" on a link that was just shown: only when SMTP is set, only on a click,
        and only for a link of ours (the link comes back in the form, so it is checked)."""
        kind = str(form.get("kind", ""))
        link = str(form.get("link", ""))
        to = str(form.get("to", "")).strip()
        expires = str(form.get("expires", ""))
        prefix = {"invite": f"{sh.PUBLIC_URL}{BASE}/accept/", "reset": f"{sh.PUBLIC_URL}{BASE}/reset/"}.get(kind)
        if not mailer.enabled() or not prefix or not link.startswith(prefix) \
                or not re.fullmatch(r"[A-Za-z0-9_-]+", link[len(prefix):]):
            return self.redirect(f"{BASE}/users?m=not-found")
        result = {"kind": kind, "link": link, "expires": expires, "email": to, "sent": False}
        if kind == "invite":
            subject = _("You are invited to {name}", name=sh.TAILNET_NAME)
            body = _("You have been invited to join {name}. Open this link to choose your user name and password "
                     "(it works once and expires on {when}):", name=sh.TAILNET_NAME, when=expires[:16].replace("T", " ") + " UTC")
        else:
            subject = _("Reset your password on {name}", name=sh.TAILNET_NAME)
            body = _("Open this link to choose a new password (it works once and expires on {when}):",
                     name=sh.TAILNET_NAME, when=expires[:16].replace("T", " ") + " UTC")
        try:
            mailer.send(to, subject, f"{body}\n\n{link}\n")
        except mailer.MailError as exc:
            return self.users_view(session, 400, error=_("The e-mail was not sent: {why}", why=str(exc)), result=result)
        audit.request_event(self, session, "link.email", to, {"kind": kind})
        result["sent"] = True
        return self.users_view(session, result=result)

    # --- self-registration ---
    def signup_open(self) -> str:
        """The active sign-up mode."""
        return signup.mode()

    def signup_form(self):
        mode = self.signup_open()
        if mode == "off":
            return self.send(404, "Not found", "text/plain")
        token = secrets.token_urlsafe(24)
        return self.send(200, signup_pages.signup_page(mode, token),
                         headers=[self.set_cookie("hse_signup", token, 3600)])

    def signup_submit(self, form: dict):
        mode = self.signup_open()
        if mode == "off":
            return self.send(404, "Not found", "text/plain")
        ip = audit.client_ip(self)
        wait = self.rate_limited("signup")
        if wait:
            return self.too_many(wait)
        token = self.cookie("hse_signup") or ""
        if not token or not hmac.compare_digest(str(form.get("csrf", "")), token):
            return self.fail(403, _("Session expired"), _("Reload the page and try again."))
        sessions.hit(f"signup:{ip}")  # every attempt counts, successful or not
        username = str(form.get("username", "")).strip().lower()
        email = str(form.get("email", "")).strip().lower()
        password, password2 = str(form.get("password", "")), str(form.get("password2", ""))
        values = {"username": username, "email": email}

        def again(message: str, status: int = 400):
            return self.send(status, signup_pages.signup_page(mode, token, message, values))

        if not signup.USERNAME_RE.fullmatch(username):
            return again(_("The user name has 3-32 characters: lowercase letters, numbers, - and _"))
        if not signup.EMAIL_RE.fullmatch(email):
            return again(_("Enter a valid email address."))
        if len(password) < lac.MIN_PASSWORD_LENGTH:
            return again(_("The password must have at least {n} characters.", n=lac.MIN_PASSWORD_LENGTH))
        if password != password2:
            return again(_("Passwords do not match."))
        key_id = None
        if mode == "invite":
            key_id = account_tokens.use_signup_key(str(form.get("key", "")).strip())
            if key_id is None:  # wrong, expired, revoked or used up: one message for all
                audit.request_event(self, None, "signup.rejected", "", {"reason": "key"}, actor="")
                return again(_("The invitation key is not valid."))

        def give_back():
            if key_id is not None:
                account_tokens.release_signup_key(key_id)

        if lac.get_account(username=username) or lac.get_account(email=email):
            give_back()
            return again(_("That user name or email is already in use."))
        try:
            hs.api("POST", "/user", {"name": username})
        except Exception:  # noqa: BLE001
            give_back()
            return again(_("That user name or email is already in use."))
        try:
            lac.create_account(username, email, password, role="member", headscale_user=username)
        except ValueError:
            give_back()
            self.drop_headscale_user(username)
            return again(_("That user name or email is already in use."))
        sh.log.info("sign-up: %s (%s)", username, mode)
        audit.request_event(self, None, "signup.create", username, {"mode": mode}, actor=username)
        return self.redirect(f"{BASE}/login?m=signed-up", [self.set_cookie("hse_signup", "", 0)])

    def save_signup_mode(self, session: dict, form: dict):
        value = str(form.get("mode", ""))
        if value not in signup.MODES:
            return self.redirect(f"{BASE}/settings/general?m=bad-signup")
        if signup.set_mode(value):
            audit.request_event(self, session, "settings.signup", _("Sign-up"), {"to": value})
        return self.redirect(f"{BASE}/settings/general?m=signup-saved")

    def create_signup_key(self, session: dict, form: dict):
        uses, days = str(form.get("uses", "1")), str(form.get("days", "7"))
        if uses not in signup.KEY_USES or days not in signup.KEY_DAYS:
            return self.users_error(session, _("Choose one of the options."))
        label = str(form.get("label", "")).strip()[:60]
        key = account_tokens.create_signup_key(label, int(uses), int(days) * 24 if days != "0" else None)
        # Never the key
        audit.request_event(self, session, "signup_key.create", label, {"uses": uses, "days": days})
        return self.users_view(session, new_key=(key, label))

    def revoke_signup_key(self, session: dict, key_id: int):
        if account_tokens.revoke_signup_key(key_id):
            audit.request_event(self, session, "signup_key.revoke", str(key_id))
            return self.redirect(f"{BASE}/users?m=signup-key-revoked")
        return self.redirect(f"{BASE}/users?m=not-found")

    def user_action(self, session: dict, user_id: str, action: str, form: dict):
        user = next((u for u in hs.all_users() if str(u["id"]) == user_id), None)
        if user is None:
            return self.redirect(f"{BASE}/users?m=not-found")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not sh.USER_NAME_RE.fullmatch(name):
                    return self.redirect(f"{BASE}/users?m=bad-user")
                hs.api("POST", f"/user/{user_id}/rename/{urllib.parse.quote(name)}")
                audit.request_event(self, session, "user.rename", name, {"from": user.get("name"), "to": name}, f"user:{user_id}")
                return self.redirect(f"{BASE}/users?m=user-renamed")
            if any(str((n.get("user") or {}).get("id")) == user_id for n in hs.all_nodes()):
                return self.redirect(f"{BASE}/users?m=user-has-nodes")
            hs.api("DELETE", f"/user/{user_id}")
            sh.log.info("%s deleted user %s", session["username"], user_id)
            audit.request_event(self, session, "user.delete", user.get("name"), ref=f"user:{user_id}")
            sessions.revoke_named(user.get("name") or "")
            return self.redirect(f"{BASE}/users?m=user-deleted")
        except urllib.error.HTTPError as exc:
            return self.users_error(session, hs.api_error(exc))

    # --- invitations ---
    def create_invitation(self, session: dict, form: dict):
        role = str(form.get("role", ""))
        email = str(form.get("email", "")).strip()
        days = str(form.get("days", "7"))
        days = days if days in sh.INVITE_DAYS else "7"
        who = session["username"] or session["name"]
        try:
            if role not in ("admin", "network_admin", "auditor", "member"):
                raise ValueError(f"Invalid role: {role}")
            expires_hours = int(days) * 24
            token = account_tokens.create_invitation(email, role=role, expires_hours=expires_hours)
            link = f"{sh.PUBLIC_URL}{BASE}/accept/{token}"
            expires = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
            sh.log.info("%s created an invitation (%s, %s, %s days)", who, role, email, days)
            audit.request_event(self, session, "invite.create", email, {"role": role, "days": days})
            return self.users_view(session, error="", result={
                "kind": "invite", "link": link, "expires": expires, "email": email, "sent": False})
        except ValueError as exc:
            sh.log.warning("%s tried to create an invitation: %s", who, exc)
            return self.users_error(session, str(exc))

    def revoke_invitation(self, session: dict, pk: str):
        # pk is the token hash
        if account_tokens.revoke_token_by_hash(pk):
            sh.log.info("%s revoked an invitation", session["username"] or session["name"])
            audit.request_event(self, session, "invite.revoke", "")
            return self.redirect(f"{BASE}/users?m=invite-revoked")
        return self.redirect(f"{BASE}/users?m=not-found")
