"""Parsing of the ISO timestamps Headscale returns."""

from __future__ import annotations

from datetime import datetime


def parse_time(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
