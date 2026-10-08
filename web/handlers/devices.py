"""Machines, pre-auth keys and device registration handlers (mixin of app.Handler)."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse

import audit
import docker_tab
import expiry
import headscale as hs
import live
import pages
import sessions
from handlers import shared as sh
from i18n import _
from ui import BASE

class DevicesHandlers:
    def events_stream(self):
        """Server-Sent Events: one message per device change this session may see.
        The page re-fetches its own (already filtered) HTML when one arrives."""
        session = self.session()
        if not session:
            return self.send(401, "Unauthorized", "text/plain")
        if session.get("must_change"):
            return self.send(403, "Forbidden", "text/plain")
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host", ""):
            return self.send(403, "Forbidden", "text/plain")  # a page on another site
        everyone = session.get("admin") or sh.is_auditor(session)

        def own_user() -> str:
            user = None if everyone else sh.my_user(session)
            return str(user["id"]) if user else ""

        try:
            sub = sh.HUB.subscribe(session.get("sid", ""), None if everyone else own_user())
        except live.TooMany:
            return self.send(429, "Too many open streams", "text/plain", [("Retry-After", "30")])
        self.close_connection = True
        try:
            self.send_response(200)
            for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"),
                         ("X-Accel-Buffering", "no"), ("X-Content-Type-Options", "nosniff"),
                         ("Referrer-Policy", "same-origin")):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b"retry: 3000\n: connected\n\n")
            self.wfile.flush()
            while not sub.closed.is_set():
                event = sub.get(live.HEARTBEAT)
                if sub.closed.is_set():
                    break
                if event is None:
                    if not sessions.validate(session):  # signed out or revoked meanwhile
                        break
                    if not everyone and not sub.user:  # a member who had no devices yet
                        sub.user = own_user()
                        if sub.user:
                            self.wfile.write(live.format_event({"type": "added"}))
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(live.format_event(event))
                self.wfile.flush()
        except OSError:  # the browser went away
            pass
        finally:
            sh.HUB.unsubscribe(sub)

    def keys_view(self, session: dict, flash: str, new_key: dict | None = None, new_apikey: str = "",
                  preselect: str = ""):
        if session.get("admin"):
            page = pages.keys_page(session, sh.CTX, hs.all_keys(), flash, new_key, users=hs.all_users(),
                                   apikeys=hs.api_keys(), own_prefix=hs.own_api_key_prefix(),
                                   new_apikey=new_apikey, preselect=preselect)
        else:
            user = sh.my_user(session)
            page = pages.keys_page(session, sh.CTX, hs.user_keys(user) if user else None, flash, new_key)
        self.send(200, page)

    def machine_action(self, session: dict, node_id: str, action: str, form: dict):
        # Go back to where the user was (list or detail)
        back = str(form.get("back", "machines"))
        if not re.fullmatch(r"machines(/\d+)?", back) or action == "delete":
            back = "machines"
        dest = f"{BASE}/{back}"

        node = sh.node_for(session, node_id)
        if node is None:
            sh.log.warning("%s tried '%s' on node %s, which they cannot manage", session["username"], action, node_id)
            return self.redirect(f"{BASE}/machines?m=not-found")
        if sh.is_auditor(session):
            # node_for() resolves any node for an auditor so they can view it;
            # never let that translate into a write, on their own devices or anyone else's
            return self.redirect(f"{dest}?m=forbidden")
        if action in {"expiry", "routes", "approve-routes", "tags"} and not session.get("admin"):
            return self.redirect(f"{dest}?m=forbidden")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not sh.NODE_NAME_RE.fullmatch(name):
                    return self.redirect(f"{dest}?m=bad-name")
                hs.api("POST", f"/node/{node_id}/rename/{urllib.parse.quote(name)}")
                audit.request_event(self, session, "machine.rename", name, {"from": node.get("givenName"), "to": name}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=renamed")
            if action == "expire":
                hs.api("POST", f"/node/{node_id}/expire")
                audit.request_event(self, session, "machine.expire", node.get("givenName"), ref=f"node:{node_id}")
                return self.redirect(f"{dest}?m=expired")
            if action == "expiry":
                if form.get("disable") == "1":
                    hs.api("POST", f"/node/{node_id}/expire?disableExpiry=true")
                    audit.request_event(self, session, "machine.expiry_disable", node.get("givenName"), ref=f"node:{node_id}")
                    return self.redirect(f"{dest}?m=expiry-off")
                # Re-enable: the tailnet's key expiry (Settings → General), or
                # Tailscale's 180 days when devices are set to never expire
                days = hs.key_expiry_days() or 180
                hs.api("POST", f"/node/{node_id}/expire?" + urllib.parse.urlencode({"expiry": sh.iso_in(days)}))
                audit.request_event(self, session, "machine.expiry_enable", node.get("givenName"), {"days": days}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=expiry-on")
            if action == "routes":
                # Only routes the node advertises can be approved
                available = set(node.get("availableRoutes") or [])
                chosen = form.get("route") or []
                routes = [r for r in chosen if r in available]
                if "exit" in chosen:
                    routes += [r for r in sh.EXIT_ROUTES if r in available]
                hs.api("POST", f"/node/{node_id}/approve_routes", {"routes": sorted(set(routes))})
                sh.log.info("%s approved routes %s on node %s", session["username"], routes, node_id)
                audit.request_event(self, session, "machine.routes", node.get("givenName"), {"from": sorted(node.get("approvedRoutes") or []), "to": sorted(set(routes))}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=routes")
            if action == "approve-routes":
                # Approve everything the node advertises, keeping what is already approved
                available = set(node.get("availableRoutes") or [])
                before = set(node.get("approvedRoutes") or [])
                routes = sorted(before | available)
                hs.api("POST", f"/node/{node_id}/approve_routes", {"routes": routes})
                audit.request_event(self, session, "machine.routes", node.get("givenName"), {"from": sorted(before), "to": routes}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=routes")
            if action == "tags":
                tags = [t.lower() for t in sh.lines(str(form.get("tags", "")))]
                if any(not sh.TAG_RE.fullmatch(t) for t in tags):
                    return self.redirect(f"{dest}?m=failed")
                hs.api("POST", f"/node/{node_id}/tags", {"tags": tags})
                audit.request_event(self, session, "machine.tags", node.get("givenName"), {"from": sorted(node.get("tags") or []), "to": sorted(tags)}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=tags")
            hs.api("DELETE", f"/node/{node_id}")
            sh.log.info("%s removed node %s", session["username"], node_id)
            audit.request_event(self, session, "machine.delete", node.get("givenName"), {"user": (node.get("user") or {}).get("name")}, f"node:{node_id}")
            return self.redirect(f"{BASE}/machines?m=removed")
        except urllib.error.HTTPError as exc:
            msg = hs.api_error(exc)
            sh.log.warning("headscale rejected '%s' on %s: %s", action, node_id, msg)
            if action == "tags":
                # The reason (e.g. a tag without an owner in tagOwners) is useful
                return self.send(400, pages.machine_page(session, sh.CTX, sh.to_machines([node])[0], "",
                                                         error=_("Could not save the tags: {reason}", reason=msg)))
            return self.redirect(f"{dest}?m=failed")

    def register_node(self, session: dict, form: dict):
        # Accept the full URL printed by 'tailscale up'
        auth_id = str(form.get("auth_id", "")).strip().rstrip("/").rsplit("/", 1)[-1]
        user = str(form.get("user", ""))
        if not sh.AUTH_ID_RE.fullmatch(auth_id) or user not in {u["name"] for u in hs.all_users()}:
            return self.redirect(f"{BASE}/machines?m=failed")
        try:
            self.register_auth_id(session, user, auth_id)
        except urllib.error.HTTPError as exc:
            return self.send(400, pages.machines_page(session, sh.CTX, sh.to_machines(sh.visible_nodes(session)), True, "",
                                                      hs.all_users(), error=_("Could not register: {reason}",
                                                                              reason=hs.api_error(exc))))
        return self.redirect(f"{BASE}/machines?m=registered")

    def register_auth_id(self, session: dict, user: str, auth_id: str) -> dict:
        """Register a pending 'tailscale up' (Auth ID) to user; the new node."""
        try:
            node = hs.api("POST", "/auth/register", {"user": user, "authId": auth_id}).get("node") or {}
        except urllib.error.HTTPError as exc:
            sh.log.warning("Auth ID registration rejected: %s", hs.api_error(exc))
            raise
        audit.request_event(self, session, "machine.register", user, {"user": user, "auth_id": audit.prefix(auth_id)})
        return node

    def register_owner(self, session: dict) -> tuple[list[dict] | None, dict | None]:
        """(users to choose from, default owner) on the approval page: admins
        pick any user (their own preselected); members and network admins can
        only add devices to their own user."""
        own = sh.my_user(session)
        return (hs.all_users(), own) if session.get("admin") else (None, own)

    def register_view(self, session: dict, auth_id: str):
        if sh.is_auditor(session):
            return self.fail(403, _("No permission"), _("Auditors cannot add devices."))
        users, owner = self.register_owner(session)
        self.send(200, pages.register_page(session, sh.CTX, auth_id, users, owner))

    def register_device(self, session: dict, auth_id: str, form: dict):
        if sh.is_auditor(session):
            return self.fail(403, _("No permission"), _("Auditors cannot add devices."))
        users, owner = self.register_owner(session)
        if users is not None:
            user = str(form.get("user", ""))
            if user not in {u["name"] for u in users}:
                return self.redirect(f"{BASE}/machines?m=failed")
        elif owner:
            user = owner["name"]  # never from the form: a member only adds to themselves
        else:
            return self.redirect(f"{BASE}/register/{auth_id}")
        try:
            node = self.register_auth_id(session, user, auth_id)
        except urllib.error.HTTPError as exc:
            return self.send(400, pages.register_page(session, sh.CTX, auth_id, users, owner,
                                                      error=_("Could not register: {reason}", reason=hs.api_error(exc))))
        dest = f"{BASE}/machines/{node['id']}" if str(node.get("id", "")).isdigit() else f"{BASE}/machines"
        return self.redirect(f"{dest}?m=registered")

    def remove_inactive(self, session: dict, form: dict):
        """Admin: remove the ticked machines of "Remove inactive machines…".
        Checked again now: a machine that came back online (or was seen within
        INACTIVE_DAYS) since the dialog was opened is kept."""
        wanted = expiry.selected_ids(form)
        removed, failed = [], []
        for node in expiry.inactive_nodes(hs.all_nodes()):
            node_id = str(node.get("id"))
            if node_id not in wanted:
                continue
            try:
                hs.api("DELETE", f"/node/{node_id}")
                removed.append(node.get("givenName") or node.get("name") or node_id)
            except urllib.error.HTTPError as exc:
                sh.log.warning("headscale rejected removing inactive node %s: %s", node_id, hs.api_error(exc))
                failed.append(node_id)
        sh.log.info("%s removed %d inactive machine(s): %s", session["username"], len(removed), ", ".join(removed))
        if removed:
            audit.request_event(self, session, "machines.remove_inactive", "", {"nodes": removed})
        return self.redirect(f"{BASE}/machines?m={expiry.remove_result(len(removed), len(failed))}")

    def bulk_machines(self, session: dict, form: dict, op: str):
        """Admin: act on every machine ticked in the Machines table's bulk
        selection at once (expire keys, remove, or add a tag)."""
        wanted = expiry.selected_ids(form)
        nodes = {str(n.get("id")): n for n in hs.all_nodes()}

        tags_to_add: list[str] = []
        if op == "tags":
            tags_to_add, error = sh.bulk_tags_from_form(form)
            if error:
                return self.redirect(f"{BASE}/machines?m=failed")

        done, failed = [], []
        for node_id in wanted:
            node = nodes.get(node_id)
            if node is None:
                continue
            try:
                if op == "expire":
                    hs.api("POST", f"/node/{node_id}/expire")
                elif op == "remove":
                    hs.api("DELETE", f"/node/{node_id}")
                else:
                    current = set(node.get("tags") or [])
                    hs.api("POST", f"/node/{node_id}/tags", {"tags": sorted(current | set(tags_to_add))})
                done.append(node.get("givenName") or node.get("name") or node_id)
            except urllib.error.HTTPError as exc:
                sh.log.warning("headscale rejected bulk %s on node %s: %s", op, node_id, hs.api_error(exc))
                failed.append(node_id)
        sh.log.info("%s bulk-%s %d machine(s): %s", session["username"], op, len(done), ", ".join(done))
        if done:
            details = {"nodes": done, "tags": tags_to_add} if op == "tags" else {"nodes": done}
            audit.request_event(self, session, f"machines.bulk_{op}", "", details)
        flash_op = {"expire": "expired", "remove": "removed", "tags": "tagged"}[op]
        code = f"bulk-{flash_op}-{len(done)}" if done else "failed"
        return self.redirect(f"{BASE}/machines?m={code}")

    def create_key(self, session: dict, form: dict):
        if session.get("admin"):
            uid = str(form.get("user_id", ""))
            user = next((u for u in hs.all_users() if str(u["id"]) == uid), None)
        else:
            user = sh.my_user(session)
        if not user:
            return self.redirect(f"{BASE}/settings/keys?m=no-user")
        days = str(form.get("days", "90"))
        days = days if days in sh.KEY_DAYS else "90"
        key = hs.api("POST", "/preauthkey", {
            "user": str(user["id"]),
            "reusable": form.get("reusable") == "1",
            "ephemeral": form.get("ephemeral") == "1",
            "expiration": sh.iso_in(int(days)),
        })["preAuthKey"]
        sh.log.info("%s generated an auth key for %s (reusable=%s, ephemeral=%s, %s d)", session["username"],
                 user["name"], key.get("reusable"), key.get("ephemeral"), days)
        audit.request_event(self, session, "authkey.create", user["name"], {"key": key.get("key"), "reusable": key.get("reusable"), "ephemeral": key.get("ephemeral"), "days": int(days)}, f"user:{user['id']}")
        # Shown in this very response: Headscale never returns it again
        self.keys_view(session, "", new_key=key)

    def add_docker(self, session: dict, form: dict):
        """Docker tab of Add device: validate the form, optionally make a single-use auth key,
        and show the page again with the snippets filled in."""
        if sh.is_auditor(session):
            return self.fail(403, _("No permission"), _("Auditors cannot add devices."))
        values, error = docker_tab.parse(form)
        admin = bool(session.get("admin"))
        users = hs.all_users() if admin else None
        exit_nodes = sh.exit_nodes_for(session)
        if not error and values["use_exit"] and values["use_exit"] not in {n["ip"] for n in exit_nodes}:
            error = _("Invalid exit node.")
        key = ""
        if not error and values["generate"]:
            if admin:
                user = next((u for u in users if str(u["id"]) == values["user_id"]), None)
            else:
                user = sh.my_user(session)
            if not user:
                error = _("There is no Headscale user to own the key.")
            else:
                created = hs.api("POST", "/preauthkey", {
                    "user": str(user["id"]), "reusable": False, "ephemeral": False,
                    "expiration": sh.iso_in(int(values["days"]))})["preAuthKey"]
                key = created.get("key") or ""
                sh.log.info("%s generated a single-use auth key for %s (Docker tab, %s d)", session["username"],
                         user["name"], values["days"])
                # Never the key itself
                audit.request_event(self, session, "authkey.create", user["name"],
                                    {"reusable": False, "ephemeral": False, "days": int(values["days"]), "source": "docker"},
                                    f"user:{user['id']}")
        page = pages.add_page(session, sh.CTX, docker={"values": values, "key": key, "error": error, "users": users,
                                                       "exit_nodes": exit_nodes})
        return self.send(400 if error else 200, page)

    def revoke_key(self, session: dict, key_id: str):
        if session.get("admin"):
            ok = any(str(k.get("id")) == key_id for k in hs.all_keys())
        else:
            ok = hs.owned_key(sh.my_user(session), key_id) is not None
        if not ok:
            sh.log.warning("%s tried to revoke key %s, which they cannot manage", session["username"], key_id)
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        hs.api("POST", "/preauthkey/expire", {"id": key_id})
        audit.request_event(self, session, "authkey.revoke", f"#{key_id}", ref=f"authkey:{key_id}")
        return self.redirect(f"{BASE}/settings/keys?m=key-revoked")
