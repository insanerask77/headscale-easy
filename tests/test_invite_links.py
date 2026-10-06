"""Invitation and password-reset links: the Users page shows each link once (with a copy button and
its expiry), lists the pending invitations, and, only when SMTP is set and an administrator clicks,
sends the link by e-mail. The SMTP server is a fake; nothing leaves the machine:

    python3 tests/test_invite_links.py
"""
import os
import re
import secrets
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_signup import SignupBase  # noqa: E402
from test_security import ADMIN, B, MEMBER, app, audit, hs, location, request  # noqa: E402

import local_accounts as lac  # noqa: E402
import mailer  # noqa: E402

SMTP_PASS = "smtp-" + secrets.token_hex(8)  # random per run: nothing credential-shaped in the source
SMTP_ENV = {"SMTP_HOST": "mail.example.test", "SMTP_PORT": "587", "SMTP_USERNAME": "robot",
            "SMTP_PASSWORD": SMTP_PASS, "SMTP_USE_TLS": "true", "SMTP_FROM": "vpn@example.test"}
USERS = [{"id": "9", "name": "carol"}]


class FakeSMTP:
    """Records what would be sent; ``fail`` makes the server refuse."""
    sent: list = []
    logins: list = []
    fail = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        FakeSMTP.logins.append((user, password))

    def send_message(self, msg):
        if FakeSMTP.fail:
            import smtplib
            raise smtplib.SMTPRecipientsRefused({"x": (550, b"no")})
        FakeSMTP.sent.append(msg)


class LinkBase(SignupBase):
    def setUp(self):
        super().setUp()
        FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail = [], [], False
        for p in (mock.patch.object(hs, "all_users", lambda: USERS), mock.patch.object(hs, "all_nodes", lambda: [])):
            p.start()
            self.addCleanup(p.stop)

    def smtp(self, **env):
        values = dict(SMTP_ENV, **env)
        patch = mock.patch.dict(os.environ, values)
        patch.start()
        self.addCleanup(patch.stop)
        p = mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP)
        p.start()
        self.addCleanup(p.stop)

    def invite(self, **extra):
        form = dict({"csrf": "tok", "role": "member", "email": "new@example.com", "days": "7"}, **extra)
        return request("POST", f"{B}/invitations", ADMIN, form)


