# Roadmap

What comes next for Headscale Easy, most relevant first. Tick an item when it
ships and note the version. Ideas and votes are welcome in
[issues](https://github.com/insanerask77/headscale-easy/issues) and
[discussions](https://github.com/insanerask77/headscale-easy/discussions).

Effort: **S** = hours · **M** = one or two days · **L** = several days.

## 🔴 Critical — keeps installations working and safe

- [x] **1. Automatic renewal of the web UI's API key** · S — *1.0.4*
  The installer creates it for 90 days; after that the web UI stops working
  until `./install.sh` is run again. The web UI should renew it by itself when
  it has, e.g., 15 days left.
- [x] **2. Scheduled backups and guided restore** · M — *1.0.6*
  `make backup` exists but is manual. Scheduled backups (daily, N days of
  retention), local or remote target (S3, rsync) and a tested `make restore`.
  Losing Headscale's database means re-registering every device.
  Done: optional daily backups chosen in the installer, retention, one-off
  `make backup`, tested `make restore` (also on a new server). Pending: remote
  targets (S3, rsync) — until then, point `BACKUP_DIR` at a mounted NAS.
- [x] **3. Two-factor authentication (MFA) in Authentik** · S — *1.0.5*
  The web UI controls the whole network behind a password. Authentik already
  has TOTP and passkeys: enable them in the blueprint, optional for members and
  required for admins.
  The mode (admins, everyone or optional) can be changed live by admins from
  Settings → General in the web UI.

## 🟠 High — what daily use misses most

- [x] **4. Invitations and password recovery** · M — *1.1.0*
  Today the admin makes up the user's password. Send an invitation link so each
  person sets their own, plus "Forgot your password?". Needs SMTP in the
  installer (or showing the link to copy it).
- [x] **5. Expiry warnings and inactive devices** · M — *1.1.0* (email/webhook notifications come with item 14)
  Devices now expire after 180 days: "expiring soon" filter, a warning in the
  web UI and optionally email or webhook; clean-up of devices offline for N
  days.
- [x] **6. Activity log (the feasible part of Tailscale's "Logs")** · M — *1.1.0*
  Configuration changes (who changed what and when, with before/after for ACL
  and DNS), device events (registered, connected/disconnected history, removed,
  key expired, client version changes) and sign-ins from Authentik. Page with
  search, filters, CSV export and retention. Network flow logs are not feasible
  with Headscale.
- [x] **7. Custom DNS records** · S — *1.0.7*
  Headscale supports `dns.extra_records` (e.g. `nas.example` → `100.64.0.5`);
  the web UI always writes it empty.
- [x] **8. Add devices with a QR code** · S — *1.1.0*
  In Add device, a QR code with the server URL (and optionally an auth key):
  setting up the alternate server on iPhone/Android is the clumsiest step.

## 🟡 Medium — extends what can be done

- [ ] **9. Visual ACL policy editor** · L
  Forms for groups, tags and "who can reach what", plus a test ("can ana reach
  nas:445?"). Today there is only the HuJSON text editor.
- [ ] **10. Auto-approval of routes and exit nodes** · S/M
  Expose Headscale's `autoApprovers` (subnets or exit nodes of certain tags are
  approved automatically) on the routes page.
- [ ] **11. Tailscale SSH rules** · M
  Headscale supports the `ssh` section of the policy: a page to define who can
  SSH into which machines without managing SSH keys.
- [ ] **12. More roles** · M
  Besides admin and member: "Network admin" (ACL and DNS only) and "Auditor"
  (read-only), like Tailscale, built on Authentik groups.
- [ ] **13. Bulk actions** · S
  Select several machines to expire, remove or tag them at once.

## 🟢 Low — polish and advanced cases

- [ ] **14. Webhook notifications** (Slack, Telegram, ntfy): new or expired device · M
- [ ] **15. Server status page**: Headscale version with update notice, container health, disk use, basic metrics · S/M
- [ ] **16. DERP relay status**: region and latency per device, editor for an own DERP map · M
- [ ] **17. PostgreSQL support** for large tailnets instead of SQLite · M
- [ ] **18. More languages**: French, German, Portuguese (the structure is ready) · S each
- [ ] **19. Faster renaming of "localhost" devices**: check every 5 s instead of 30 s · S

## Suggested order

1. **Now:** 1 and 3 — small, and they prevent a broken or insecure installation.
2. **Next release (1.1):** 2, 4, 5 and 7 — reliable and comfortable for several users.
3. **Then:** 6 and 8, and 9 as the big project.

## Done

Shipped items move here with their version.

- [x] Tailscale-style web console, per-user isolation, Authentik with Google sign-in, installer — 1.0.0
- [x] Own sign-in/sign-out flows, unique add-user emails, Apple `localhost` renaming — 1.0.1
- [x] Live updates on Machines, machine details and Users — 1.0.2
- [x] Device key expiry (180 days, editable) — 1.0.3
- [x] Automatic renewal of the web UI's API key — 1.0.4
- [x] Two-factor authentication (required for admins by default) — 1.0.5
- [x] Scheduled backups and one-command restore — 1.0.6
- [x] DNS page like Tailscale's, invitations and password reset, expiry warnings, activity log, QR codes — 1.1.0
