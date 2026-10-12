"""The activity log page and its CSV export (/console/logs; admins and auditors, read only)."""

from __future__ import annotations

import logging
import re
import sqlite3
import urllib.parse
from datetime import date, datetime, timedelta

import audit
from i18n import _, ngettext
from ui import BASE, badge, esc, icon, layout, notice, page_head, relative, time_tag

log = logging.getLogger("headscale-easy")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date(value: str) -> date | None:
    if not _DATE_RE.fullmatch(value or ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def filters_from(params: dict) -> dict:
    """Validated filters from the query string (q, cat, actor, from, to, page)."""
    f = {"text": str(params.get("q", "")).strip()[:200],
         "category": params.get("cat") if params.get("cat") in audit.CATEGORIES else "",
         "actor": str(params.get("actor", ""))[:200],
         "target": str(params.get("target", ""))[:300],
         "from": "", "to": ""}
    start, end = _date(params.get("from", "")), _date(params.get("to", ""))
    if start:
        f["from"], f["since"] = start.isoformat(), start.isoformat() + "T00:00:00"
    if end:
        f["to"], f["until"] = end.isoformat(), (end + timedelta(days=1)).isoformat() + "T00:00:00"
    page = str(params.get("page", "1"))
    f["page"] = max(1, min(int(page), 100_000)) if page.isdigit() else 1
    return f


def _qs(f: dict, **over) -> str:
    keys = {"q": f.get("text"), "cat": f.get("category"), "actor": f.get("actor"), "target": f.get("target"),
            "from": f.get("from"), "to": f.get("to"), "page": f.get("page") if f.get("page", 1) > 1 else ""}
    keys.update(over)
    return urllib.parse.urlencode({k: v for k, v in keys.items() if v})


def actor_label(actor: str) -> str:
    if actor == audit.SYSTEM:
        return _("Headscale Easy (automatic)")
    if actor == audit.HEADSCALE:
        return _("Headscale")
    if actor.startswith("api-key:"):
        return _("API key {prefix}…", prefix=actor[len("api-key:"):])
    if actor == "api-key":
        return _("API key")
    return actor or "—"


def _short(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "—"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_short(v)}" for k, v in value.items()) or "—"
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return _("yes") if value else _("no")
    return str(value)


def _details_html(ev: dict) -> tuple[str, str]:
    """(inline summary, dialog body or '') of an event's details."""
    d = dict(ev.get("details") or {})
    parts, full = [], ""
    if "diff" in d:
        diff_text = d.pop("diff")
        parts.append(esc(_("Lines: +{added} −{removed}", added=d.pop("added", 0),
                              removed=d.pop("removed", 0))))
        full = f'<pre class="plainpre audit-diff">{_diff_lines(diff_text)}</pre>'
    if "changed" in d:
        changed = d.pop("changed") or {}
        items = "".join(f"<li><b>{esc(k)}</b>: <span class=\"audit-from\">{esc(_short(v.get('from')))}</span> → "
                        f"<span class=\"audit-to\">{esc(_short(v.get('to')))}</span></li>"
                        for k, v in changed.items() if isinstance(v, dict))
        parts.append(esc(ngettext("{n} setting changed", "{n} settings changed", len(changed))))
        full = f'<ul class="audit-changes">{items}</ul>' if items else full
    if "from" in d or "to" in d:
        parts.append(f'<span class="audit-from">{esc(_short(d.pop("from", None)))}</span> → '
                     f'<span class="audit-to">{esc(_short(d.pop("to", None)))}</span>')
    for k, v in d.items():
        parts.append(f'<span class="muted">{esc(k)}:</span> {esc(_short(v))}')
    return " · ".join(parts), full


def _diff_lines(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        cls = ("add" if line.startswith("+") and not line.startswith("+++") else
               "del" if line.startswith("-") and not line.startswith("---") else
               "hunk" if line.startswith("@@") else "")
        out.append(f'<span class="{cls}">{esc(line)}</span>' if cls else esc(line))
    return "\n".join(out)


_CAT_KIND = {"config": "blue", "devices": "", "signin": "orange"}


def _row_html(ev: dict, labels: dict, cats: dict) -> tuple[str, str]:
    dt = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
    summary, full = _details_html(ev)
    dlg = ""
    if full:
        dlg_id = f"ev-{ev['id']}"
        summary += (f' <button type="button" class="link audit-more" data-open="{dlg_id}">'
                    f'{esc(_("View changes"))}</button>')
        dlg = f"""
    <dialog id="{dlg_id}" class="audit-dialog">
      <h3>{esc(labels.get(ev["action"], ev["action"]))}{f" · {esc(ev['target'])}" if ev["target"] else ""}</h3>
      <p class="muted small">{esc(actor_label(ev["actor"]))} · {time_tag(ev["ts"])}</p>
      {full}
      <div class="dialog-actions"><button type="button" class="btn" data-close>{esc(_("Close"))}</button></div>
    </dialog>"""
    target = ev["target"]
    if ev["ref"].startswith("node:") and ev["action"] not in ("device.removed", "machine.delete"):
        target_html = f'<a class="link" href="{BASE}/machines/{esc(ev["ref"][5:])}">{esc(target)}</a>'
    else:
        target_html = esc(target or "—")
    row = f"""
        <tr>
          <td class="audit-time"><time datetime="{esc(dt.isoformat())}" title="{esc(dt.strftime('%Y-%m-%d %H:%M:%S UTC'))}">{esc(relative(dt))}</time>
            <div class="muted small">{time_tag(ev["ts"])}</div></td>
          <td><div class="audit-action">{esc(labels.get(ev["action"], ev["action"]))}</div>
            {badge(cats.get(ev["category"], ev["category"]), _CAT_KIND.get(ev["category"], ""))}</td>
          <td>{target_html}</td>
          <td><span class="audit-actor">{esc(actor_label(ev["actor"]))}</span>
            {f'<div class="muted small"><code>{esc(ev["ip"])}</code></div>' if ev["ip"] else ""}</td>
          <td class="audit-details small">{summary or '<span class="muted">—</span>'}</td>
        </tr>"""
    return row, dlg


def page(session: dict, ctx: dict, params: dict) -> str:
    f = filters_from(params)
    labels, cats = audit.action_labels(), audit.category_labels()
    retention = (ngettext("Events are kept for {n} day.", "Events are kept for {n} days.", audit.RETENTION_DAYS)
                 if audit.RETENTION_DAYS else _("Events are kept forever."))
    head = page_head(_("Logs"), esc(_("Configuration changes, console sign-ins and device events in the tailnet.")
                                    + " " + retention))
    try:
        events, total = audit.query(f, audit.PAGE_SIZE, (f["page"] - 1) * audit.PAGE_SIZE)
        who = audit.actors()
    except (sqlite3.Error, OSError) as exc:
        log.warning("activity log unavailable: %s", exc)
        body = head + notice("error", _("The activity log is not available: Headscale Easy cannot write to its "
                                        "data folder (/data)."))
        return layout(_("Logs"), "logs", body, session, ctx)

    cat_opts = "".join(f'<option value="{c}"{" selected" if f["category"] == c else ""}>{esc(cats[c])}</option>'
                       for c in audit.CATEGORIES)
    if f["actor"] and f["actor"] not in who:
        who.append(f["actor"])
    actor_opts = "".join(f'<option value="{esc(a)}"{" selected" if f["actor"] == a else ""}>{esc(actor_label(a))}</option>'
                         for a in who)
    active = sum(1 for k in ("category", "actor", "from", "to", "target") if f[k])
    filters = f"""
      <details class="dropdown filters"{" open" if params.get("open") else ""}>
        <summary class="btn">{icon("filter")}{esc(_("Filters"))}<span class="fcount"{"" if active else " hidden"}>{active}</span>{icon("chevron-down", "chev")}</summary>
        <div class="dropdown-body filter-body">
          <label class="field">{esc(_("Category"))}<select name="cat">
            <option value="">{esc(_("All"))}</option>{cat_opts}</select></label>
          <label class="field">{esc(_("Actor"))}<select name="actor">
            <option value="">{esc(_("Anyone"))}</option>{actor_opts}</select></label>
          <label class="field">{esc(_("From (UTC)"))}<input type="date" name="from" value="{esc(f["from"])}"></label>
          <label class="field">{esc(_("To (UTC)"))}<input type="date" name="to" value="{esc(f["to"])}"></label>
          {f'<input type="hidden" name="target" value="{esc(f["target"])}">' if f["target"] else ""}
          <div class="audit-filter-actions"><button type="submit" class="btn small primary">{esc(_("Apply"))}</button>
            <a class="btn small" href="{BASE}/logs">{esc(_("Clear filters"))}</a></div>
        </div>
      </details>"""
    csv_href = f"{BASE}/logs.csv" + (f"?{_qs(f, page='')}" if _qs(f, page="") else "")
    toolbar = f"""
    <form class="toolbar audit-toolbar" method="get" action="{BASE}/logs" data-audit-filters>
      <label class="search">{icon("search")}<input type="search" name="q" value="{esc(f["text"])}" placeholder="{esc(_("Search events, actors, machines, IPs…"))}" aria-label="{esc(_("Search events"))}"></label>
      {filters}
      <span class="spacer"></span>
      <a class="icon-btn boxed" href="{esc(csv_href)}" title="{esc(_("Export to CSV"))}" aria-label="{esc(_("Export to CSV"))}">{icon("download")}</a>
    </form>"""

    rows, dialogs = [], []
    for ev in events:
        r, d = _row_html(ev, labels, cats)
        rows.append(r)
        dialogs.append(d)

    first = (f["page"] - 1) * audit.PAGE_SIZE + 1 if total else 0
    last = min(total, f["page"] * audit.PAGE_SIZE)
    count = (_("{first}–{last} of {total}", first=first, last=last, total=total) if total > audit.PAGE_SIZE
             else ngettext("{n} event", "{n} events", total))
    pager = ""
    if total > audit.PAGE_SIZE:
        newer = (f'<a class="btn small" href="{BASE}/logs?{esc(_qs(f, page=f["page"] - 1 if f["page"] > 2 else ""))}">'
                 f'{esc(_("Newer"))}</a>' if f["page"] > 1 else "")
        older = (f'<a class="btn small" href="{BASE}/logs?{esc(_qs(f, page=f["page"] + 1))}">{esc(_("Older"))}</a>'
                 if last < total else "")
        pager = f'<div class="audit-pager">{newer}{older}</div>'

    # Only the first page updates live: newer events would shift later pages
    live = ' data-live="rows"' if f["page"] == 1 else ""
    live_dlg = ' data-live="dialogs"' if f["page"] == 1 else ""
    live_count = ' data-live="count"' if f["page"] == 1 else ""
    empty = ""
    if not events:
        filtered = any(f[k] for k in ("text", "category", "actor", "from", "to", "target"))
        empty = f'<p class="no-results muted">{esc(_("No events match the current filters.") if filtered else _("No events yet. Changes made in the console and device activity show up here."))}</p>'
    body = head + toolbar + f"""
    <span class="pill"{live_count}>{esc(count)}</span>
    <div class="table-wrap">
      <table class="machines audit">
        <thead><tr><th>{esc(_("Time"))}</th><th>{esc(_("Event"))}</th><th>{esc(_("Target"))}</th>
          <th>{esc(_("Actor"))}</th><th>{esc(_("Details"))}</th></tr></thead>
        <tbody{live}>{"".join(rows)}</tbody>
      </table>
    </div>
    {empty}{pager}
    <div{live_dlg}>{"".join(dialogs)}</div>"""
    return layout(_("Logs"), "logs", body, session, ctx)


def csv_export(params: dict) -> str:
    f = filters_from(params)
    events, _total = audit.query(f, audit.CSV_MAX_ROWS, 0)
    return audit.to_csv(events)
