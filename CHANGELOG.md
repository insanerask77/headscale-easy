# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] - Unreleased

Work in progress on the `next` branch: see `SIMPLIFICATION_PLAN.md`.

### Added
- Built-in backups for the all-in-one image (plan phase 3). The container backs
  itself up every night at 03:00 (`BACKUP_SCHEDULE`, `off` disables it) and keeps
  14 days (`BACKUP_KEEP_DAYS`) in `/data/backups`, with no extra container. One
  `.tar.gz` holds a consistent copy of Headscale's database (while it runs; a
  `pg_dump` with an external PostgreSQL), its keys, the console's accounts and
  activity log, `config/` and Caddy's internal CA, with a `meta.json` and a
  SHA-256 of every file. Each backup is read back and verified, the newest
  successful one is never pruned, and sessions are not included.
  - `docker exec <c> hse backup`, `hse backups` and `hse restore <file>`. A
    restore validates the archive first, takes a pre-restore backup and puts it
    back if it fails; it works on a stopped volume (also on a new host) or on the
    running container.
  - A **Backups** menu (administrators) shows the last backup, the next run and a
    **Back up now** button, lets you turn scheduled backups on/off and change the
    schedule and days kept (values fixed by `BACKUP_SCHEDULE` / `BACKUP_KEEP_DAYS`
    stay read-only), lists the backups with **Download** and **Restore** (typed
    confirmation, safety copy first, a page waits for the restart) and takes an
    uploaded backup (**Upload**, or **Upload and restore**; streamed to disk and
    checked before it is kept, `BACKUP_UPLOAD_MAX_MB`). Everything is audited; a
    failed scheduled backup sends a notification.
  - The `backup` image gains `BACKUP_MODE=sync`: a sidecar that uploads the
    archives of `/data/backups` to the remote (rclone / rsync) for the advanced edition.
  - The archive keeps `backup/backup.sh`'s directories but adds `meta.json` and
    `console/`; `scripts/restore.sh` (1.x) refuses it and points to `hse restore`.
  - CI restores a backup for real (offline and online), waits for a scheduled
    run and fails above 100 MB of RAM also while a backup runs.
- Device sign-in without OIDC (plan phase 0). With `AUTH_PROVIDER=none` the
  link `tailscale up` prints (`<url>/register/<auth id>`) now opens an approval
  page in the console instead of Headscale's "run this command" page: Caddy
  redirects it to `/admin/register/<auth id>`, the console asks the person to
  sign in (and comes back to the page afterwards), and approving registers the
  device through Headscale's API. Members and network admins can only add
  devices to their own user, admins choose the owner, auditors cannot approve.
  Re-run `./install.sh` once to regenerate the Caddyfile.
- All-in-one image, preview (plan phase 2): `ghcr.io/insanerask77/headscale-easy-aio`
  runs Headscale, Caddy and the console in one non-root container under a small
  Python supervisor (restart with backoff, ordered shutdown, the hs-helper
  protocol on a local socket, `hse health` / `hse reload`). No Docker socket.
  State lives in one `/data` volume.
  - First-run wizard (styled like the console): a one-time token in the logs, then
    language, public URL and HTTPS, administrator, tailnet and isolation, backup
    settings. Two-factor is optional there; local accounts without it get a
    popup in the console (once per browser session) suggesting to enable it. It creates the API key, the Headscale user and the isolation
    policy, and can be retried safely. With `HSE_PUBLIC_URL` set the container
    starts without the wizard (headless).
  - `aio/render.py` ports the installer's config generators (output verified
    against `install.sh`).
  - CI builds the image, smoke-tests both modes and fails above 250 MB or
    100 MB of idle RAM (about 55 MB and 65 MB today); the image is published
    next to the others.
  - The wizard's isolation policy also allows `autogroup:internet`, so a user's exit nodes
    work (the isolation-only policy let devices connect to an exit node but forwarded nothing).
  - `web/status.py` reads its disks from `STATUS_DISKS`.
  - See `docs/all-in-one.md`. The 1.x installer and the split compose are
    unchanged.
