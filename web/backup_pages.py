"""The Backups page (all-in-one image): state, schedule, the list with Download / Restore, and Upload.

Everything here is for administrators. The page is built from the supervisor's ``backup`` summary
(control socket, GET /status); with no such key (the supervisor is not answering) the menu entry is not shown.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from i18n import _, ngettext
from ui import dialog
from status_pages import _kv, _size
from ui import (BASE, LOGO, badge, bare_page, csrf_input, esc, flash_html, layout, page_head, parse_time,
                time_tag)

UPLOAD_MAX_MB = int(os.environ.get("BACKUP_UPLOAD_MAX_MB") or 1024)


def _when(value) -> str:
    """A backup time: a <time> tag when it parses, the raw text otherwise. backup.py writes epoch seconds,
    the supervisor ISO 8601: take both, and never fail the page over a odd value."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            value = datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return "—"
    if value is not None and not isinstance(value, str):
        return "—"
    if parse_time(value) is None:
        return esc(value) if value else "—"
    return time_tag(value)


def _state_card(backup: dict) -> str:
    """Last backup, next run, what is kept, and Back up now. data-live: app.js refreshes it every few seconds,
    which also shows a manual run finishing."""
    last, running = backup.get("last") or None, bool(backup.get("running"))
    if running:
        state = badge(_("Running"), "orange")
    elif last is None:
        state = badge(_("Never"), "orange")
    elif last.get("ok"):
        state = badge(_("OK"), "green")
    else:
        state = badge(_("Failed"), "red")
    rows = _kv(_("Last backup"), state + (f' {_when(last.get("at"))}' if last else ""))
    if last and last.get("ok"):
        rows += _kv(_("Size"), f'{esc(_size(last.get("size") or 0))} · <code>{esc(last.get("file") or "")}</code>')
    if last and not last.get("ok") and last.get("error"):
        rows += _kv(_("Error"), esc(str(last["error"])[:300]))
    good = backup.get("last_ok")
    if good and last and not last.get("ok"):
        rows += _kv(_("Last good backup"), _when(good.get("at")))
    if backup.get("enabled"):
        schedule = f'<code>{esc(backup.get("schedule") or "")}</code>'
        rows += _kv(_("Schedule"), schedule + (f' · {esc(_("next"))} {_when(backup.get("next_run"))}'
                                              if backup.get("next_run") else ""))
    else:
        rows += _kv(_("Schedule"), f'<span class="muted">{esc(_("Off"))}</span>')
    rows += _kv(_("Kept"), esc(ngettext("{n} day", "{n} days", int(backup.get("keep_days") or 0))) +
                (f' · {esc(ngettext("{n} backup", "{n} backups", int(backup.get("count") or 0)))} · {esc(_size(backup.get("bytes") or 0))}'
                 if backup.get("count") else ""))
    rows += _kv(_("Location"), "<code>/data/backups</code>")
    return (f'<section class="card" data-live="backup"><h2>{esc(_("Status"))}</h2>'
            f'<dl class="kvs">{rows}</dl></section>')


def _run_form(backup: dict, session: dict) -> str:
    """Back up now. Outside the live card: that card is replaced every few seconds."""
    busy = " disabled" if backup.get("running") else ""
    return (f'<form method="post" action="{BASE}/backups/run" data-busy>{csrf_input(session)}'
            f'<button class="btn primary" type="submit"{busy}>{esc(_("Back up now"))}</button></form>')


