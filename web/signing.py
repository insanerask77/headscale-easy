"""HMAC-signed cookie payloads."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def sign(data: dict, secret: bytes) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    mac = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def unsign(value: str | None, secret: bytes) -> dict | None:
    if not value or "." not in value:
        return None
    payload, mac = value.rsplit(".", 1)
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    return data if data.get("exp", 0) >= time.time() else None