- All-in-one image, findings from testing it (plan phase 2.5):
  - **Embedded DERP by default.** `HSE_DERP_MODE` = `embedded` (default: the
    container's own DERP + STUN on 3478/udp, no third-party relay), `public`
    or `custom` (+ `HSE_DERP_URL`); `DERP_USE_PUBLIC` still works as an alias.
    New wizard step; the console's DERP page shows the mode in use.
  - **Users with a password.** Users → Create local user takes an email, a
    password and a role; a temporary password forces a change at first sign-in.
    **Set password** per user signs out their open sessions.
  - **Self-registration.** `HSE_SIGNUP` = `off` (default; `/admin/signup`
    answers 404) | `invite` | `open`, also in the wizard and Settings → General.
    Self-registered accounts are always Members. Invitation keys (Users page):
    single or multi use, optional expiry, revocable, stored hashed and shown
    once; a wrong, expired or used-up key gets one generic error. Rate limited
    and CSRF protected.
  - **MagicDNS base domain** is configurable (`HSE_BASE_DOMAIN`, wizard field),
    default `hse.net` in this image; still editable on the DNS page.
  - **Live device status** over Server-Sent Events (`/admin/events`): one shared
    poller, per-user filtering, "Live" indicator, falls back to polling.
  - **Add device → Docker**: `docker run` / `docker-compose.yml` for the official
    `tailscale/tailscale` image against this server, with host name, exit node,
    subnet routes and userspace options, and an optional single-use auth key
    that is shown once. It warns when the server address is `localhost`.
  - The console's language selector works again with five languages (the
    buttons overflowed the user menu).
  - `scripts/aio-smoke.sh` also checks the embedded DERP config, STUN, sign-up
    off and the event stream's authentication.

## [Unreleased]

## [1.5.0] - 2026-10-03

Upgrade: run `./install.sh` again to pick up the new settings and the `hs-helper` service.
An older `docker-compose.yml` that still mounts the Docker socket in `web` keeps working,
with a warning in the logs. Existing installs keep their ACL policy: see *Fixed*.

