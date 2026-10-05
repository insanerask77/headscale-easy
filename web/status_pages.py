"""Server status page (Settings > Status), for admins and auditors. The data
comes from status.py."""

from __future__ import annotations

import status
from i18n import _, ngettext
from ui import BASE, badge, csrf_input, esc, flash_html, layout, page_head, parse_time, time_tag


def _kv(label: str, value: str) -> str:
    return f'<div class="kv"><dt>{esc(label)}</dt><dd>{value}</dd></div>'


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _version_row(label: str, info: dict, enabled: bool) -> str:
    current, latest = info["version"], info["latest"]
    cell = f"<code>{esc(current)}</code>" if current else f'<span class="muted">{esc(_("Unknown"))}</span>'
    if status.is_newer(latest, current):
        cell += " " + badge(_("Update available: {v}", v=latest), "orange")
    elif latest and current:
        cell += " " + badge(_("Up to date"), "green")
    elif enabled:
        cell += " " + badge(_("Could not check for updates"), "")
    return _kv(label, cell)


def _containers(rows: list[dict], helper: dict | None) -> str:
    if helper is None:
        return f'<p class="muted">{esc(_("The Docker helper (hs-helper) is not answering, so the containers cannot be listed."))}</p>'
    if not helper.get("docker"):
        return f'<p class="muted">{esc(_("The Docker helper cannot reach Docker."))}</p>'
    if not rows:
        return f'<p class="muted">{esc(_("No containers found."))}</p>'
    body = ""
    for c in rows:
        ok = c.get("state") == "running" and c.get("health") in (None, "healthy")
        kind = "green" if ok else ("orange" if c.get("health") == "starting" else "red")
        label = c.get("health") or c.get("state") or "?"
        body += (f"<tr><td>{esc(c.get('service') or c.get('name'))}</td><td><code>{esc(c.get('image'))}</code></td>"
                 f"<td>{badge(label, kind)}</td><td class=\"muted\">{esc(c.get('status'))}</td></tr>")
    return f"""<div class="table-wrap"><table class="simple">
      <thead><tr><th>{esc(_("Service"))}</th><th>{esc(_("Image"))}</th><th>{esc(_("Health"))}</th><th>{esc(_("Status"))}</th></tr></thead>
      <tbody>{body}</tbody></table></div>"""


def _when(value: str | None) -> str:
    """A backup time: a <time> tag when it parses, the raw text otherwise."""
    if parse_time(value) is None:
        return esc(value) if value else "—"
    return time_tag(value)


def _backups(backup: dict | None, session: dict) -> str:
    """The Backups card (all-in-one image). None: the helper reports no `backup`
    key (1.x compose, or no helper), so there is nothing to show. The card is
    data-live: app.js refreshes it every few seconds, which also shows a manual
    run finishing."""
    if backup is None:
        return ""
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
        rows += _kv(_("Schedule"), f'<span class="muted">{esc(_("Off (BACKUP_SCHEDULE=off)"))}</span>')
    rows += _kv(_("Kept"), esc(ngettext("{n} day", "{n} days", int(backup.get("keep_days") or 0))) +
                (f' · {esc(ngettext("{n} backup", "{n} backups", int(backup.get("count") or 0)))} · {esc(_size(backup.get("bytes") or 0))}'
                 if backup.get("count") else ""))
    rows += _kv(_("Location"), "<code>/data/backups</code>")
    action = ""
    if session.get("admin"):
        busy = " disabled" if running else ""
        action = (f'<form method="post" action="{BASE}/settings/status/backup" data-busy>{csrf_input(session)}'
                  f'<button class="btn" type="submit"{busy}>{esc(_("Back up now"))}</button></form>')
    hint = (f'<p class="muted small">{esc(_("Backups hold every secret of this server (accounts, keys, settings). Keep them off this disk, for example on a mounted NAS folder, and out of reach of other people."))}</p>')
    return (f'<section class="card" data-live="backup"><h2>{esc(_("Backups"))}</h2>'
            f'<dl class="kvs">{rows}</dl>{action}{hint}</section>')


def _disks(disks: list) -> str:
    if not disks:
        return f'<p class="muted">{esc(_("Disk use is not available."))}</p>'
    labels = {"data": _("Headscale Easy data"), "headscale": _("Headscale database")}
    rows = ""
    for key, d in disks:
        kind = "red" if d["percent"] >= 90 else "orange" if d["percent"] >= 80 else "green"
        pct = str(d["percent"]) + " %"
        rows += (f"<tr><td>{esc(labels.get(key, key))}</td><td><code>{esc(d['path'])}</code></td>"
                 f"<td>{esc(_size(d['used']))} / {esc(_size(d['total']))}</td><td>{badge(pct, kind)}</td></tr>")
    return f"""<div class="table-wrap"><table class="simple">
      <thead><tr><th>{esc(_("Volume"))}</th><th>{esc(_("Path"))}</th><th>{esc(_("Used"))}</th><th></th></tr></thead>
      <tbody>{rows}</tbody></table></div>"""


def _metrics(metrics: dict | None, online: tuple[int, int] | None) -> str:
    rows = ""
    if online:
        rows += _kv(_("Devices online"), esc(_("{online} of {total}", online=online[0], total=online[1])))
    note = ""
    if metrics is None:
        note = f'<p class="muted">{esc(_("Headscale metrics are not reachable."))}</p>'
    else:
        if metrics.get("requests") is not None:
            rows += _kv(_("Requests served"), esc(f"{metrics['requests']:.0f}"))
        if metrics.get("goroutines") is not None:
            rows += _kv(_("Goroutines"), esc(f"{metrics['goroutines']:.0f}"))
        if metrics.get("memory") is not None:
            rows += _kv(_("Memory"), esc(_size(metrics["memory"])))
    return (f'<dl class="kvs">{rows}</dl>' if rows else "") + note


def status_page(session: dict, ctx: dict, data: dict, flash: str = "") -> str:
    enabled = data["update_check"]
    off = "" if enabled else f'<p class="muted small">{esc(_("The update check is off (STATUS_UPDATE_CHECK=false)."))}</p>'
    body = page_head(_("Status"), esc(_("The health of this server at a glance."))) + flash_html(flash) + f"""
    <section class="card"><h2>{esc(_("Versions"))}</h2>
      <dl class="kvs">{_version_row("Headscale", data["headscale"], enabled)}{_version_row("Headscale Easy", data["easy"], enabled)}</dl>
      <p class="muted small">{esc(_("The latest releases are looked up on GitHub and cached for 12 hours."))}</p>{off}</section>
    <section class="card"><h2>{esc(_("Containers"))}</h2>{_containers(data["containers"], data["helper"])}</section>
    {_backups(data.get("backup"), session)}
    <section class="card"><h2>{esc(_("Disk"))}</h2>{_disks(data["disks"])}</section>
    <section class="card"><h2>{esc(_("Headscale"))}</h2>{_metrics(data["metrics"], data["online"])}</section>"""
    return layout(_("Status"), "status", body, session, ctx)
