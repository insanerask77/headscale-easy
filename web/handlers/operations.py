"""Backup, restore and notification handlers (mixin of app.Handler)."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import time

import audit
import backup_pages
import headscale as hs
import multipart
import notify
import status as server_status
from handlers import shared as sh
from handlers.base import HandlerBase
from i18n import _
from ui import BASE


class OperationsHandlers(HandlerBase):
    def backup_now(self, session: dict):
        """Status page > Back up now (admins only): asks the all-in-one supervisor for a manual backup."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        result = hs.control_backup()
        if result in ("started", "busy"):
            audit.request_event(self, session, "backup.run", _("Backup"), {"result": result})
        return self.redirect(f"{BASE}/backups?m=backup-{result}")

    def backup_settings(self, session: dict, form: dict):
        """Status page > Backup settings (admins only): scheduled backups on/off, schedule, days to keep."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        enabled = form.get("backup_enabled", "on") != "off"
        schedule = (form.get("backup_schedule") or "").strip()
        keep = (form.get("backup_keep_days") or "").strip()
        result, detail = hs.control_backup_settings(enabled, schedule, keep)
        if result == "saved":
            sh.log.info("%s set scheduled backups to %s", session["username"], f"{schedule!r}, {keep} days" if enabled else "off")
            audit.request_event(self, session, "backup.settings", _("Backup settings"),
                                {"enabled": enabled, "schedule": schedule if enabled else None, "keep_days": keep})
        elif result == "invalid":
            sh.log.info("%s sent invalid backup settings: %s", session["username"], detail)
        return self.redirect(f"{BASE}/backups?m=backup-settings-{result}")

    def backup_upload(self, session: dict):
        """Backups > Upload a backup (admins only): the file is streamed to a work file in the backups directory
        (never held in memory), the supervisor checks it as it would a restore and keeps it under a safe name;
        with ``action=restore`` and the typed confirmation it is restored right away."""
        self.close_connection = True  # a big body that is not read to the end must not poison the connection
        if session.get("must_change"):
            return self.redirect(f"{BASE}/settings/account?m=must-change")
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        directory = os.environ.get("BACKUP_DIR", "")
        if not directory or not os.path.isdir(directory):
            return self.fail(404, _("Not found"), _("Backups cannot be uploaded here."))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self.redirect(f"{BASE}/backups?m=backup-upload-none")
        if length > sh.BACKUP_UPLOAD_MAX + (1 << 20):
            return self.redirect(f"{BASE}/backups?m=backup-upload-toolarge")
        for stale in os.listdir(directory):  # work files of uploads that died half way
            path = os.path.join(directory, stale)
            if re.fullmatch(r"\.upload-[0-9a-f]{24}\.part", stale) and time.time() - os.path.getmtime(path) > 3600:
                os.unlink(path)
        tmp_name = ".upload-" + secrets.token_hex(12) + ".part"
        tmp_path = os.path.join(directory, tmp_name)
        sink = []

        def on_file(name, filename, fields):
            if name != "file":
                raise multipart.MultipartError("unexpected file")
            if not hmac.compare_digest(str(fields.get("csrf", "")), session["csrf"]):
                raise multipart.MultipartError("session", 403)  # nothing is written for a forged request
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            sink.append(os.fdopen(fd, "wb"))
            return sink[0]

        def discard():
            for fh in sink:
                fh.close()
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        try:
            fields, files = multipart.read_form(self.rfile, self.headers.get("Content-Type", ""), length, on_file,
                                                max_file=sh.BACKUP_UPLOAD_MAX)
        except multipart.MultipartError as exc:
            discard()
            if exc.status == 403:
                return self.fail(403, _("Session expired"), _("Reload the page and try again."))
            sh.log.info("%s sent a bad backup upload: %s", session["username"], exc)
            return self.redirect(f"{BASE}/backups?m=backup-upload-" + ("toolarge" if exc.status == 413 else "error"))
        except OSError as exc:  # no space left, or the backups directory is gone
            discard()
            sh.log.error("could not store an uploaded backup: %s", exc)
            return self.redirect(f"{BASE}/backups?m=backup-upload-error")
        for fh in sink:
            fh.close()
        if not hmac.compare_digest(str(fields.get("csrf", "")), session["csrf"]):
            discard()
            return self.fail(403, _("Session expired"), _("Reload the page and try again."))
        if "file" not in files or files["file"][1] == 0:
            discard()
            return self.redirect(f"{BASE}/backups?m=backup-upload-none")
        restore = fields.get("action") == "restore"
        if restore and fields.get("confirm") != "RESTORE":
            discard()
            return self.redirect(f"{BASE}/backups?m=backup-restore-confirm")
        original = re.sub(r"[^\x20-\x7e]", "?", os.path.basename(files["file"][0].replace("\\", "/")))[:120]
        result, detail = hs.control_backup_upload(tmp_name, original)
        if result != "saved":
            discard()  # (the supervisor already deleted a file it refused)
            sh.log.info("%s uploaded %r and it was refused: %s %s", session["username"], original, result, detail)
            return self.redirect(f"{BASE}/backups?m=backup-upload-{result}")
        audit.request_event(self, session, "backup.upload", detail,
                            {"file": detail, "original": original, "size": files["file"][1]})
        sh.log.warning("%s uploaded the backup %s (%s bytes)", session["username"], detail, files["file"][1])
        if not restore:
            return self.redirect(f"{BASE}/backups?m=backup-uploaded")
        started, rid = hs.control_restore(detail)
        if started != "started":  # the file stays in the list: it can be restored from there
            return self.redirect(f"{BASE}/backups?m=backup-restore-{started}")
        audit.request_event(self, session, "backup.restore", detail, {"file": detail})
        return self.send(200, backup_pages.restoring_page(rid))

    def backup_restore(self, session: dict, form: dict):
        """Status page > Restore (admins only, typed confirmation): the supervisor restores one backup and restarts us."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        if form.get("confirm") != "RESTORE":
            return self.redirect(f"{BASE}/backups?m=backup-restore-confirm")
        name = form.get("name", "")
        result, detail = hs.control_restore(name)
        if result != "started":
            sh.log.info("%s could not restore %r: %s %s", session["username"], name, result, detail)
            return self.redirect(f"{BASE}/backups?m=backup-restore-{result}")
        sh.log.warning("%s restores the backup %s", session["username"], name)
        audit.request_event(self, session, "backup.restore", name, {"file": name})
        return self.send(200, backup_pages.restoring_page(detail))

    def backup_download(self, session: dict, form: dict):
        """Status page > Download (admins only, POST with the CSRF token): the archive as an attachment."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        name = form.get("name", "")
        opened = server_status.open_backup(name)
        if opened is None:
            return self.fail(404, _("Not found"), _("That backup does not exist."))
        fh, size = opened
        with fh:
            audit.request_event(self, session, "backup.download", name, {"file": name, "size": size})
            sh.log.warning("%s downloads the backup %s", session["username"], name)
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            shutil.copyfileobj(fh, self.wfile, 1 << 20)

    def restore_status(self, restore_id: str):
        """GET /console/restore-status?id=...: has the restore with that id finished? No session needed (the restore
        restarts the console and may change the session secret); it only says done / ok for an id only the requester has."""
        done = ok = False
        result = hs.restore_result()
        try:
            if result is not None and float(restore_id) == float(result.get("requested")):
                done, ok = True, bool(result.get("ok"))
        except (TypeError, ValueError):
            pass
        return self.send(200, json.dumps({"done": done, "ok": ok}), "application/json",
                         [("Cache-Control", "no-store")])

    def notify_test(self, session: dict):
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This section is for admins only."))
        results = notify.send_test()
        audit.request_event(self, session, "settings.notify_test", _("Webhook notifications"),
                            {"destinations": [label for label, _ok in results],
                             "failed": [label for label, ok in results if not ok]})
        if not results:
            code = "notify-none"
        else:
            code = "notify-test-ok" if all(ok for _label, ok in results) else "notify-test-failed"
        return self.redirect(f"{BASE}/settings/general?m={code}")