### Security
- Sessions are now revocable: the signed cookie carries a session id that must
  exist, unrevoked, in `data/web/sessions.db` (`600`). New **Settings →
  Sessions** page: your sessions (admins and auditors: everybody's) with
  *Log out*, *Sign out everywhere* and, for admins, *Sign out everyone else*.
  Deleting a user, or a role change seen at their next sign-in, revokes their
  older sessions. Cookies issued before this change have no session id: users
  sign in again once.
- Sign-in rate limiting per client IP (`SIGNIN_RATE_LIMIT`, default 10, per
  `SIGNIN_RATE_WINDOW`, default 600 seconds) on the API key sign-in and the
  OIDC sign-in: `429` with `Retry-After` and an `auth.rate_limited` activity
  event. New activity events: `auth.session_revoked`,
  `auth.sessions_revoked_all`.
- The web UI no longer mounts the Docker socket (equivalent to root on the
  host) and is no longer in the Docker group. A new `hs-helper` service
  (`helper/`, image `ghcr.io/insanerask77/headscale-easy-helper`) is the only
  container with the socket. It has no network and answers the web UI over a
  `660` Unix socket in the `hse-helper` volume with exactly three fixed
  operations on the `headscale` container: `POST /configtest`,
  `POST /restart` and `GET /status` (container health and Headscale's
  version). It takes no parameters; anything else is refused before Docker is
  contacted. Installations with an older `docker-compose.yml` that still
  mounts the socket in `web` keep working, with a warning in the logs.
  `make validate` fails if any other service mounts the socket.

### Added
- The console is now available in French, German and Portuguese (Brazilian),
  next to English and Spanish: full catalogs in `web/locales/{fr,de,pt}.json`
  and `{fr,de,pt}.d/`, offered in the language selector in Settings. `UI_LANG`
  accepts `fr`, `de` and `pt`; the installer lets you choose them and keeps its
  own messages in English for those languages.
- PostgreSQL support for Headscale's database (roadmap item 17). The installer
  asks: SQLite (default, recommended), PostgreSQL in the stack (new
  `headscale-postgresql` container, `postgres` Compose profile, volume
  `headscale-db`) or your own PostgreSQL server (host, port, database, owner,
  password, TLS mode). New `HEADSCALE_DB_TYPE` and `HEADSCALE_PG_*` settings
  in `.env`; switching type warns that data is not migrated.
- `web/pgwire.py`: a minimal read-only PostgreSQL client written with the
  standard library only (protocol v3, SCRAM-SHA-256 with server signature
  check, cleartext password, optional TLS with libpq's `sslmode` values,
  simple query; the legacy MD5 method is refused). The web image still has no
  third-party dependencies.
- The web UI reads device Hostinfo (OS, Tailscale version, DERP, endpoints)
  from PostgreSQL through its own read-only role (`HEADSCALE_PG_RO_USER`,
  created by `templates/headscale-pg-readonly.sql`): `SELECT` on three
  columns of `nodes` only, read-only sessions; it never gets Headscale's
  credentials.
- Backups and restore support PostgreSQL: `pg_dump` of Headscale's database
  (`headscale/headscale.sql` in the archive) and `make restore` loads it back
  and re-creates the read-only role.
- `tests/test_postgresql.py`: the client against a fake server (recorded
  protocol messages, a real SCRAM server side, MD5 refused, errors, TLS refusal),
  RFC 7677 SCRAM and RFC 4013 SASLprep vectors, Hostinfo from PostgreSQL on
  the machine page.
- Server status page (**Settings → Status**, admins and auditors): Headscale
  and Headscale Easy versions with an update notice (GitHub releases, cached
  12 h; `STATUS_UPDATE_CHECK=false` turns it off), container health from
  `hs-helper`, disk use of `/data` and `/headscale`, devices online and basic
  Headscale metrics. Each source fails independently.
- Remote backups: set `BACKUP_REMOTE` and every backup is also uploaded to S3,
  B2, SFTP... (any rclone remote) or to a server with rsync over SSH
  (`rsync:user@host:/dir`). Remote retention with `BACKUP_REMOTE_KEEP_DAYS`;
  credentials in `data/backup-remote/`. The installer asks for it, and
  `make restore file=s3:bucket/dir/headscale-easy-....tar.gz` downloads the
  backup first. A failed upload is reported without losing the local backup.
- `tests/test_backup_remote.py`: tests `backup/remote.sh` against a local
  destination with fake rclone/rsync.
- DERP relay status: the machine detail shows the preferred relay with its
  latency and the latency to every relay the device measured; the Machines
  list has a Relay column.
- New **DERP relays** page (Network): which relays the devices use and their
  median latency, and an editor (admins) for your own DERP map. It writes
  `headscale-derp.yaml` and points `derp.paths` of `config.yaml` at it (a
  marked block, like DNS), validates it with `headscale configtest`, restarts
  Headscale and restores the previous map if anything fails. Run
  `./install.sh` once on existing installs to add the block and the file.
- Webhook notifications (roadmap item 14): Slack, Telegram, ntfy and a generic
  JSON webhook, for new, expired, about-to-expire and removed devices.
  Configured with `NOTIFY_URLS` / `NOTIFY_EVENTS` (the installer asks,
  optionally); sending is in the background with a timeout and retries, and
  never slows down or breaks the web UI. **Settings -> General ->
  Notifications** lists the destinations and has a "Send a test" button
  (admins; blocked in the demo).

### Changed
- Machines that register as `localhost` are now renamed within about 5 seconds
  (was 30). Each pass is one node-list call; host details are only fetched when
  something needs renaming. Tune it with `RENAME_INTERVAL` (seconds, minimum 1).

### Fixed
- Exit nodes had no internet with `NETWORK_ISOLATION=true`: the policy the installer applies only
  allowed `autogroup:self`, so an exit node accepted connections but forwarded nothing. It now also
  allows `autogroup:member` -> `autogroup:internet:*`. Existing installs keep their policy; add the rule
  in Access controls (see the configuration docs).

## [1.4.0] - 2026-09-30

### Security
- An email in `PORTAL_ADMIN_EMAILS` no longer grants admin when the OIDC
  provider says it is not verified (`email_verified: false`). Before, with a
  provider that lets users set an unverified email, anyone could claim an
  admin's address.
- The activity log database (`data/web/audit.db`, with emails and client IPs)
  is now readable only by the web UI's user (`600`); it was world-readable.
- New `DEMO_MODE=true` for public demo instances: a "DEMO ENVIRONMENT" banner
  on every page and the actions that grant access or change things for
  everyone (auth keys, API keys, registering devices, invitations, reset
  links, users, DNS, the ACL policy (visual editor and Advanced), two-factor,
  key expiry, removing or expiring machines, one by one or in bulk) are
  refused.

### Added
- `tests/test_security.py`: permission-boundary tests run in CI (forged and
  expired sessions, CSRF, admin-only pages and actions, members limited to
  their own machines and keys, path traversal, redirects, OIDC
  admin-by-email, demo mode).
- Documentation: security notice in the README and docs; `SECURITY.md` with
  what is exposed, review and test status, known limitations and how to
  report; `AI_USAGE.md`; new pages *Why Headscale Easy?* (the problem it
  solves, comparison with Headplane, end-to-end workflow), *Production and
  hardening* and *Architecture and resources* (with measured RAM, CPU and disk
  use); a 5-minute quick start; more troubleshooting (OIDC, HTTPS, front
  proxies, device registration, DNS, ACL).
- Contributing: pull requests say whether they were AI-assisted, and changes to
  sign-in, sessions, permissions or the Docker socket need a security test.

### Changed
- The project is described as what it is — a deployment and management layer
  around the official Headscale — rather than "the open source Tailscale
  alternative".

## [1.3.1] - 2026-09-30

### Fixed
- **SSO login behind proxies that block Python's default user agent.** The
  console now identifies its outbound OIDC requests (discovery, token
  exchange, userinfo) as `headscale-easy/<version>`. Some identity-provider
  proxies reject `Python-urllib`: Pocket ID behind Cloudflare answered
  discovery with HTTP 403 / Error 1010, which broke sign-in. Thanks to
  [@vhryniv](https://github.com/vhryniv) for the report and the fix (#21).

### Changed
- The README now lists the project's contributors.

## [1.3.0] - 2026-09-30

Closes out the roadmap's Medium-priority section (items 10-13).

### Added
- **Auto-approval of routes and exit nodes.** A 5th "Auto-approval" tab in
  Access controls exposes Headscale's `autoApprovers` policy section:
  declare which tag, group or user gets a subnet route (or the exit node
  role) approved automatically, instead of approving each device by hand.
  Verified live: a tagged test device's advertised route and exit-node role
  were both approved with zero manual steps.
- **Tailscale SSH rules.** A 6th "SSH rules" tab: who can SSH into which
  machines, as which host users, with optional periodic re-authentication —
  no SSH keys to manage. Tailscale SSH itself still needs turning on per
  device (`tailscale up --ssh`).
- **Bulk actions on machines.** Tick several rows in the Machines table (or
  the header checkbox for all of them) to expire keys, add a tag or remove
  them all at once, instead of one at a time.
- **Network admin and Auditor roles**, both opt-in and built on Authentik
  groups (`PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS`): a network
  admin edits the ACL policy and DNS only; an auditor sees everything an
  admin sees — every machine, Users, DNS, Access controls, Logs — but can
  never change anything, anywhere. No Authentik blueprint changes needed:
  the existing `profile` OIDC scope already sends every group a user belongs
  to.

## [1.2.0] - 2026-09-29

### Added
- **Visual ACL policy editor.** Access controls now has four tabs: **Rules**
  (who can reach what, with source/destination/port/protocol forms),
  **Groups & tags** (reusable groups and tag owners), **Test access** (a
  simulator: pick a source and a destination and it says whether the policy
  allows it and which rule matched — not a live packet test), and **Advanced
  (HuJSON)**, the original text editor kept as a full fallback. Under the
  hood, visual edits rewrite only the `acls`, `groups` or `tagOwners` block
  they touch and leave the rest of the file — comments, key order, hand-written
  `ssh` or `autoApprovers` sections — untouched. Verified against a live
  Headscale instance: real device connectivity (per-user isolation and a
  tag-based rule) matched the simulator's predictions in every case tested.
- **Optional public DERP servers.** The installer now asks whether to also use
  Tailscale's public DERP relays (default: yes); answering no gives a fully
  self-hosted install with only the embedded relay (`DERP_USE_PUBLIC` in
  `.env`). `uninstall.sh` gains a `--lang en|es` option.

## [1.1.0] - 2026-09-29

Built by five agents working in parallel, one feature each, then integrated
and tested together.

### Added
- **Invitations and password reset.** Admins invite people from Users →
  Invite user (member or admin, optional email, 1–30 days, QR code for the
  link); the person chooses their own user name and password. Pending
  invitations can be copied again or revoked. "Password reset link…" makes a
  single-use link for any account, no email needed. Optional SMTP (asked by the
  installer) adds "Forgot password?" on the sign-in page and lets invitations
  and reset links be emailed.
- **Expiry warnings and inactive machines.** An orange "Expires soon" badge
  (14 days, `EXPIRY_WARNING_DAYS`), a notice at the top of Machines with a link
  that filters them, "Expiring soon" and "Offline for 30+ days" filters
  (`INACTIVE_DAYS`), and bulk removal of inactive machines (admins, with
  confirmation; each machine is checked again before removal).
- **Activity log** (Logs page, admins): configuration changes made in the web
  UI (machines, users, keys, ACL with a diff, DNS before/after, key expiry,
  two-factor, invitations, password resets), console sign-ins and failed
  sign-ins with the client IP, and device events (registered, removed,
  connected, disconnected, key expired, Tailscale version changed, renamed
  outside the console). Search, filters, CSV export, live updates. Kept
  `AUDIT_RETENTION_DAYS` (90) in `./data/web/audit.db`, included in backups.
  Secrets are never stored.
- **QR codes** in Add device (server URL on the iOS and Android tabs, whose
  steps now follow Tailscale's current custom-server flow) and for new auth
  keys, generated by the web UI itself (no JavaScript or third-party code).
- **DNS page like Tailscale's**: the implicit MagicDNS nameserver
  (100.100.100.100), split DNS and global nameservers as rows with add/remove,
  "Use local DNS settings", the tailnet domain as the fixed first search
  domain, custom records as rows, and confirmation before renaming the tailnet
  or disabling MagicDNS. Works without JavaScript.
- Unit tests (`make test`, run in CI).

### Fixed
- The Spanish strings of custom DNS records were lost in 1.0.7.

## [1.0.7] - 2026-09-29

### Added
- Custom DNS records in the DNS page (`name address`, A or AAAA), resolved by
  every device of the tailnet (Headscale's `dns.extra_records`).

## [1.0.6] - 2026-09-29

### Added
- Optional daily backups: the installer asks (off by default, recommended) and
  lets you choose the time, folder and retention. A `backup` container
  (Compose profile `backup`) backs up Headscale's database and private keys,
  Authentik's database, the configuration and Caddy's internal CA. The
  installer makes a first backup right away and shows the exact restore
  command.
- `make restore file=...` (`scripts/restore.sh`): restores all of it, also on a
  new server. `make backup` makes a one-off backup, with or without the
  schedule.

### Fixed
- `make backup` copied Headscale's SQLite files while Headscale was writing,
  which could produce an inconsistent database. It now uses SQLite's online
  backup and checks the copy's integrity.

## [1.0.5] - 2026-09-29

### Added
- Two-factor authentication with the built-in Authentik: an authenticator app
  (TOTP) or a passkey after the password. Required for admins by default;
  `MFA_REQUIRED` (asked by the installer) can make it required for everyone or
  optional. Users who already have a second factor are always asked for it.
- Admins change the two-factor mode live from the web UI (Settings → General
  → Two-factor authentication), without reinstalling. The web UI uses an
  Authentik API token (`PORTAL_AUTHENTIK_TOKEN`, generated by the installer)
  of a service account that may only read and change the two-factor policy;
  restarting Authentik keeps the admin's choice, and `MFA_REQUIRED` is the
  initial value (re-running the installer applies the value chosen there).
  Without the built-in Authentik the section is hidden; without the token it
  is read-only.

## [1.0.4] - 2026-09-29

### Fixed
- The web UI stopped working 90 days after installing, when its Headscale API
  key expired. It now renews the key by itself 15 days before (keeping the new
  one in `data/web/api-key` and expiring the old one); the installer reuses the
  renewed key. If no valid key is left, the web UI says how to fix it instead
  of showing a generic error.
- API key prefixes containing "-" were misread, which could hide the "Used by
  Headscale Easy" mark on the web UI's own key.

## [1.0.3] - 2026-09-29

### Fixed
- Devices were always added with key expiry disabled, whatever the auth key's
  expiry: Headscale's `node.expiry` defaults to never. New devices now expire
  after 180 days, like in Tailscale, and admins change it in Settings →
  General → Device management. The auth key dialog explains that its expiry
  only limits until when the key can add devices.
- Live updates stopped while any menu or dialog was open, e.g. Add device or a
  freshly generated key, exactly while adding a device. Now only menus and
  dialogs inside the refreshed area pause them, and the page also refreshes
  when the window gets the focus back.

## [1.0.2] - 2026-09-29

### Added
- Live updates: the Machines, machine details and Users pages refresh on
  their own every few seconds, so machines appear, connect and disconnect
  without reloading. Paused while the tab is hidden or a menu or dialog is
  open.

### Changed
- Automatic naming of machines called `localhost` logs whether it is on and
  what it finds, to diagnose devices it could not rename.

## [1.0.1] - 2026-09-29

### Fixed
- The sign-in page said "Welcome to authentik!" and the "Sign in with Google"
  button could disappear: Authentik resets its default flows, so Headscale Easy
  now has its own sign-in, sign-out and Google flows.
- Signing out could leave a blank page or a half-closed session when the web UI
  had been open for more than an hour (expired ID token). It now always ends
  the Authentik session and returns to the web UI with "You have signed out".
- The add-user form accepted an email (or user name) already used by another
  account, including one created by signing in with Google.
- Apple devices registered from the Tailscale app were called `localhost`: the
  web UI now renames them to `<owner>-<device>` (e.g. `ana-iphone`). Disable
  with `AUTO_RENAME_LOCALHOST=false`.

## [1.0.0] - 2026-09-28

First release as **Headscale Easy**.

### Added
- Web console modelled on Tailscale's admin panel, at `/admin`: machines
  (status, addresses, OS and client version, rename, expire, remove, key
  expiry, tags, subnet routes, exit nodes, filters, search, CSV export), users,
  DNS editor, ACL policy editor, auth and API keys, register by auth ID.
- Members see and manage only their own devices; admins manage everything.
- English and Spanish, in the installer and the console.
- Docker image `ghcr.io/insanerask77/headscale-easy` (amd64, arm64).
- Built-in Authentik with a themed login, a simple add-user form at `/add-user`
  and optional Google sign-in.
- Per-user network isolation policy (`autogroup:self`) on new installs.
- Ready-made configuration for Nginx Proxy Manager, nginx, Traefik and Caddy
  when another proxy terminates TLS.
- Documentation site on GitHub Pages, in English and Spanish:
  https://insanerask77.github.io/headscale-easy/

[1.5.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.5.0
[1.4.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.4.0
[1.3.1]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.3.1
[1.3.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.3.0
[1.2.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.2.0
[1.1.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.1.0
[1.0.7]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.7
[1.0.6]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.6
[1.0.5]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.5
[1.0.4]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.4
[1.0.3]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.3
[1.0.2]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.2
[1.0.1]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.1
[1.0.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.0
