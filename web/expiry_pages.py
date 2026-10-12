"""Expiry warnings and the "Remove inactive machines" dialog (HTML for the rules in expiry.py)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from i18n import _, ngettext
from ui import BASE, csrf_input, esc, notice, parse_time, relative
import expiry


def soon_badge_tip(node: dict) -> str:
    return _("The key expires {when}. Sign in again on the device (or disable key expiry) to keep it connected.",
             when=relative(parse_time(node.get("expiry")), future=True))


def filter_options() -> str:
    """Two more checkboxes for the Filters menu (app.js filterRows)."""
    return (f'<label class="check"><input type="checkbox" data-f="expiring"><span>{esc(_("Expiring soon"))}</span></label>'
            f'<label class="check"><input type="checkbox" data-f="inactive">'
            f'<span>{esc(_("Offline for {days}+ days", days=expiry.INACTIVE_DAYS))}</span></label>')


def notice_html(nodes: list[dict], admin: bool = False, now: datetime | None = None) -> str:
    """Warning at the top of the list: machines that expire soon or expired
    and, for admins, inactive ones with the "Remove inactive machines…" action."""
    counts = expiry.summary(nodes, now)
    lines = []
    if counts["soon"]:
        text = ngettext("{n} machine expires in the next {days} days.",
                        "{n} machines expire in the next {days} days.", counts["soon"], days=expiry.EXPIRY_WARNING_DAYS)
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
                        counts["inactive"], days=expiry.INACTIVE_DAYS)
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
        f'{esc(_("Last seen {when}", when=relative(expiry.offline_since(m.node))))}</span></span></label>'
        for m in sorted(inactive, key=lambda m: expiry.offline_since(m.node) or datetime.min.replace(tzinfo=timezone.utc)))
    text = _("These machines have been offline for more than {days} days. The selected ones are removed from "
             "the tailnet; to use one again it has to be connected again.", days=expiry.INACTIVE_DAYS)
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


def flash_html(code: str) -> str:
    """Messages after "Remove inactive machines" (codes made by remove_result)."""
    m = re.fullmatch(r"inactive-removed-(\d+)", code or "")
    if m:
        n = int(m.group(1))
        return notice("ok", ngettext("{n} inactive machine removed.", "{n} inactive machines removed.", n))
    if code == "inactive-none":
        return notice("error", _("Nothing was removed: none of the selected machines is inactive any more."))
    return ""
