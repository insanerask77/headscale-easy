# Roadmap

What comes next for Headscale Easy, most relevant first. Tick an item when it
ships and note the version. Ideas and votes are welcome in
[issues](https://github.com/insanerask77/headscale-easy/issues) and
[discussions](https://github.com/insanerask77/headscale-easy/discussions).

Effort: **S** = hours · **M** = one or two days · **L** = several days.

## 🧭 2.0 — Simplification

One container and one command for the simple case, the same image plus
external pieces for advanced setups. Details, tasks and acceptance criteria in
[SIMPLIFICATION_PLAN.md](https://github.com/insanerask77/headscale-easy/blob/main/SIMPLIFICATION_PLAN.md); the work happens on the
`next` branch (see [CONTRIBUTING.md](https://github.com/insanerask77/headscale-easy/blob/main/CONTRIBUTING.md#branches-and-releases)).

- [x] **P0. Device sign-in without OIDC** (spike) · S
- [x] **P1. Local accounts in the console** (password, TOTP, invitations) · L
- [x] **P2. All-in-one image + first-run setup wizard** · L
- [x] **P2.5. Findings from testing the AIO image** (embedded DERP, user credentials, sign-up modes, base domain, live status, Docker tab) · M
- [x] **P3. Built-in backups** (nightly schedule, `hse backup` / `hse restore`, status card, sync sidecar) · M
- [ ] **P4. Advanced edition** (`deploy/compose`, external OIDC/PostgreSQL examples) · M
- [ ] **P5. Migration from 1.x and deprecations** · M
- [ ] **P6. Docs and clean-up** · M

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
  `make backup`, tested `make restore` (also on a new server). Remote
  copies (S3, B2, SFTP via rclone, or rsync over SSH) with remote retention and
  `make restore file=remote:...` — *next*.
- [x] **3. Two-factor authentication (MFA) in Authentik** · S — *1.0.5*
  The web UI controls the whole network behind a password. Authentik already
  has TOTP and passkeys: enable them in the blueprint, optional for members and
  required for admins.
  The mode (admins, everyone or optional) can be changed live by admins from
  Settings → General in the web UI.

## 🛡️ Security and trust — from the community's feedback

The project is young, AI-assisted and security-sensitive: making that clear
and verifiable comes before new features.

- [x] **S1. Security notice, SECURITY.md and AI usage** · S — *1.4.0*
  Visible warning in the README and docs, what is exposed, known limitations,
  how to report vulnerabilities, `AI_USAGE.md`.
- [x] **S2. Permission-boundary tests** · M — *1.4.0*
  `tests/test_security.py`: sessions, CSRF, admin-only pages and actions,
  member ownership, path traversal, redirects, OIDC admin-by-email, demo mode.
- [ ] **S3. Independent security review** · L
  Ask for a third-party review of sign-in, sessions, the Docker socket and the
  installer; publish the findings and fixes.
- [ ] **S4. End-to-end tests in CI** · L
  Start the real stack (Headscale + console, with and without Authentik) in CI
  and test auth, OIDC, API, ACL, DNS, devices, backup/restore against it.
- [x] **S5. Revocable sessions and sign-in rate limiting** · M — *next*
  Server-side session list (sign out everywhere, role changes take effect at
  once) and per-IP limits on sign-in attempts.
- [x] **S6. Narrower Docker access** · M — *next*
  Replace the raw Docker socket with a restricted proxy or a tiny helper that
  can only validate and restart Headscale. Done: `hs-helper` holds the socket
  and serves only configtest, restart and status; the web UI has no socket.
- [x] **S7. Demo mode** · S — *1.4.0*
  `DEMO_MODE=true`: banner on every page, access-granting and destructive
  actions disabled.
- [x] **S8. Positioning and docs** · S — *1.4.0*
  "Why Headscale Easy?", comparison with Headplane, end-to-end workflow,
  architecture, measured resource usage, 5-minute quick start, production and
  hardening guide, troubleshooting.

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

- [x] **9. Visual ACL policy editor** · L — *1.2.0*
  Forms for groups, tags and "who can reach what", plus a test ("can ana reach
  nas:445?"). The raw HuJSON editor stays as a full fallback (Advanced tab);
  edits from the visual editor splice only the block they touch, so comments
  and hand-written sections (`ssh`, `autoApprovers`) are never lost.
- [x] **10. Auto-approval of routes and exit nodes** · S/M — *1.3.0*
  Expose Headscale's `autoApprovers` (subnets or exit nodes of certain tags are
  approved automatically) on a new Auto-approval tab in Access controls.
- [x] **11. Tailscale SSH rules** · M — *1.3.0*
  Headscale supports the `ssh` section of the policy: a page to define who can
  SSH into which machines without managing SSH keys.
- [x] **12. More roles** · M — *1.3.0*
  Besides admin and member: "Network admin" (ACL and DNS only) and "Auditor"
  (read-only), like Tailscale, built on Authentik groups.
- [x] **13. Bulk actions** · S — *1.3.0*
  Select several machines to expire, remove or tag them at once.

## 🟢 Low — polish and advanced cases

- [x] **14. Webhook notifications** (Slack, Telegram, ntfy): new or expired device · M — *next*
- [x] **15. Server status page**: Headscale version with update notice, container health, disk use, basic metrics · S/M — *next*
- [x] **16. DERP relay status**: region and latency per device, editor for an own DERP map · M — *next*
- [x] **17. PostgreSQL support** for large tailnets instead of SQLite · M — *next*
  Bundled or external PostgreSQL chosen in the installer; the web UI reads it
  with a read-only role through a small stdlib client; backups use `pg_dump`.
- [x] **18. More languages**: French, German, Portuguese · *next*
- [x] **19. Faster renaming of "localhost" devices**: check every 5 s instead of 30 s · S (*next*)

## Suggested order

Items 1–13 are done — the Critical, High and Medium sections are complete.
What's left is all 🟢 Low priority, roughly in order:

1. Webhook notifications (14) and the server status page (15) are the most
   generally useful.
2. DERP relay status (16) and PostgreSQL support (17) are for larger or
   more specific deployments.
3. More languages (18) and the "localhost" rename delay (19) are small,
   pick up anytime.

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
- [x] Visual ACL policy editor (rules, groups, tag owners, access simulator) — 1.2.0
- [x] Auto-approval of routes/exit nodes, SSH rules, bulk machine actions, Network admin and Auditor roles — 1.3.0