def _settings_card(backup: dict, session: dict) -> str:
    """On/off, schedule, days kept. Not data-live: the live card is refreshed every few seconds and that would
    wipe what is being typed here."""
    locked = backup.get("env_locked") or {}
    enabled = bool(backup.get("enabled"))
    schedule = backup.get("schedule") or ""
    if not enabled or schedule == "off":
        schedule = "0 3 * * *"
    dis_sched = " disabled" if locked.get("schedule") else ""
    dis_keep = " disabled" if locked.get("keep_days") else ""
    on, off = (" selected", "") if enabled else ("", " selected")
    note = ""
    if locked.get("schedule") or locked.get("keep_days"):
        note = (f'<p class="muted small">{esc(_("Some of these values are fixed by environment variables (BACKUP_SCHEDULE / BACKUP_KEEP_DAYS) and can only be changed there."))}</p>')
    button = "" if (locked.get("schedule") and locked.get("keep_days")) else (
        f'<div><button class="btn primary" type="submit">{esc(_("Save"))}</button></div>')
    return f"""
    <section class="card">
      <h2>{esc(_("Schedule"))}</h2>
      <form method="post" action="{BASE}/backups/settings" class="stack">{csrf_input(session)}
        <label class="field">{esc(_("Scheduled backups"))}
          <select name="backup_enabled"{dis_sched}>
            <option value="on"{on}>{esc(_("On"))}</option><option value="off"{off}>{esc(_("Off"))}</option></select></label>
        <label class="field">{esc(_("Schedule (cron)"))}
          <span class="muted small">{esc(_("Five fields: minute hour day month weekday. For example 0 3 * * * is every day at 03:00. The server time zone is TZ."))}</span>
          <input type="text" name="backup_schedule" value="{esc(schedule)}" spellcheck="false" autocomplete="off"{dis_sched}></label>
        <label class="field">{esc(_("Days to keep backups"))}
          <span class="muted small">{esc(_("Older backups are deleted. The newest one is never deleted."))}</span>
          <span class="inline"><input type="number" name="backup_keep_days" min="1" max="3650" value="{int(backup.get("keep_days") or 14)}" class="short"{dis_keep}> {esc(_("days"))}</span></label>
        {note}{button}
      </form>
    </section>"""


def _files_card(backup: dict, session: dict) -> str:
    """The archives in /data/backups with Download and Restore. Restoring replaces all the data, so it opens a
    dialog that asks to type RESTORE. Not data-live: a confirmation being typed must survive the refreshes."""
    files = backup.get("files")
    if files is None:
        return ""
    if not files:
        body = f'<p class="muted">{esc(_("There are no backups yet."))}</p>'
    else:
        rows, dialogs = "", ""
        for i, f in enumerate(files):
            name = esc(f.get("name", ""))
            hidden = f'<input type="hidden" name="name" value="{name}">'
            rows += (
                f'<tr><td><code>{name}</code></td><td class="nowrap">{_when(f.get("mtime"))}</td>'
                f'<td class="nowrap">{esc(_size(f.get("size") or 0))}</td><td class="actions">'
                f'<form method="post" action="{BASE}/backups/download">{csrf_input(session)}{hidden}'
                f'<button class="btn" type="submit">{esc(_("Download"))}</button></form>'
                f'<button type="button" class="btn inv-danger" data-open="restore-{i}">{esc(_("Restore"))}…</button></td></tr>')
            dialogs += dialog(
                f"restore-{i}", _("Restore this backup?"),
                esc(_("This replaces every user, device, account and setting with what this backup holds. The current data is saved first and put back if the restore fails. The console restarts and you may have to sign in again.")),
                f"{BASE}/backups/restore", session, submit=_("Restore this backup"), danger=True,
                fields=(f'<p><code>{name}</code></p>{hidden}'
                        f'<label class="field">{esc(_("Type RESTORE to confirm"))}'
                        f'<input type="text" name="confirm" required pattern="RESTORE" autocomplete="off" '
                        f'spellcheck="false" placeholder="RESTORE"></label>'))
        body = (f'<div class="table-wrap"><table class="simple"><thead><tr><th>{esc(_("Backup"))}</th>'
                f'<th>{esc(_("Date"))}</th><th>{esc(_("Size"))}</th><th></th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>{dialogs}')
    return (f'<section class="card"><h2>{esc(_("Available backups"))}</h2>{body}'
            f'<p class="muted small">{esc(_("A backup holds every secret of this server. Download it only to a safe place."))}</p></section>')


