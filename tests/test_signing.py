"""Signed cookie payloads (web/signing.py).

    python3 -m unittest tests.test_signing
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

import signing  # noqa: E402

SECRET = b"secret"


class SigningTest(unittest.TestCase):
    def test_round_trip(self):
        data = {"sub": "a", "exp": time.time() + 60}
        self.assertEqual(signing.unsign(signing.sign(data, SECRET), SECRET), data)

    def test_wrong_secret_or_tampering_is_rejected(self):
        value = signing.sign({"exp": time.time() + 60}, SECRET)
        self.assertIsNone(signing.unsign(value, b"other"))
        payload, mac = value.rsplit(".", 1)
        self.assertIsNone(signing.unsign(payload + "x." + mac, SECRET))

    def test_expired_or_missing_expiry_is_rejected(self):
        self.assertIsNone(signing.unsign(signing.sign({"exp": time.time() - 1}, SECRET), SECRET))
        self.assertIsNone(signing.unsign(signing.sign({}, SECRET), SECRET))  # no exp counts as expired

    def test_garbage(self):
        for value in (None, "", "nodot", "a.b"):
            self.assertIsNone(signing.unsign(value, SECRET))


if __name__ == "__main__":
    unittest.main()
