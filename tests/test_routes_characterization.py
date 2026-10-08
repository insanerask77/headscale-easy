"""Characterization of the console's route table (``Handler.do_GET`` / ``do_POST`` / ``do_HEAD``).

Records what the code does TODAY for every route and every role -- status code and ``Location`` --
so the route table and the Handler can be taken apart (refactor phases 4-5) without changing who
can reach what. It documents behaviour; it does not judge it. Oddities are marked ``QUIRK:``.

Roles (columns of the tables): anonymous, member, admin, auditor, network admin.
``~`` in a Location stands for the console base path (``/console``).

Headscale is replaced by one permissive mock, so a row records the *routing and access decision*:
the page rendered (200), the redirect, or the refusal. Where a POST with an empty form falls into
the generic error handler the row says ``?m=failed``; that too is current behaviour.

Standard library only:

    python3 -m unittest tests/test_routes_characterization.py
"""
import logging
import unittest
from unittest import mock

from test_security import ADMIN, BOB, BOB_NODE, MEMBER, B, Base, app, location, request  # sets up env + sys.path
from handlers import shared as sh  # noqa: E402
from handlers import users as users_handlers  # noqa: E402

import local_accounts as lac  # noqa: E402

ROLES = ("anon", "member", "admin", "auditor", "netadmin")
SESSIONS = {
    "anon": None,
    "member": MEMBER,
    "admin": ADMIN,
    "auditor": dict(MEMBER, sub="c", username="aud", role="auditor"),
    "netadmin": dict(MEMBER, sub="d", username="net", role="network_admin"),
}

