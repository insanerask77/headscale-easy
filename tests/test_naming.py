"""Unit tests for the automatic renaming of "localhost" machines (web/naming.py).

Standard library only:  python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import naming  # noqa: E402


class IntervalCase(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("RENAME_INTERVAL", None)

    def test_default_is_five_seconds(self):
        os.environ.pop("RENAME_INTERVAL", None)
        self.assertEqual(naming._interval(), 5.0)

    def test_from_env(self):
        os.environ["RENAME_INTERVAL"] = "12"
        self.assertEqual(naming._interval(), 12.0)

    def test_invalid_or_too_small(self):
        os.environ["RENAME_INTERVAL"] = "abc"
        self.assertEqual(naming._interval(), 5.0)
        os.environ["RENAME_INTERVAL"] = "0"
        self.assertEqual(naming._interval(), 1.0)


class RenameCase(unittest.TestCase):
    NODES = [
        {"id": 1, "givenName": "localhost", "user": {"name": "ana@x.com"}},
        {"id": 2, "givenName": "bobs-laptop", "user": {"name": "bob"}},
        {"id": 3, "givenName": "localhost-ab12", "user": {"name": "leo"}},
    ]
    DETAILS = {"1": {"hostinfo": {"OS": "iOS"}}, "3": {"hostinfo": {"OS": "macOS"}}}

    def run_pass(self, nodes):
        with mock.patch.object(naming.hs, "all_nodes", return_value=nodes), \
             mock.patch.object(naming.hs, "host_details", return_value=self.DETAILS) as det, \
             mock.patch.object(naming.hs, "api") as api, \
             mock.patch.object(naming.audit, "record"):
            count = naming.rename_placeholders()
        return count, det, api

    def test_only_placeholders_are_renamed(self):
        count, det, api = self.run_pass(self.NODES)
        self.assertEqual(count, 2)
        det.assert_called_once_with(["1", "3"])
        paths = sorted(c.args[1] for c in api.call_args_list)
        self.assertEqual(paths, ["/node/1/rename/ana-iphone", "/node/3/rename/leo-mac"])

    def test_nothing_to_do_skips_host_details(self):
        count, det, api = self.run_pass([self.NODES[1]])
        self.assertEqual(count, 0)
        det.assert_not_called()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
