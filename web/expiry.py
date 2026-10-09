"""Expiry warnings and inactive machines (like Tailscale's "Expires soon").

The rules work on plain Headscale node dicts (the API's JSON), so they are easy
to test and to reuse: a future email or webhook notifier only needs
expiring_nodes() / inactive_nodes() over hs.all_nodes().

  - A machine "expires soon" when its key expires within EXPIRY_WARNING_DAYS
    (default 14) and has not expired yet. Expiry disabled = never.
  - A machine is "inactive" when it is offline and was last seen more than
    INACTIVE_DAYS (default 30) ago; one that never connected counts from the
    day it was registered.

The page pieces (notice, filters' flash messages, the "Remove inactive
machines" dialog) live in expiry_pages.py.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from timeparse import parse_time


def _days_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


EXPIRY_WARNING_DAYS = _days_env("EXPIRY_WARNING_DAYS", 14)
INACTIVE_DAYS = _days_env("INACTIVE_DAYS", 30)


# -----------------------------------------------------------------------------
# Rules (node dicts in, plain values out)
# -----------------------------------------------------------------------------

def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def expiry_state(node: dict, now: datetime | None = None, days: int | None = None) -> str:
    """'disabled', 'expired', 'soon' or 'ok'."""
    expiry = parse_time(node.get("expiry"))
    if expiry is None:
        return "disabled"
    now = _now(now)
    if expiry < now:
        return "expired"
    window = EXPIRY_WARNING_DAYS if days is None else days
    return "soon" if expiry <= now + timedelta(days=window) else "ok"


def expires_soon(node: dict, now: datetime | None = None, days: int | None = None) -> bool:
    return expiry_state(node, now, days) == "soon"


def offline_since(node: dict) -> datetime | None:
    """When an offline machine was last seen (its registration if never)."""
    if node.get("online"):
        return None
    return parse_time(node.get("lastSeen")) or parse_time(node.get("createdAt"))


def is_inactive(node: dict, now: datetime | None = None, days: int | None = None) -> bool:
    since = offline_since(node)
    if node.get("online") or since is None:
        return False
    window = INACTIVE_DAYS if days is None else days
    return since < _now(now) - timedelta(days=window)


def expiring_nodes(nodes: list[dict], now: datetime | None = None) -> list[dict]:
    return [n for n in nodes if expires_soon(n, now)]


def inactive_nodes(nodes: list[dict], now: datetime | None = None) -> list[dict]:
    return [n for n in nodes if is_inactive(n, now)]


def summary(nodes: list[dict], now: datetime | None = None) -> dict[str, int]:
    """How many machines expire soon, already expired or are inactive."""
    now = _now(now)
    states = [expiry_state(n, now) for n in nodes]
    return {"soon": states.count("soon"), "expired": states.count("expired"),
            "inactive": sum(is_inactive(n, now) for n in nodes)}


# -----------------------------------------------------------------------------
# Page pieces (Machines page)
# -----------------------------------------------------------------------------


def selected_ids(form: dict) -> set[str]:
    """Node ids ticked in the dialog (fields named node-<id>)."""
    return {k[5:] for k in form if re.fullmatch(r"node-\d+", k)}


def remove_result(removed: int, failed: int) -> str:
    if failed:
        return "failed"
    return f"inactive-removed-{removed}" if removed else "inactive-none"
