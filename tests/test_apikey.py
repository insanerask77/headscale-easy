"""Characterization tests for web/apikey.py: the background renewal of the console's Headscale API key.

They document what the module does today (which key it prefers, when it renews, what it does when Headscale
refuses). Headscale is replaced by a small fake; nothing here changes behavior.

    python3 -m unittest tests.test_apikey
"""
import io
import json
import logging
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)

import apikey  # noqa: E402
import headscale as hs  # noqa: E402


def make_key(prefix: str, secret: str = "s3cret") -> str:
    assert len(prefix) == 12
    return f"hskey-api-{prefix}-{secret}"


def http_error(code: int, body: dict | None = None) -> urllib.error.HTTPError:
    payload = json.dumps(body or {}).encode()
    return urllib.error.HTTPError("http://headscale", code, "err", None, io.BytesIO(payload))


def iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeHeadscale:
    """Stands in for hs.http_json (key listing) and hs.api (create/expire)."""

    def __init__(self):
        self.entries: dict[str, dict] = {}  # bearer key -> list entry (what that key sees); absent = rejected
        self.listing: dict[str, int] = {}   # bearer key -> HTTP status to raise instead
        self.api_calls: list[tuple] = []
        self.created = make_key("NEWKEYPREFIX")
        self.expire_error: Exception | None = None

    def http_json(self, method, url, headers=None, body=None):
        key = headers["Authorization"].removeprefix("Bearer ")
        if key in self.listing:
            raise http_error(self.listing[key])
        entry = self.entries.get(key)
        return {"apiKeys": [entry] if entry else []}

    def api(self, method, path, body=None):
        self.api_calls.append((method, path, body))
        if path == "/apikey":
            return {"apiKey": self.created}
        if path == "/apikey/expire" and self.expire_error:
            raise self.expire_error
        return {}


class ApiKeyTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.key_file = os.path.join(tmp.name, "api-key")
        self.fake = FakeHeadscale()
        patches = [
            mock.patch.object(apikey, "KEY_FILE", self.key_file),
            mock.patch.object(apikey, "RENEW_BEFORE_DAYS", 15),
            mock.patch.object(apikey, "RENEW_FOR_DAYS", 90),
            mock.patch.object(apikey.hs, "http_json", self.fake.http_json),
            mock.patch.object(apikey.hs, "api", self.fake.api),
            mock.patch.object(apikey.hs, "HEADSCALE_API_KEY", "x"),
            mock.patch.object(apikey, "problem", ""),
            mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def write_saved(self, key: str):
        with open(self.key_file, "w", encoding="utf-8") as fh:
            fh.write(key + "\n")

    def entry(self, key: str, days: float | None, entry_id: str = "1") -> dict:
        e = {"id": entry_id, "prefix": apikey._prefix(key)}
        if days is not None:
            e["expiration"] = iso(timedelta(days=days))
        self.fake.entries[key] = e
        return e


class ParseTimeTests(unittest.TestCase):
    def test_empty_values_give_none(self):
        for value in (None, "", 0, {}):
            self.assertIsNone(apikey._parse_time(value))

    def test_iso_with_z_suffix(self):
        self.assertEqual(apikey._parse_time("2030-01-02T03:04:05Z"),
                         datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    def test_iso_with_offset(self):
        t = apikey._parse_time("2030-01-02T03:04:05+02:00")
        self.assertEqual(t.utcoffset(), timedelta(hours=2))

    def test_seconds_nanos_dict(self):
        self.assertEqual(apikey._parse_time({"seconds": 86400, "nanos": 5}),
                         datetime(1970, 1, 2, tzinfo=timezone.utc))

    def test_dict_without_seconds_is_the_epoch(self):
        # QUIRK: a non-empty dict without "seconds" is the Unix epoch (already expired), not "no expiry".
        self.assertEqual(apikey._parse_time({"nanos": 1}), datetime(1970, 1, 1, tzinfo=timezone.utc))

    def test_garbage_gives_none(self):
        self.assertIsNone(apikey._parse_time("not a date"))

    def test_naive_iso_stays_naive(self):
        # QUIRK: a timestamp without zone yields a naive datetime; comparing it with an aware "now" raises.
        t = apikey._parse_time("2030-01-02T03:04:05")
        self.assertIsNone(t.tzinfo)


class SavedKeyTests(ApiKeyTestCase):
    def test_missing_file_gives_empty_string(self):
        self.assertEqual(apikey._saved_key(), "")

    def test_saved_key_is_stripped(self):
        self.write_saved("  abc  ")
        self.assertEqual(apikey._saved_key(), "abc")

    def test_save_key_writes_newline_and_mode_600(self):
        apikey._save_key("abc")
        with open(self.key_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "abc\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.key_file).st_mode), 0o600)
        self.assertFalse(os.path.exists(self.key_file + ".tmp"))

    def test_save_key_replaces_previous_content(self):
        apikey._save_key("one")
        apikey._save_key("two")
        self.assertEqual(apikey._saved_key(), "two")

    def test_save_key_in_missing_directory_raises(self):
        with mock.patch.object(apikey, "KEY_FILE", os.path.join(self.key_file, "no", "api-key")):
            with self.assertRaises(OSError):
                apikey._save_key("abc")


class StatusTests(ApiKeyTestCase):
    def test_valid_key_returns_expiry_and_entry(self):
        key = make_key("AAAAAAAAAAAA")
        e = self.entry(key, 40)
        expiry, entry = apikey._status(key)
        self.assertEqual(entry, e)
        self.assertAlmostEqual((expiry - datetime.now(timezone.utc)).days, 39, delta=1)

    def test_key_without_expiration_returns_none_expiry_but_entry(self):
        key = make_key("AAAAAAAAAAAA")
        e = self.entry(key, None)
        self.assertEqual(apikey._status(key), (None, e))

    def test_expired_key_is_rejected(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, -1)
        self.assertEqual(apikey._status(key), (None, None))

    def test_401_and_403_mean_rejected(self):
        key = make_key("AAAAAAAAAAAA")
        for code in (401, 403):
            self.fake.listing[key] = code
            self.assertEqual(apikey._status(key), (None, None))

    def test_other_http_errors_propagate(self):
        key = make_key("AAAAAAAAAAAA")
        self.fake.listing[key] = 500
        with self.assertRaises(urllib.error.HTTPError):
            apikey._status(key)

    def test_key_not_in_listing_is_rejected(self):
        self.assertEqual(apikey._status(make_key("AAAAAAAAAAAA")), (None, None))

    def test_key_with_unparseable_prefix_is_rejected(self):
        # A key that is not hskey-api-... has an empty prefix and never matches an entry.
        self.fake.entries["plain"] = {"id": "1", "prefix": "anything"}
        self.assertEqual(apikey._status("plain"), (None, None))

    def test_entry_with_null_prefix_is_skipped(self):
        key = make_key("AAAAAAAAAAAA")
        self.fake.entries[key] = {"id": "1", "prefix": None}
        self.assertEqual(apikey._status(key), (None, None))


class CheckTests(ApiKeyTestCase):
    def test_no_candidates_sets_problem_and_logs(self):
        with self.assertLogs("headscale-easy", level="ERROR") as logs:
            apikey.check()
        self.assertEqual(apikey.problem, "expired")
        self.assertIn("no valid Headscale API key left", logs.output[0])
        self.assertEqual(self.fake.api_calls, [])
        self.assertEqual(hs.HEADSCALE_API_KEY, "x")

    def test_problem_is_cleared_when_a_valid_key_returns(self):
        apikey.problem = "expired"
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 60)
        self.write_saved(key)
        apikey.check()
        self.assertEqual(apikey.problem, "")

    def test_env_key_alone_is_used(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 60)
        with mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": key}):
            apikey.check()
        self.assertEqual(hs.HEADSCALE_API_KEY, key)
        self.assertEqual(self.fake.api_calls, [])

    def test_saved_key_alone_is_used(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 60)
        self.write_saved(key)
        apikey.check()
        self.assertEqual(hs.HEADSCALE_API_KEY, key)

    def test_picks_the_key_that_expires_last(self):
        saved, env = make_key("SAVEDSAVEDSA"), make_key("ENVENVENVENV")
        self.entry(saved, 50, "1")
        self.entry(env, 80, "2")
        self.write_saved(saved)
        with mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": env}):
            apikey.check()
        self.assertEqual(hs.HEADSCALE_API_KEY, env)
        self.assertEqual(self.fake.api_calls, [])

    def test_invalid_saved_key_falls_back_to_env(self):
        saved, env = make_key("SAVEDSAVEDSA"), make_key("ENVENVENVENV")
        self.entry(env, 60)
        self.write_saved(saved)  # rejected by the fake
        with mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": env}):
            apikey.check()
        self.assertEqual(hs.HEADSCALE_API_KEY, env)

    def test_same_key_in_both_places_is_checked_once(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 60)
        self.write_saved(key)
        calls = []
        original = self.fake.http_json
        with mock.patch.object(apikey.hs, "http_json", lambda *a, **k: calls.append(1) or original(*a, **k)):
            with mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": key}):
                apikey.check()
        self.assertEqual(len(calls), 1)

    def test_key_without_expiry_wins_over_any_dated_key(self):
        forever, dated = make_key("FOREVERFOREV"), make_key("DATEDDATEDDA")
        self.entry(forever, None, "1")
        self.entry(dated, 400, "2")
        self.write_saved(dated)
        with mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": forever}):
            apikey.check()
        self.assertEqual(hs.HEADSCALE_API_KEY, forever)
        self.assertEqual(self.fake.api_calls, [])  # never renewed: expiry is datetime.max

    def test_switching_key_is_logged(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 60)
        self.write_saved(key)
        with self.assertLogs("headscale-easy", level="INFO") as logs:
            apikey.check()
        self.assertTrue(any("using API key AAAAAAAAAAAA" in line for line in logs.output))

    def test_current_key_in_use_is_not_logged_again(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 60)
        self.write_saved(key)
        with mock.patch.object(apikey.hs, "HEADSCALE_API_KEY", key):
            with self.assertNoLogs("headscale-easy", level="INFO"):
                apikey.check()

    def test_no_renewal_above_the_threshold(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 16)
        self.write_saved(key)
        apikey.check()
        self.assertEqual(self.fake.api_calls, [])

    def test_renews_below_the_threshold(self):
        old = make_key("OLDOLDOLDOLD")
        self.entry(old, 10, "7")
        self.write_saved(old)
        with self.assertLogs("headscale-easy", level="INFO") as logs:
            apikey.check()
        self.assertEqual([c[:2] for c in self.fake.api_calls], [("POST", "/apikey"), ("POST", "/apikey/expire")])
        create_body = self.fake.api_calls[0][2]
        until = datetime.strptime(create_body["expiration"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertAlmostEqual((until - datetime.now(timezone.utc)).total_seconds(), 90 * 86400, delta=60)
        self.assertEqual(self.fake.api_calls[1][2], {"id": "7"})
        self.assertEqual(apikey._saved_key(), self.fake.created)
        self.assertEqual(hs.HEADSCALE_API_KEY, self.fake.created)
        self.assertTrue(any("renewed the API key: NEWKEYPREFIX" in line and "OLDOLDOLDOLD expired" in line
                            for line in logs.output))

    def test_threshold_and_duration_follow_the_module_settings(self):
        key = make_key("AAAAAAAAAAAA")
        self.entry(key, 20)
        self.write_saved(key)
        with mock.patch.object(apikey, "RENEW_BEFORE_DAYS", 30), mock.patch.object(apikey, "RENEW_FOR_DAYS", 7):
            apikey.check()
        body = self.fake.api_calls[0][2]
        until = datetime.strptime(body["expiration"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertAlmostEqual((until - datetime.now(timezone.utc)).total_seconds(), 7 * 86400, delta=60)

    def test_only_the_chosen_key_is_expired_not_the_other_candidate(self):
        saved, env = make_key("SAVEDSAVEDSA"), make_key("ENVENVENVENV")
        self.entry(saved, 5, "1")
        self.entry(env, 10, "2")  # expires last -> chosen, renewed, expired
        self.write_saved(saved)
        with mock.patch.dict(os.environ, {"HEADSCALE_API_KEY": env}):
            apikey.check()
        self.assertEqual(self.fake.api_calls[1][2], {"id": "2"})

    def test_failure_to_expire_the_old_key_is_only_a_warning(self):
        old = make_key("OLDOLDOLDOLD")
        self.entry(old, 10)
        self.write_saved(old)
        self.fake.expire_error = http_error(500, {"message": "boom"})
        with self.assertLogs("headscale-easy", level="INFO") as logs:
            apikey.check()
        self.assertTrue(any("could not expire the old one (OLDOLDOLDOLD): boom" in line for line in logs.output))
        self.assertEqual(hs.HEADSCALE_API_KEY, self.fake.created)  # the new key is still adopted
        self.assertEqual(apikey._saved_key(), self.fake.created)

    def test_failure_to_create_the_new_key_propagates_and_keeps_the_old_one(self):
        old = make_key("OLDOLDOLDOLD")
        self.entry(old, 10)
        self.write_saved(old)
        with mock.patch.object(apikey.hs, "api", side_effect=http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                apikey.check()
        self.assertEqual(apikey._saved_key(), old)
        self.assertEqual(hs.HEADSCALE_API_KEY, old)  # adopted before the renewal attempt

    def test_status_error_other_than_401_403_propagates(self):
        key = make_key("AAAAAAAAAAAA")
        self.write_saved(key)
        self.fake.listing[key] = 502
        with self.assertRaises(urllib.error.HTTPError):
            apikey.check()


class LoopAndStartTests(ApiKeyTestCase):
    def test_loop_survives_a_failing_check_and_sleeps(self):
        class Stop(Exception):
            pass

        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise Stop

        with mock.patch.object(apikey, "check", side_effect=[RuntimeError("down"), None]) as check, \
                mock.patch.object(apikey.time, "sleep", fake_sleep), \
                self.assertLogs("headscale-easy", level="WARNING") as logs:
            with self.assertRaises(Stop):
                apikey._loop()
        self.assertEqual(check.call_count, 2)
        self.assertEqual(sleeps, [apikey.CHECK_EVERY, apikey.CHECK_EVERY])
        self.assertEqual(apikey.CHECK_EVERY, 6 * 3600)
        self.assertIn("API key check failed: down", logs.output[0])

    def test_start_adopts_the_saved_key_and_starts_a_daemon_thread(self):
        self.write_saved("saved-key")
        with mock.patch.object(apikey.threading, "Thread") as thread:
            apikey.start()
        self.assertEqual(hs.HEADSCALE_API_KEY, "saved-key")
        thread.assert_called_once_with(target=apikey._loop, name="api-key", daemon=True)
        thread.return_value.start.assert_called_once_with()

    def test_start_without_a_saved_key_keeps_the_current_one(self):
        with mock.patch.object(apikey.threading, "Thread"):
            apikey.start()
        self.assertEqual(hs.HEADSCALE_API_KEY, "x")


class ModuleSettingsTests(unittest.TestCase):
    def test_environment_overrides_are_read_at_import(self):
        import importlib
        env = {"API_KEY_FILE": "/tmp/somewhere", "API_KEY_RENEW_BEFORE_DAYS": "3", "API_KEY_RENEW_FOR_DAYS": "5"}
        with mock.patch.dict(os.environ, env):
            fresh = importlib.reload(apikey)
        try:
            self.assertEqual((fresh.KEY_FILE, fresh.RENEW_BEFORE_DAYS, fresh.RENEW_FOR_DAYS),
                             ("/tmp/somewhere", 3, 5))
        finally:
            importlib.reload(apikey)

    def test_defaults(self):
        for name in ("API_KEY_FILE", "API_KEY_RENEW_BEFORE_DAYS", "API_KEY_RENEW_FOR_DAYS"):
            self.assertNotIn(name, os.environ)
        self.assertEqual((apikey.KEY_FILE, apikey.RENEW_BEFORE_DAYS, apikey.RENEW_FOR_DAYS),
                         ("/data/api-key", 15, 90))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
