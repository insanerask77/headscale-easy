"""Self-registration: the sign-up mode (off / invite / open), its page and the
admin's side of it (invitation keys, the mode control in Settings).

The mode lives in a small file the console owns (SIGNUP_MODE_FILE), so an admin
can change it without restarting; HSE_SIGNUP is the starting value. Anything
unreadable or unknown means "off": nobody can register unless an admin turned
it on."""

from __future__ import annotations

import os
import re
import tempfile


MODES = ("off", "invite", "open")
MODE_FILE = os.environ.get("SIGNUP_MODE_FILE", "/data/signup-mode")
START = os.environ.get("HSE_SIGNUP", "off").strip().lower()
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KEY_DAYS = ("1", "7", "30", "0")  # 0 = never expires
KEY_USES = ("1", "5", "25", "0")  # 0 = unlimited


def mode() -> str:
    try:
        with open(MODE_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        value = START
    return value if value in MODES else "off"


def set_mode(value: str) -> bool:
    """Save the mode; True if it changed."""
    if value not in MODES:
        raise ValueError("unknown sign-up mode")
    changed = value != mode()
    parent = os.path.dirname(MODE_FILE) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, MODE_FILE)
    return changed


# -----------------------------------------------------------------------------
# Public page
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Admin: the control in Settings and the keys on the Users page
# -----------------------------------------------------------------------------