class InvitationScreen(LinkBase):
    def test_the_link_is_shown_once_with_a_copy_button_and_the_expiry(self):
        status, _, body = self.invite()
        self.assertEqual(status, 200)
        link = re.search(r"<code>(http[^<]*/accept/[A-Za-z0-9_-]+)</code>", body)
        self.assertTrue(link, body[:500])
        self.assertIn(f'data-copy="{link.group(1)}"', body)
        self.assertIn("<time", body)
        # a second look at the page does not show it again, only the pending invitation
        _, _, again = request("GET", f"{B}/users", ADMIN)
        self.assertNotIn(link.group(1), again)
        self.assertIn("new@example.com", again)
        self.assertIn("/revoke", again)

    def test_the_link_works_and_is_single_use(self):
        _, _, body = self.invite()
        token = re.search(r"/accept/([A-Za-z0-9_-]+)</code>", body).group(1)
        self.assertEqual(request("GET", f"{B}/accept/{token}")[0], 200)
        self.assertIsNotNone(lac.check_token(token, "invite"))

    def test_without_smtp_there_is_no_mail_button(self):
        _, _, body = self.invite()
        self.assertNotIn("send-link", body)

    def test_a_member_cannot_invite(self):
        status, _, _ = request("POST", f"{B}/invitations", MEMBER, {"csrf": "tok", "role": "member", "email": "x@example.com"})
        self.assertIn(status, (303, 403))
        self.assertEqual(lac.list_active_invitations(), [])

    def test_bad_role_is_refused(self):
        self.assertEqual(self.invite(role="root")[0], 400)
        self.assertEqual(lac.list_active_invitations(), [])

    def test_revoke_removes_it(self):
        self.invite()
        token_hash = lac.list_active_invitations()[0]["token_hash"]
        _, headers, _ = request("POST", f"{B}/invitations/{token_hash}/revoke", ADMIN, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/users?m=invite-revoked")
        self.assertEqual(lac.list_active_invitations(), [])


class ResetLink(LinkBase):
    def setUp(self):
        super().setUp()
        self.account = lac.create_account("carol", "carol@example.com", "old-password-1", headscale_user="carol")

    def test_an_admin_gets_a_single_use_reset_link_once(self):
        status, _, body = request("POST", f"{B}/users/9/reset-link", ADMIN, {"csrf": "tok"})
        self.assertEqual(status, 200)
        link = re.search(r"<code>(http[^<]*/reset/[A-Za-z0-9_-]+)</code>", body)
        self.assertTrue(link)
        token = link.group(1).rsplit("/", 1)[1]
        self.assertIsNotNone(lac.check_token(token, "reset"))
        _, _, again = request("GET", f"{B}/users", ADMIN)
        self.assertNotIn(token, again)
        self.assertNotIn(token, repr(audit.request_event.call_args_list))

    def test_a_user_without_an_account_has_no_reset_link(self):
        with mock.patch.object(hs, "all_users", lambda: [{"id": "9", "name": "servers"}]):
            _, headers, _ = request("POST", f"{B}/users/9/reset-link", ADMIN, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/users?m=not-found")

    def test_a_member_cannot(self):
        status, headers, _ = request("POST", f"{B}/users/9/reset-link", MEMBER, {"csrf": "tok"})
        self.assertNotEqual(status, 200)
        self.assertEqual(lac.list_active_invitations(), [])


class MailByClick(LinkBase):
    def setUp(self):
        super().setUp()
        self.smtp()

    def link_form(self, body, **change):
        link = re.search(r"<code>(http[^<]*)</code>", body).group(1)
        kind = "reset" if "/reset/" in link else "invite"
        return dict({"csrf": "tok", "kind": kind, "link": link, "to": "new@example.com", "expires": "2030-01-01T00:00:00+00:00"},
                    **change)

    def test_nothing_is_sent_by_itself(self):
        self.invite()
        self.assertEqual(FakeSMTP.sent, [])

    def test_the_button_appears_and_a_click_sends_the_link(self):
        _, _, body = self.invite()
        self.assertIn(f"{B}/users/send-link", body)
        status, _, page = request("POST", f"{B}/users/send-link", ADMIN, self.link_form(body))
        self.assertEqual(status, 200)
        self.assertEqual(len(FakeSMTP.sent), 1)
        msg = FakeSMTP.sent[0]
        self.assertEqual(msg["To"], "new@example.com")
        self.assertEqual(msg["From"], "vpn@example.test")
        self.assertIn("/accept/", msg.get_content())
        self.assertEqual(FakeSMTP.logins, [("robot", SMTP_PASS)])

    def test_the_smtp_password_never_leaks(self):
        _, _, body = self.invite()
        _, _, page = request("POST", f"{B}/users/send-link", ADMIN, self.link_form(body))
        self.assertNotIn(SMTP_PASS, page)
        self.assertNotIn(SMTP_PASS, repr(audit.request_event.call_args_list))
        FakeSMTP.fail = True
        with self.assertLogs("hse.mailer", level="WARNING") as logs:
            _, _, failed = request("POST", f"{B}/users/send-link", ADMIN, self.link_form(body))
        self.assertNotIn(SMTP_PASS, failed)
        self.assertNotIn(SMTP_PASS, "\n".join(logs.output))
        self.assertIn("not sent", failed)

    def test_only_our_own_links_can_be_sent(self):
        _, _, body = self.invite()
        for link in ("https://evil.example/accept/abc", app.PUBLIC_URL + "/other/abc",
                     f"{app.PUBLIC_URL}{B}/accept/abc/../../x", f"{app.PUBLIC_URL}{B}/reset/abc"):
            _, headers, _ = request("POST", f"{B}/users/send-link", ADMIN, self.link_form(body, link=link, kind="invite"))
            self.assertEqual(location(headers), f"{B}/users?m=not-found", link)
        self.assertEqual(FakeSMTP.sent, [])

    def test_a_bad_address_is_refused(self):
        _, _, body = self.invite()
        request("POST", f"{B}/users/send-link", ADMIN, self.link_form(body, to="a@b.c\nBcc: x@y.zz"))
        self.assertEqual(FakeSMTP.sent, [])

    def test_a_member_cannot_send(self):
        _, _, body = self.invite()
        status, _, _ = request("POST", f"{B}/users/send-link", MEMBER, self.link_form(body))
        self.assertNotEqual(status, 200)
        self.assertEqual(FakeSMTP.sent, [])

    def test_without_smtp_the_endpoint_does_nothing(self):
        _, _, body = self.invite()
        with mock.patch.dict(os.environ, {"SMTP_HOST": ""}):
            _, headers, _ = request("POST", f"{B}/users/send-link", ADMIN, self.link_form(body))
        self.assertEqual(location(headers), f"{B}/users?m=not-found")
        self.assertEqual(FakeSMTP.sent, [])


class MailerUnit(unittest.TestCase):
    def setUp(self):
        FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail = [], [], False
        patch = mock.patch.dict(os.environ, SMTP_ENV)
        patch.start()
        self.addCleanup(patch.stop)
        p = mock.patch.object(mailer.smtplib, "SMTP", FakeSMTP)
        p.start()
        self.addCleanup(p.stop)

    def test_disabled_without_a_host(self):
        with mock.patch.dict(os.environ, {"SMTP_HOST": ""}):
            self.assertFalse(mailer.enabled())
            with self.assertRaises(mailer.MailError):
                mailer.send("a@example.com", "s", "t")

    def test_the_subject_is_one_line(self):
        mailer.send("a@example.com", "hello\r\nBcc: x@y.zz", "body")
        self.assertNotIn("\n", FakeSMTP.sent[0]["Subject"])
        self.assertIsNone(FakeSMTP.sent[0]["Bcc"])

    def test_addresses(self):
        for good in ("a@example.com", "first.last+tag@sub.example.org"):
            self.assertTrue(mailer.valid_address(good), good)
        for bad in ("", "a", "a@b", "a@b.c", "a b@example.com", "a@example.com,b@example.com", "a@example.com\nBcc: x@y.zz"):
            self.assertFalse(mailer.valid_address(bad), bad)

    def test_a_refusal_is_a_mail_error_without_the_server_text(self):
        FakeSMTP.fail = True
        with self.assertRaises(mailer.MailError) as ctx, self.assertLogs("hse.mailer", level="WARNING"):
            mailer.send("a@example.com", "s", "t")
        self.assertNotIn("550", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