# (path, expected) -- expected is one string for every role, or a tuple in ROLES order.
# A string is "<status>" or "<status> <Location>".
GET_ROUTES = [
    ('/healthz', '200'),
    ('/restore-status?id=x', '200'),
    ('/static/style.css', '200'),
    ('/static/nope', '404'),
    ('/login', '200'),
    ('/login?m=signed-out', '200'),
    ('/signup', '404'),
    ('/login/sso', ('303 ~/login', '404', '404', '404', '404')),
    ('/login/totp', '303 ~/login'),
    ('/callback', ('303 ~/login', '404', '404', '404', '404')),
    ('/accept/abc', '400'),
    ('/reset/abc', '400'),
    ('/register/abcdefgh12', ('303 ~/login', '200', '200', '403', '200')),
    ('/', ('303 ~/login', '303 ~/machines', '303 ~/machines', '303 ~/machines', '303 ~/machines')),
    ('/machines', ('303 ~/login', '200', '200', '200', '200')),
    ('/machines.csv', ('303 ~/login', '200', '200', '200', '200')),
    ('/machines/7', ('303 ~/login', '200', '200', '200', '200')),
    ('/machines/99', ('303 ~/login', '303 ~/machines?m=not-found', '303 ~/machines?m=not-found', '303 ~/machines?m=not-found', '303 ~/machines?m=not-found')),
    ('/add', ('303 ~/login', '200', '200', '200', '200')),
    ('/dns', ('303 ~/login', '200', '200', '200', '200')),
    ('/settings', ('303 ~/login', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general')),
    ('/settings/general', ('303 ~/login', '200', '200', '200', '200')),
    ('/settings/keys', ('303 ~/login', '200', '200', '200', '200')),
    ('/settings/sessions', ('303 ~/login', '200', '200', '200', '200')),
    ('/settings/account', ('303 ~/login', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general')),
    ('/settings/account/totp/enroll', ('303 ~/login', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general')),
    ('/acl', ('303 ~/login', '403', '200', '200', '200')),
    ('/users', ('303 ~/login', '403', '200', '200', '403')),
    ('/derp', ('303 ~/login', '403', '200', '200', '403')),
    ('/settings/status', ('303 ~/login', '403', '200', '200', '403')),
    ('/backups', ('303 ~/login', '403', '200', '403', '403')),
    ('/logs', ('303 ~/login', '403', '200', '200', '403')),
    ('/logs.csv', ('303 ~/login', '403', '200', '200', '403')),
    ('/nope', ('303 ~/login', '404', '404', '404', '404')),
]

POST_ROUTES = [
    ('/login/apikey', '401'),
    ('/login/local', '401'),
    ('/signup', '404'),
    ('/login/totp', '303 ~/login'),
    ('/accept/abc', '400'),
    ('/reset/abc', '400'),
    ('/logout', ('303 ~/login', '303 ~/login?m=signed-out', '303 ~/login?m=signed-out', '303 ~/login?m=signed-out', '303 ~/login?m=signed-out')),
    ('/settings/sessions/revoke', ('303 ~/login', '303 ~/settings/sessions?m=session-not-found', '303 ~/settings/sessions?m=session-not-found', '303 ~/settings/sessions?m=session-not-found', '303 ~/settings/sessions?m=session-not-found')),
    ('/settings/sessions/revoke-all', ('303 ~/login', '303 ~/login?m=signed-out', '303 ~/login?m=signed-out', '303 ~/login?m=signed-out', '303 ~/login?m=signed-out')),
    ('/settings/account/password', ('303 ~/login', '403', '403', '403', '403')),
    ('/settings/account/totp/confirm', ('303 ~/login', '403', '403', '403', '403')),
    ('/settings/account/totp/disable', ('303 ~/login', '403', '403', '403', '403')),
    ('/settings/account/totp/recovery/reset', ('303 ~/login', '403', '403', '403', '403')),
    ('/settings/language', ('303 ~/login', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general', '303 ~/settings/general')),
    ('/keys', ('303 ~/login', '200', '303 ~/settings/keys?m=no-user', '303 ~/settings/keys?m=no-user', '303 ~/settings/keys?m=no-user')),
    ('/add/docker', ('303 ~/login', '200', '200', '403', '200')),
    ('/keys/3/revoke', ('303 ~/login', '303 ~/settings/keys?m=key-revoked', '303 ~/settings/keys?m=not-found', '303 ~/settings/keys?m=key-revoked', '303 ~/settings/keys?m=key-revoked')),
    ('/machines/7/rename', ('303 ~/login', '303 ~/machines?m=bad-name', '303 ~/machines?m=bad-name', '303 ~/machines?m=forbidden', '303 ~/machines?m=bad-name')),
    ('/machines/7/delete', ('303 ~/login', '303 ~/machines?m=removed', '303 ~/machines?m=removed', '303 ~/machines?m=forbidden', '303 ~/machines?m=removed')),
    ('/machines/7/expire', ('303 ~/login', '303 ~/machines?m=expired', '303 ~/machines?m=expired', '303 ~/machines?m=forbidden', '303 ~/machines?m=expired')),
    ('/machines/7/expiry', ('303 ~/login', '303 ~/machines?m=forbidden', '303 ~/machines?m=expiry-on', '303 ~/machines?m=forbidden', '303 ~/machines?m=forbidden')),
    ('/machines/7/routes', ('303 ~/login', '303 ~/machines?m=forbidden', '303 ~/machines?m=routes', '303 ~/machines?m=forbidden', '303 ~/machines?m=forbidden')),
    ('/machines/7/approve-routes', ('303 ~/login', '303 ~/machines?m=forbidden', '303 ~/machines?m=routes', '303 ~/machines?m=forbidden', '303 ~/machines?m=forbidden')),
    ('/machines/7/tags', ('303 ~/login', '303 ~/machines?m=forbidden', '303 ~/machines?m=tags', '303 ~/machines?m=forbidden', '303 ~/machines?m=forbidden')),
    ('/register/abcdefgh12', ('303 ~/login', '303 ~/machines?m=registered', '303 ~/machines?m=failed', '403', '303 ~/register/abcdefgh12')),
    ('/acl/test', ('303 ~/login', '403', '200', '200', '200')),
    ('/acl', ('303 ~/login', '403', '200', '403', '200')),
    ('/dns', ('303 ~/login', '403', '400', '403', '400')),
    ('/acl/rules', ('303 ~/login', '403', '303 ~/acl?m=acl-unreadable&tab=rules', '403', '303 ~/acl?m=acl-unreadable&tab=rules')),
    ('/acl/groups', ('303 ~/login', '403', '303 ~/acl?m=acl-unreadable&tab=groups', '403', '303 ~/acl?m=acl-unreadable&tab=groups')),
    ('/acl/tags', ('303 ~/login', '403', '303 ~/acl?m=acl-unreadable&tab=groups', '403', '303 ~/acl?m=acl-unreadable&tab=groups')),
    ('/acl/autoapprove/routes', ('303 ~/login', '403', '303 ~/acl?m=acl-unreadable&tab=auto', '403', '303 ~/acl?m=acl-unreadable&tab=auto')),
    ('/acl/autoapprove/exit-node', ('303 ~/login', '403', '303 ~/acl?m=acl-unreadable&tab=auto', '403', '303 ~/acl?m=acl-unreadable&tab=auto')),
    ('/acl/ssh', ('303 ~/login', '403', '303 ~/acl?m=acl-unreadable&tab=ssh', '403', '303 ~/acl?m=acl-unreadable&tab=ssh')),
    ('/acl/zzz', ('303 ~/login', '403', '404', '403', '404')),
    ('/machines/register', ('303 ~/login', '403', '303 ~/machines?m=failed', '403', '403')),
    ('/machines/remove-inactive', ('303 ~/login', '403', '303 ~/machines?m=inactive-none', '403', '403')),
    ('/machines/bulk/expire', ('303 ~/login', '403', '303 ~/machines?m=failed', '403', '403')),
    ('/machines/bulk/remove', ('303 ~/login', '403', '303 ~/machines?m=failed', '403', '403')),
    ('/machines/bulk/tags', ('303 ~/login', '403', '303 ~/machines?m=failed', '403', '403')),
    ('/settings/key-expiry', ('303 ~/login', '403', '200', '403', '403')),
    ('/derp', ('303 ~/login', '403', '400', '403', '403')),
    ('/settings/signup', ('303 ~/login', '403', '303 ~/settings/general?m=bad-signup', '403', '403')),
    ('/signup/keys', ('303 ~/login', '403', '200', '403', '403')),
    ('/signup/keys/1/revoke', ('303 ~/login', '403', '303 ~/users?m=signup-key-revoked', '403', '403')),
    ('/users/1/password', ('303 ~/login', '403', '303 ~/users?m=not-found', '403', '403')),
    ('/users/1/reset-link', ('303 ~/login', '403', '303 ~/users?m=not-found', '403', '403')),
    ('/users/send-link', ('303 ~/login', '403', '303 ~/users?m=not-found', '403', '403')),
    ('/settings/notify-test', ('303 ~/login', '403', '303 ~/settings/general?m=notify-none', '403', '403')),
    ('/backups/run', ('303 ~/login', '403', '303 ~/backups?m=backup-started', '403', '403')),
    ('/backups/settings', ('303 ~/login', '403', '303 ~/machines?m=failed', '403', '403')),
    ('/backups/restore', ('303 ~/login', '403', '303 ~/backups?m=backup-restore-confirm', '403', '403')),
    ('/backups/download', ('303 ~/login', '403', '404', '403', '403')),
    ('/users', ('303 ~/login', '403', '303 ~/users?m=bad-user', '403', '403')),
    ('/users/1/rename', ('303 ~/login', '403', '303 ~/users?m=not-found', '403', '403')),
    ('/users/1/delete', ('303 ~/login', '403', '303 ~/users?m=not-found', '403', '403')),
    ('/invitations', ('303 ~/login', '403', '400', '403', '403')),
    ('/invitations/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revoke', ('303 ~/login', '403', '303 ~/users?m=not-found', '403', '403')),
    ('/apikeys', ('303 ~/login', '403', '200', '403', '403')),
    ('/apikeys/1/expire', ('303 ~/login', '403', '303 ~/settings/keys?m=not-found', '403', '403')),
    ('/nope', ('303 ~/login', '403', '404', '403', '403')),
]

# POSTs that are reachable without a session (or are 'Public' in do_POST): not CSRF-checked.
PUBLIC_POSTS = {"/login/apikey", "/login/local", "/signup", "/login/totp", "/accept/abc", "/reset/abc"}


def permissive_headscale() -> mock.MagicMock:
    """A Headscale client that answers every call with something harmless: one user (bob), one node (7)."""
    hsm = mock.MagicMock()
    hsm.all_nodes.return_value = [BOB_NODE]
    hsm.user_nodes.return_value = [BOB_NODE]
    hsm.get_node.side_effect = lambda i: BOB_NODE if i == "7" else None
    hsm.owned_node.side_effect = lambda user, i: BOB_NODE if i == "7" else None
    hsm.all_users.return_value = [BOB]
    hsm.user_by_name.return_value = BOB
    hsm.user_for_sub.side_effect = lambda sub: BOB if sub == "b" else None
    hsm.host_details.return_value = {}
    hsm.dns_config.return_value = {}
    hsm.latest_tailscale_version.return_value = ""
    hsm.derp_regions.return_value = {}
    hsm.all_keys.return_value = []
    hsm.api_keys.return_value = []
    hsm.user_keys.return_value = []
    hsm.key_expiry_days.return_value = 90
    hsm.control_status.return_value = {}
    hsm.restore_result.return_value = {}
    hsm.control_backup.return_value = "started"
    return hsm


class RouteCase(Base):
    @classmethod
    def setUpClass(cls):
        lac.configure(":memory:")

    def setUp(self):
        super().setUp()
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        HS = permissive_headscale()
        patches = [
            mock.patch.object(app, "hs", HS),
            mock.patch.object(sh, "hs", HS),
            mock.patch.object(users_handlers, "hs", HS),
            # data sources that read files or the supervisor: not part of the routing decision
            mock.patch.object(app.Handler, "derp_view", lambda *a, **k: "derp"),
            mock.patch.object(app.audit, "csv_export", lambda params: "csv"),
            mock.patch.object(app.server_status, "collect", lambda: {}),
            mock.patch.object(app.status_pages, "status_page", lambda *a, **k: "status"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def outcome(self, method: str, path: str, role: str, form: dict | None = None, **kw) -> str:
        status, headers, _ = request(method, B + path, SESSIONS[role], form, **kw)
        loc = location(headers).replace(B, "~")
        return f"{status} {loc}" if loc else str(status)

    @staticmethod
    def expected(spec, role: str) -> str:
        return spec if isinstance(spec, str) else spec[ROLES.index(role)]


class GetRoutes(RouteCase):
    def test_every_get_route_for_every_role(self):
        for path, spec in GET_ROUTES:
            for role in ROLES:
                with self.subTest(path=path, role=role):
                    self.assertEqual(self.outcome("GET", path, role), self.expected(spec, role))

    def test_head_answers_like_get_without_a_body(self):
        for path in ("/healthz", "/login", "/machines", "/users", "/nope"):
            for role in ("anon", "member", "admin"):
                with self.subTest(path=path, role=role):
                    get = request("GET", B + path, SESSIONS[role])
                    head = request("HEAD", B + path, SESSIONS[role])
                    self.assertEqual(head[0], get[0])
                    self.assertEqual(location(head[1]), location(get[1]))
                    self.assertEqual(head[2], "")

    def test_public_pages_do_not_need_a_session(self):
        for path in ("/healthz", "/restore-status?id=x", "/static/style.css", "/login", "/login?m=signed-out"):
            self.assertEqual(self.outcome("GET", path, "anon"), "200", path)

    def test_login_page_is_served_even_to_a_signed_in_user(self):
        # QUIRK: no redirect to /machines when already signed in.
        for role in ("member", "admin"):
            self.assertEqual(self.outcome("GET", "/login", role), "200", role)

    def test_sso_routes_fall_through_when_sso_is_off(self):
        # QUIRK: /login/sso and /callback are plain unknown paths without SSO: anonymous users are sent to the
        # login page, signed-in users get 404 (the same as any other unknown path).
        self.assertFalse(sh.SSO)
        for path in ("/login/sso", "/callback"):
            self.assertEqual(self.outcome("GET", path, "anon"), "303 ~/login", path)
            self.assertEqual(self.outcome("GET", path, "admin"), "404", path)

    def test_unknown_path_is_404_only_after_signing_in(self):
        self.assertEqual(self.outcome("GET", "/nope", "anon"), "303 ~/login")
        for role in ROLES[1:]:
            self.assertEqual(self.outcome("GET", "/nope", role), "404", role)

    def test_csv_downloads_are_attachments(self):
        for path, name in (("/machines.csv", "machines.csv"), ("/logs.csv", "activity-log.csv")):
            _, headers, _ = request("GET", B + path, ADMIN)
            self.assertEqual(headers["content-type"], ["text/csv; charset=utf-8"], path)
            self.assertEqual(headers["content-disposition"], [f'attachment; filename="{name}"'], path)

    def test_anonymous_visit_to_a_device_approval_link_is_remembered(self):
        # The sign-in comes back to the approval page through the signed hse_next cookie.
        _, headers, _ = request("GET", f"{B}/register/abcdefgh12")
        self.assertEqual(location(headers), f"{B}/login")
        self.assertTrue(any(c.startswith("hse_next=") for c in headers["set-cookie"]))
        _, headers, _ = request("GET", f"{B}/machines")
        self.assertNotIn("set-cookie", headers)

    def test_a_forced_password_change_blocks_everything_else(self):
        session = dict(MEMBER, kind="local", must_change=True)
        with mock.patch.object(app.Handler, "session", lambda self: dict(session)):
            for path in ("/machines", "/users", "/settings/general", "/register/abcdefgh12"):
                status, headers, _ = request("GET", B + path)
                self.assertEqual((status, location(headers)), (303, f"{B}/settings/account?m=must-change"), path)
            # the account page and sign-out stay reachable (the account page is for local accounts only)
            status, headers, _ = request("GET", f"{B}/settings/account")
            self.assertNotEqual(location(headers), f"{B}/settings/account?m=must-change")

    def test_event_stream_refusals(self):
        self.assertEqual(request("GET", f"{B}/events")[0], 401)
        must_change = dict(MEMBER, must_change=True)
        with mock.patch.object(app.Handler, "session", lambda self: dict(must_change)):
            self.assertEqual(request("GET", f"{B}/events")[0], 403)
        # a page on another site may not open the stream
        status, _, _ = request("GET", f"{B}/events", ADMIN, headers={"Origin": "https://evil.example", "Host": "vpn.example.com"})
        self.assertEqual(status, 403)


class PostRoutes(RouteCase):
    def test_every_post_route_for_every_role(self):
        for path, spec in POST_ROUTES:
            for role in ROLES:
                with self.subTest(path=path, role=role):
                    self.assertEqual(self.outcome("POST", path, role, {"csrf": "tok"}), self.expected(spec, role))

    def test_signed_in_posts_without_the_csrf_token_are_refused(self):
        # QUIRK: the CSRF check comes before the role check, so every role gets 403 here, even for
        # routes it could never use.
        for path, _spec in POST_ROUTES:
            if path in PUBLIC_POSTS:
                continue
            for role in ROLES[1:]:
                with self.subTest(path=path, role=role):
                    self.assertEqual(self.outcome("POST", path, role, {}), "403")

    def test_anonymous_posts_are_sent_to_sign_in_before_any_csrf_check(self):
        for path, _spec in POST_ROUTES:
            if path in PUBLIC_POSTS:
                continue
            with self.subTest(path=path):
                self.assertEqual(self.outcome("POST", path, "anon", {}), "303 ~/login")

    def test_backup_upload_is_checked_for_a_session_first(self):
        self.assertEqual(self.outcome("POST", "/backups/upload", "anon"), "303 ~/login")

    def test_public_posts_answer_without_a_session(self):
        # Empty forms: bad credentials / invalid token, but never a redirect to sign-in.
        for path, status in (("/login/apikey", "401"), ("/login/local", "401"), ("/signup", "404"),
                             ("/accept/abc", "400"), ("/reset/abc", "400")):
            self.assertEqual(self.outcome("POST", path, "anon", {}), status, path)
        # QUIRK: /login/totp without a pending sign-in redirects to /login (a GET-style response to a POST).
        self.assertEqual(self.outcome("POST", "/login/totp", "anon", {}), "303 ~/login")

    def test_account_settings_posts_refuse_non_local_sessions(self):
        # QUIRK: GET /settings/account redirects non-local sessions to /settings/general, but the matching
        # POSTs answer 403 (different handling for the same condition).
        for path in ("/settings/account/password", "/settings/account/totp/confirm",
                     "/settings/account/totp/disable", "/settings/account/totp/recovery/reset"):
            self.assertEqual(self.outcome("POST", path, "admin", {"csrf": "tok"}), "403", path)

    def test_unknown_post_path_depends_on_the_role(self):
        # QUIRK: a path that does not exist is 403 for non-admins (the admin gate runs first) and 404 only for admins.
        self.assertEqual(self.outcome("POST", "/nope", "member", {"csrf": "tok"}), "403")
        self.assertEqual(self.outcome("POST", "/nope", "admin", {"csrf": "tok"}), "404")

    def test_a_forced_password_change_only_allows_logout_password_and_language(self):
        session = dict(ADMIN, kind="local", must_change=True)
        blocked = f"{B}/settings/account?m=must-change"
        with mock.patch.object(app.Handler, "session", lambda self: dict(session)):
            for path in ("/keys", "/users", "/machines/7/delete"):
                status, headers, _ = request("POST", B + path, form={"csrf": "tok"})
                self.assertEqual((status, location(headers)), (303, blocked), path)
            for path, loc in (("/logout", f"{B}/login?m=signed-out"), ("/settings/language", f"{B}/settings/general")):
                status, headers, _ = request("POST", B + path, form={"csrf": "tok"})
                self.assertEqual((status, location(headers)), (303, loc), path)

    def test_language_cookie_only_accepts_a_known_language(self):
        _, headers, _ = request("POST", f"{B}/settings/language", ADMIN, {"csrf": "tok", "lang": "es"})
        self.assertTrue(any(c.startswith("hse_lang=es;") for c in headers["set-cookie"]))
        _, headers, _ = request("POST", f"{B}/settings/language", ADMIN, {"csrf": "tok", "lang": "xx"})
        self.assertTrue(any(c.startswith("hse_lang=;") for c in headers["set-cookie"]))


class RoleMatrixFacts(RouteCase):
    """The access rules the tables imply, stated once in words (they can only fail if a table row does too)."""

    def test_who_reads_what(self):
        # QUIRK: GET /dns is 200 for every signed-in role (view only; saving is network-admin/admin).
        self.assertEqual({r: self.outcome("GET", "/dns", r) for r in ROLES[1:]},
                         {"member": "200", "admin": "200", "auditor": "200", "netadmin": "200"})
        # Auditors read everything an admin reads except /backups; network admins read only /acl of the admin pages.
        self.assertEqual({r: self.outcome("GET", "/backups", r) for r in ROLES[1:]},
                         {"member": "403", "admin": "200", "auditor": "403", "netadmin": "403"})
        self.assertEqual({r: self.outcome("GET", "/acl", r) for r in ROLES[1:]},
                         {"member": "403", "admin": "200", "auditor": "200", "netadmin": "200"})
        self.assertEqual({r: self.outcome("GET", "/users", r) for r in ROLES[1:]},
                         {"member": "403", "admin": "200", "auditor": "200", "netadmin": "403"})

    def test_who_writes_what(self):
        # ACL and DNS: admin and network admin; the auditor may only run the simulator (/acl/test).
        for path in ("/acl", "/dns", "/acl/rules"):
            self.assertEqual(self.outcome("POST", path, "auditor", {"csrf": "tok"}), "403", path)
        self.assertEqual(self.outcome("POST", "/acl/test", "auditor", {"csrf": "tok"}), "200")
        self.assertEqual(self.outcome("POST", "/acl/test", "netadmin", {"csrf": "tok"}), "200")
        # Everything else under "admins only" refuses the network admin and the auditor.
        for path in ("/users", "/derp", "/backups/run", "/apikeys", "/machines/register"):
            for role in ("auditor", "netadmin"):
                self.assertEqual(self.outcome("POST", path, role, {"csrf": "tok"}), "403", (path, role))


if __name__ == "__main__":
    unittest.main()