def _upload_card(session: dict) -> str:
    """Upload a backup made elsewhere (another server, or a copy kept off this one), and optionally restore it
    at once. The CSRF field comes first: the server checks it before it accepts the file."""
    return f"""
    <section class="card">
      <h2>{esc(_("Upload a backup"))}</h2>
      <form method="post" action="{BASE}/backups/upload" enctype="multipart/form-data" class="stack">{csrf_input(session)}
        <p class="muted">{esc(_("Have a backup from another server or kept elsewhere? Upload it to see it in the list, or upload it and restore it in one step."))}</p>
        <label class="field">{esc(_("Backup file"))}
          <span class="muted small">{esc(_("A .tar.gz made by Headscale Easy (all-in-one image), up to {mb} MB. It is checked before it is kept.", mb=UPLOAD_MAX_MB))}</span>
          <input type="file" name="file" accept=".gz,.tgz,application/gzip" required></label>
        <div class="form-actions">
          <button class="btn primary" type="submit" name="action" value="upload">{esc(_("Upload"))}</button>
          <button class="btn inv-danger" type="button" data-open="upload-restore">{esc(_("Upload and restore"))}…</button>
        </div>
        <dialog id="upload-restore">
          <h3>{esc(_("Upload and restore"))}</h3>
          <p class="muted">{esc(_("This replaces every user, device, account and setting with what the file holds. The current data is saved first and put back if the restore fails. The console restarts and you may have to sign in again."))}</p>
          <label class="field">{esc(_("Type RESTORE to confirm"))}
            <input type="text" name="confirm" autocomplete="off" spellcheck="false" placeholder="RESTORE"></label>
          <div class="dialog-actions"><button type="button" class="btn" data-close>{esc(_("Cancel"))}</button>
            <button class="btn danger-solid" type="submit" name="action" value="restore">{esc(_("Upload and restore"))}</button></div>
        </dialog>
      </form>
    </section>"""


def backups_page(session: dict, ctx: dict, backup: dict | None, flash: str = "") -> str:
    if backup is None:
        body = (page_head(_("Backups"), _("Scheduled backups of this server, with download and restore.")) +
                flash_html(flash) + f'<section class="card"><p class="muted">{esc(_("Backups are not available here: the supervisor of the all-in-one image is not answering."))}</p></section>')
    else:
        actions = _run_form(backup, session)
        body = (page_head(_("Backups"), _("Scheduled backups of this server, with download and restore."), actions) +
                flash_html(flash) + _state_card(backup) + _settings_card(backup, session) +
                _files_card(backup, session) + _upload_card(session) +
                f'<p class="muted small">{esc(_("Backups hold every secret of this server (accounts, keys, settings). Keep them off this disk, for example on a mounted NAS folder, and out of reach of other people."))}</p>')
    return layout(_("Backups"), "backups", body, session, ctx)


def restoring_page(restore_id: str) -> str:
    """Shown while the supervisor stops the console, restores and starts it again (app.js waits for it)."""
    rid = restore_id if re.fullmatch(r"[0-9.]{1,32}", restore_id or "") else ""
    return bare_page(_("Restoring backup"), f"""
    <section class="card narrow center login setup" data-await-restore="{BASE}/backups?m=backup-restore-done"
             data-probe="{BASE}/restore-status?id={rid}">
      <div class="big-logo">{LOGO}</div>
      <h1>{esc(_("Restoring backup"))}</h1>
      <p class="muted" data-await-waiting>{esc(_("The backup is being restored and the console restarts. Do not close this page: it continues by itself when it is done."))}</p>
      <p class="muted small" data-await-slow hidden>{esc(_("The restore is taking longer than expected. Check the container logs; this page can be reloaded."))}</p>
    </section>""")
