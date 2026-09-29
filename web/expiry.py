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
machines" dialog) live here too so pages.py only gains a few lines.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from i18n import _, ngettext
from ui import BASE, csrf_input, esc, notice, parse_time, relative


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

def soon_badge_tip(node: dict) -> str:
    return _("The key expires {when}. Sign in again on the device (or disable key expiry) to keep it connected.",
             when=relative(parse_time(node.get("expiry")), future=True))


def filter_options() -> str:
    """Two more checkboxes for the Filters menu (app.js filterRows)."""
    return (f'<label class="check"><input type="checkbox" data-f="expiring"><span>{esc(_("Expiring soon"))}</span></label>'
            f'<label class="check"><input type="checkbox" data-f="inactive">'
            f'<span>{esc(_("Offline for {days}+ days", days=INACTIVE_DAYS))}</span></label>')


def notice_html(nodes: list[dict], admin: bool = False, now: datetime | None = None) -> str:
    """Warning at the top of the list: machines that expire soon or expired
    and, for admins, inactive ones with the "Remove inactive machines…" action."""
    counts = summary(nodes, now)
    lines = []
    if counts["soon"]:
        text = ngettext("{n} machine expires in the next {days} days.",
                        "{n} machines expire in the next {days} days.", counts["soon"], days=EXPIRY_WARNING_DAYS)
        lines.append(f'<p>{esc(text)} <button type="button" class="link" data-f-apply="expiring">'
                     f'{esc(_("Show only these"))}</button></p>')
    if counts["expired"]:
        text = ngettext("{n} machine has an expired key and cannot connect until it signs in again.",
                        "{n} machines have an expired key and cannot connect until they sign in again.",
                        counts["expired"])
        lines.append(f'<p>{esc(text)} <button type="button" class="link" data-f-apply="expired">'
                     f'{esc(_("Show only these"))}</button></p>')
    if admin and counts["inactive"]:
        text = ngettext("{n} machine has been offline for more than {days} days.",
                        "{n} machines have been offline for more than {days} days.",
                        counts["inactive"], days=INACTIVE_DAYS)
        lines.append(f'<p>{esc(text)} <button type="button" class="link" data-f-apply="inactive">'
                     f'{esc(_("Show only these"))}</button> · <button type="button" class="link" '
                     f'data-open="remove-inactive">{esc(_("Remove inactive machines…"))}</button></p>')
    return f'<div class="notice warn expiry-notice" role="status">{"".join(lines)}</div>' if lines else ""


def remove_inactive_dialog(machines: list, session: dict) -> str:
    """Confirmation listing every inactive machine, all ticked."""
    inactive = [m for m in machines if m.inactive]
    if not inactive:
        return ""
    rows = "".join(
        f'<label class="check"><input type="checkbox" name="node-{esc(m.id)}" value="1" checked>'
        f'<span>{esc(m.name)}<span class="muted">{esc(m.owner_label)} · '
        f'{esc(_("Last seen {when}", when=relative(offline_since(m.node))))}</span></span></label>'
        for m in sorted(inactive, key=lambda m: offline_since(m.node) or datetime.min.replace(tzinfo=timezone.utc)))
    text = _("These machines have been offline for more than {days} days. The selected ones are removed from "
             "the tailnet; to use one again it has to be connected again.", days=INACTIVE_DAYS)
    return f"""
    <dialog id="remove-inactive">
      <form method="post" action="{BASE}/machines/remove-inactive">{csrf_input(session)}
        <h3>{esc(_("Remove inactive machines"))}</h3>
        <p class="muted">{esc(text)}</p>
        <div class="inactive-list">{rows}</div>
        <div class="dialog-actions"><button type="button" class="btn" data-close>{esc(_("Cancel"))}</button>
          <button class="btn danger-solid" type="submit">{esc(_("Remove selected"))}</button></div>
      </form>
    </dialog>"""


def selected_ids(form: dict) -> set[str]:
    """Node ids ticked in the dialog (fields named node-<id>)."""
    return {k[5:] for k in form if re.fullmatch(r"node-\d+", k)}


def flash_html(code: str) -> str:
    """Messages after "Remove inactive machines" (codes made by remove_result)."""
    m = re.fullmatch(r"inactive-removed-(\d+)", code or "")
    if m:
        n = int(m.group(1))
        return notice("ok", ngettext("{n} inactive machine removed.", "{n} inactive machines removed.", n))
    if code == "inactive-none":
        return notice("error", _("Nothing was removed: none of the selected machines is inactive any more."))
    return ""


def remove_result(removed: int, failed: int) -> str:
    if failed:
        return "failed"
    return f"inactive-removed-{removed}" if removed else "inactive-none"
