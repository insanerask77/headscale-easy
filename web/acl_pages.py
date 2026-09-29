"""Visual ACL policy editor: Rules, Groups & tags and Test access panels.

admin_pages.acl_page() wires these together with the raw HuJSON editor
(Advanced tab), which stays the full, authoritative fallback: anything a rule
or group cannot represent (see policy.rule_view's 'editable' flag) is only
ever edited there.
"""

from __future__ import annotations

import policy
from i18n import _
from pages import dialog
from ui import BASE, badge, csrf_input, esc, icon


def datalist(vocab: dict) -> str:
    options = (vocab["auto"] + vocab["groups"] + vocab["tags"]
              + [f"{u}@" for u in vocab["users"]] + vocab["hosts"])
    seen, opts = set(), []
    for o in options:
        if o not in seen:
            seen.add(o)
            opts.append(o)
    items = "".join(f'<option value="{esc(o)}">' for o in opts)
    return f'<datalist id="acl-targets">{items}</datalist>'


def _chips(values: list[str]) -> str:
    if not values:
        return f'<span class="muted small">{esc(_("Anyone"))}</span>'
    return f'<div class="badges">{"".join(badge(v, "tag") for v in values)}</div>'


def _table(rows: list[str], headers: list[str], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    return f"""<div class="table-wrap"><table class="simple"><thead><tr>{head}</tr></thead>
      <tbody>{"".join(rows)}</tbody></table></div>"""


# -----------------------------------------------------------------------------
# Rules
# -----------------------------------------------------------------------------

def _rule_dialog(dialog_id: str, session: dict, title: str, view: dict | None, idx: int | None) -> str:
    v = view or {"src": [], "dst": [], "port": "*", "proto": ""}
    proto_opts = "".join(
        f'<option value="{p}" {"selected" if v["proto"] == p else ""}>{esc(label)}</option>'
        for p, label in [("", _("Any")), ("tcp", "TCP"), ("udp", "UDP"), ("icmp", "ICMP")])
    index_input = f'<input type="hidden" name="index" value="{idx}">' if idx is not None else ""
    return dialog(
        dialog_id, title,
        esc(_("Who can reach what. Suggestions include users, tags, groups and the special autogroup: names; "
              "any other value is fine too.")),
        f"{BASE}/acl/rules", session, submit=_("Save"),
        fields=f"""{index_input}
        <label class="field">{esc(_("Source"))}<input name="src" list="acl-targets" value="{esc(", ".join(v["src"]))}"
          placeholder="autogroup:member, tag:server, alice@" required autocomplete="off" spellcheck="false"></label>
        <label class="field">{esc(_("Destination"))}<input name="dst" list="acl-targets" value="{esc(", ".join(v["dst"]))}"
          placeholder="tag:nas, 10.0.0.0/24" required autocomplete="off" spellcheck="false"></label>
        <div class="grid-2">
          <label class="field">{esc(_("Port"))}<input name="port" value="{esc(v["port"])}" placeholder="*"
            autocomplete="off" spellcheck="false"></label>
          <label class="field">{esc(_("Protocol"))}<select name="proto">{proto_opts}</select></label>
        </div>""")


def rules_panel(session: dict, pol: dict) -> str:
    acls = pol.get("acls") or []
    rows, dialogs = [], []
    for idx, rule in enumerate(acls):
        view = policy.rule_view(rule)
        edit_action = (f'<button type="button" data-open="acl-rule-{idx}">{esc(_("Edit…"))}</button>' if view["editable"]
                      else f'<span class="menu-note">{esc(_("Edit in Advanced (HuJSON)"))}</span>')
        rows.append(f"""
        <tr>
          <td>{_chips(view["src"])}</td>
          <td>{_chips(view["dst"])}</td>
          <td><code>{esc(view["port"])}</code></td>
          <td>{esc(view["proto"].upper()) if view["proto"] else esc(_("Any"))}</td>
          <td class="actions">
            <details class="dropdown">
              <summary class="icon-btn" aria-label="{esc(_("Actions"))}">{icon("more")}</summary>
              <div class="dropdown-body right">
                {edit_action}<hr>
                <form method="post" action="{BASE}/acl/rules">{csrf_input(session)}
                  <input type="hidden" name="op" value="delete"><input type="hidden" name="index" value="{idx}">
                  <button type="submit" class="danger">{esc(_("Delete…"))}</button></form>
              </div>
            </details>
          </td>
        </tr>""")
        if view["editable"]:
            dialogs.append(_rule_dialog(f"acl-rule-{idx}", session, _("Edit rule"), view, idx))
    dialogs.append(_rule_dialog("acl-rule-new", session, _("Add rule"), None, None))

    content = _table(rows, [_("Source"), _("Destination"), _("Port"), _("Protocol"), ""],
                     _("No rules yet: every machine can reach every other one."))
    return f"""<div class="stack">
      <div class="card-title"><h2>{esc(_("Rules"))}</h2>
        <button class="btn primary small" type="button" data-open="acl-rule-new">{icon("plus")} {esc(_("Add rule"))}</button></div>
      {content}
    </div>{"".join(dialogs)}"""


# -----------------------------------------------------------------------------
# Groups & tag owners
# -----------------------------------------------------------------------------

def _group_dialog(dialog_id: str, session: dict, title: str, name: str, members: list[str]) -> str:
    return dialog(dialog_id, title, esc(_("Members are Headscale user names with an @, e.g. alice@.")),
                 f"{BASE}/acl/groups", session, submit=_("Save"),
                 fields=f"""<input type="hidden" name="orig_name" value="{esc(name)}">
        <label class="field">{esc(_("Name"))}<input name="name" value="{esc(name[len('group:'):] if name.startswith('group:') else name)}"
          placeholder="staff" required autocomplete="off" spellcheck="false"></label>
        <label class="field">{esc(_("Members"))}<input name="members" list="acl-targets" value="{esc(', '.join(members))}"
          placeholder="alice@, bob@" required autocomplete="off" spellcheck="false"></label>""")


def _tag_owner_dialog(dialog_id: str, session: dict, title: str, name: str, owners: list[str]) -> str:
    return dialog(dialog_id, title, esc(_("Owners can be users (alice@) or a group (group:admins).")),
                 f"{BASE}/acl/tags", session, submit=_("Save"),
                 fields=f"""<input type="hidden" name="orig_name" value="{esc(name)}">
        <label class="field">{esc(_("Tag"))}<input name="name" value="{esc(name)}"
          placeholder="tag:server" required autocomplete="off" spellcheck="false"></label>
        <label class="field">{esc(_("Owners"))}<input name="owners" list="acl-targets" value="{esc(', '.join(owners))}"
          placeholder="alice@, group:admins" required autocomplete="off" spellcheck="false"></label>""")


def groups_panel(session: dict, pol: dict) -> str:
    groups: dict[str, list[str]] = pol.get("groups") or {}
    tag_owners: dict[str, list[str]] = pol.get("tagOwners") or {}

    def rows_for(mapping: dict[str, list[str]], action_path: str, dialog_prefix: str, make_dialog):
        rows, dialogs = [], []
        for name, members in sorted(mapping.items()):
            rows.append(f"""
            <tr><td><code>{esc(name)}</code></td><td>{_chips(members)}</td>
              <td class="actions">
                <details class="dropdown">
                  <summary class="icon-btn" aria-label="{esc(_("Actions"))}">{icon("more")}</summary>
                  <div class="dropdown-body right">
                    <button type="button" data-open="{dialog_prefix}-{esc(name)}">{esc(_("Edit…"))}</button><hr>
                    <form method="post" action="{action_path}">{csrf_input(session)}
                      <input type="hidden" name="op" value="delete"><input type="hidden" name="orig_name" value="{esc(name)}">
                      <button type="submit" class="danger">{esc(_("Delete…"))}</button></form>
                  </div>
                </details>
              </td></tr>""")
            dialogs.append(make_dialog(f"{dialog_prefix}-{name}", _("Edit"), name, members))
        return rows, dialogs

    g_rows, g_dialogs = rows_for(groups, f"{BASE}/acl/groups", "acl-group",
                                 lambda did, title, name, members: _group_dialog(did, session, title, name, members))
    g_dialogs.append(_group_dialog("acl-group-new", session, _("Add group"), "", []))
    t_rows, t_dialogs = rows_for(tag_owners, f"{BASE}/acl/tags", "acl-tag",
                                 lambda did, title, name, owners: _tag_owner_dialog(did, session, title, name, owners))
    t_dialogs.append(_tag_owner_dialog("acl-tag-new", session, _("Add tag owner"), "", []))

    return f"""<div class="stack">
      <div class="card-title"><h2>{esc(_("Groups"))}</h2>
        <button class="btn primary small" type="button" data-open="acl-group-new">{icon("plus")} {esc(_("Add group"))}</button></div>
      <p class="muted small">{esc(_("Reusable sets of users for rules, referenced with the group: prefix."))}</p>
      {_table(g_rows, [_("Group"), _("Members"), ""], _("No groups yet."))}
    </div>
    <hr>
    <div class="stack">
      <div class="card-title"><h2>{esc(_("Tag owners"))}</h2>
        <button class="btn primary small" type="button" data-open="acl-tag-new">{icon("plus")} {esc(_("Add tag owner"))}</button></div>
      <p class="muted small">{esc(_("Who can assign each tag to a machine. A tag with no owner here is rejected."))}</p>
      {_table(t_rows, [_("Tag"), _("Owners"), ""], _("No tag owners yet."))}
    </div>
    {"".join(g_dialogs)}{"".join(t_dialogs)}"""


# -----------------------------------------------------------------------------
# Test access
# -----------------------------------------------------------------------------

def test_panel(session: dict, test: tuple | None) -> str:
    src, dst, port, proto, verdict = test or ("", "", "", "", None)
    result_html = ""
    if verdict is not None:
        if verdict.rule is not None:
            detail = _("Rule {n}: source {src} -> destination {dst}", n=verdict.rule_index + 1,
                       src=", ".join(verdict.rule.get("src") or []), dst=", ".join(verdict.rule.get("dst") or []))
        elif verdict.allowed:
            detail = _("No policy: every machine can reach every other one.")
        else:
            detail = _("No rule allows it.")
        kind = "ok" if verdict.allowed else "error"
        label = _("Allowed") if verdict.allowed else _("Denied")
        result_html = f'<div class="notice {kind}" role="status"><b>{esc(label)}</b><br>{esc(detail)}</div>'
    return f"""<div class="stack">
      <h2>{esc(_("Test access"))}</h2>
      <p class="muted small">{esc(_("A simulation over this policy, not a live packet test. For certainty, test from the actual devices."))}</p>
      <form method="post" action="{BASE}/acl/test" class="stack">
        {csrf_input(session)}
        <div class="grid-2">
          <label class="field">{esc(_("From"))}<input name="test_src" list="acl-targets" value="{esc(src)}"
            placeholder="alice@" required autocomplete="off" spellcheck="false"></label>
          <label class="field">{esc(_("To"))}<input name="test_dst" list="acl-targets" value="{esc(dst)}"
            placeholder="nas" required autocomplete="off" spellcheck="false"></label>
        </div>
        <div class="grid-2">
          <label class="field">{esc(_("Port (optional)"))}<input name="test_port" value="{esc(port)}" placeholder="445"
            autocomplete="off" spellcheck="false"></label>
          <label class="field">{esc(_("Protocol (optional)"))}<input name="test_proto" value="{esc(proto)}" placeholder="tcp"
            autocomplete="off" spellcheck="false"></label>
        </div>
        <div class="form-foot"><button class="btn primary" type="submit">{esc(_("Test"))}</button></div>
      </form>
      {result_html}
    </div>"""
