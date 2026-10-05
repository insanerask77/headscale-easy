"""Server status page (Settings > Status), for admins and auditors. The data
comes from status.py."""

from __future__ import annotations

import status
from i18n import _
from ui import BASE, badge, esc, flash_html, layout, page_head


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
    <section class="card"><h2>{esc(_("Disk"))}</h2>{_disks(data["disks"])}</section>
    <section class="card"><h2>{esc(_("Headscale"))}</h2>{_metrics(data["metrics"], data["online"])}</section>"""
    return layout(_("Status"), "status", body, session, ctx)
