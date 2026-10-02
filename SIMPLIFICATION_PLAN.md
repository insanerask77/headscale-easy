# Simplification plan — Headscale Easy 2.0

Working document for development. It turns the "do we really need all of
this?" analysis into phases with tasks and acceptance criteria. Tick tasks as
they land; when a phase ships, add it to [ROADMAP.md](ROADMAP.md) with its
version.

Effort: **S** = hours · **M** = one or two days · **L** = several days.

---

## 1. Goal

- **Simple edition**: one container, one command, minimal configuration
  (a first-run setup wizard in the browser or a handful of env vars).
- **Advanced edition**: the same image plus external pieces (OIDC provider,
  PostgreSQL, front proxy, remote backups) for companies and people who want to
  customise the deployment.
- **Less resource use and fewer pieces**: remove components that duplicate
  work, and the Docker socket.

### Non-goals

- Changing Headscale. We keep running the official, unmodified binary.
- Removing features users rely on. Each removed piece either moves into the
  console or becomes an external option.
- Multi-instance or HA Headscale (Headscale does not support it).

---

## 2. Current state (1.x)

| Piece | RAM (idle) | Notes |
|---|---|---|
| `headscale` | 16–25 MB | The product |
| `caddy` | 13–15 MB | Single domain, TLS, routes `/`, `/admin`, `/authentik` |
| `headscale-easy` (console) | 28 MB | Python stdlib only |
| `hs-helper` | a few MB | Docker socket, only for `configtest` / restart / status |
| `authentik-server` + `worker` + `postgresql` | **~1.07 GB**, 2 GB image | Passwords, 2FA, invitations, reset, Google, groups |
| `headscale-postgresql` | 50–170 MB | Optional, a second PostgreSQL |
| `backup` | a few MB | Cron + tar + rclone/rsync |

- Up to **8 containers, ~1.2 GB RAM**; the useful core is **~65 MB**.
- `install.sh` (1705 lines of bash) generates `.env`, `headscale-config.yaml`,
  `Caddyfile`, `docker-compose.override.yml` and the DERP map, so there is no
  one-command deployment.
- `authentik/blueprints/headscale.yaml` (1393 lines), `web/accounts.py` and
  `web/mfa.py` are coupled to Authentik's API.

### Redundancies found

1. **Authentik** is a full IdP plus its own PostgreSQL, and the simple case
   only uses it for user + password + 2FA + invitation/reset links. The console
   already has server-side sessions, rate limiting, an audit log and SQLite.
2. **Two PostgreSQL servers** can run at the same time. Headscale recommends
   SQLite.
3. **hs-helper** exists only because Headscale and the console run in separate
   containers and the console must not hold the Docker socket.
4. **The backup container** duplicates a scheduler that a long-running process
   can provide.
5. **Bash config generation** duplicates logic that has to run at container
   start anyway for a one-command deployment.

### Already in place (makes this cheaper)

- Registering a node by Auth ID through the API already exists:
  `register_node` in `web/app.py` calls `POST /api/v1/auth/register` with
  `{user, authId}`. Today it is admin-only, from a dialog on Machines.
- The policy is stored in the database (`policy.mode: database`), so ACL
  changes need no restart.
- The console talks to the helper over a small Unix-socket protocol
  (`/configtest`, `/restart`, `/status`). It can be reused as is.

---

## 3. Target

| | Simple edition | Advanced edition |
|---|---|---|
| Deploy | `docker run …` (or a 10-line compose) | `deploy/compose/` |
| Containers | **1** (AIO image) | AIO image + whatever external pieces you choose |
| Database | SQLite | SQLite or **external** PostgreSQL |
| Sign-in | **Local accounts** in the console (password + TOTP, invitations, reset) and optionally external OIDC | External OIDC (Authentik, Keycloak, Pocket ID, Google…), local accounts, API key |
| TLS | Built-in Caddy: `auto` (Let's Encrypt), `internal` or `off` (behind a proxy) | Same, plus front-proxy examples (nginx, Traefik, NPM) |
| Backups | Built in, local (mount a NAS folder at `/data/backups`) | Built in, plus the remote sidecar (rclone/rsync) |
| Docker socket | **None** | **None** |
| Target RAM (idle) | **< 100 MB** | Depends on the external pieces |
| Target image | **< 250 MB** | — |

```text
               ┌──────────────── headscale-easy (one container) ────────────────┐
:80/:443 ────▶ │ caddy ──┬─ /        ──▶ headscale (official binary, child proc)  │
:3478/udp ───▶ │         └─ /admin   ──▶ console (Python)                         │
               │ supervisor: starts/restarts the three, configtest, backups      │
               │ /data: headscale/ caddy/ console/ config/ backups/              │
               └─────────────────────────────────────────────────────────────────┘
       optional, outside: OIDC provider · PostgreSQL · front proxy · remote backup
```

---

## 4. Design decisions

- **D1 — One image for both editions.** The advanced edition reuses the AIO
  image and adds external services. This is a refinement of the first idea
  (separate services for the advanced edition): with one image there is a
  single thing to build and test, and the Docker socket goes away for everyone.
  The current split compose stays in maintenance mode during 1.x (see §6).
- **D2 — Local accounts replace bundled Authentik.** Passwords, TOTP,
  invitations and reset links live in the console. Authentik becomes one more
  "external OIDC" example. If phase 0 fails, the fallback is a light bundled IdP
  (Pocket ID, ~30 MB) instead of local accounts.
- **D3 — Device sign-in without OIDC.** Caddy sends the registration URL that
  Headscale prints to the console. The user signs in, approves, and the console
  calls `POST /api/v1/auth/register` for *their* Headscale user. This keeps the
  "open this URL and sign in" experience with no IdP.
- **D4 — No bundled PostgreSQL.** SQLite by default, external PostgreSQL as an
  option. The read-only role and `web/pgwire.py` stay for the external case.
- **D5 — A supervisor instead of hs-helper.** A small stdlib Python supervisor
  (PID 1 child, e.g. under `tini`) starts Headscale, Caddy and the console,
  restarts them if they crash and forwards signals. It also serves the **same
  helper protocol** on a local Unix socket, so `web/headscale.py` needs no
  changes. The console does not supervise Headscale itself, so a console crash
  never takes the control plane down.
- **D6 — One config renderer, in Python.** The `install.sh` generators
  (`generate_headscale_config`, `dns_block`, `database_block`,
  `generate_caddyfile`…) are ported to Python over the existing `templates/`.
  Precedence: env vars > `/data/config/settings.json` (from the wizard) >
  defaults. Any remaining installer calls the same renderer inside the image;
  there are never two generators.
- **D7 — Pin Headscale.** `HEADSCALE_IMAGE_TAG=latest` goes away. The AIO image
  embeds a pinned Headscale version (we depend on API paths such as
  `/auth/register`), and bumps go through CI.
- **D8 — Security is ours now.** Moving auth out of Authentik means our code
  stores passwords and second factors. This raises the priority of roadmap item
  **S3** (independent review) and needs new tests (§5, phase 1).

---

## 5. Phases

### Phase 0 — Spike: device sign-in without OIDC · S

The deciding experiment: if it works, nothing else in the plan needs an IdP.

- [ ] With OIDC disabled and the pinned Headscale version, record the exact URL
      that `tailscale up --login-server …` prints (path and Auth ID format).
      The current Caddyfile notes that Headscale uses `/auth/{id}`; confirm it
      for the no-OIDC case too.
- [ ] Caddy: redirect that path to `/admin/register/{id}` (302).
- [ ] Console: `GET /admin/register/<id>` → sign-in if needed → confirmation
      page ("Add this device to your account?") → `POST` with CSRF →
      `hs.api("POST", "/auth/register", {"user": <session user>, "authId": id})`.
      Members register only to themselves; admins may pick the user (reuse
      `register_node` and `AUTH_ID_RE`).
- [ ] Audit event `machine.register` and `device.registered` notification, as
      today.
- [ ] Check whether Headscale exposes anything about the pending request
      (hostname, OS) to show on the confirmation page. Optional.
- [ ] Check that registering against a Headscale user created via OIDC works
      (needed for the Authentik migration, phase 5).

**Done when** a fresh device runs `tailscale up --login-server`, opens the
link, signs in to the console (API key for the spike) and joins the right user
with no OIDC configured.

### Phase 1 — Local accounts in the console · L

- [ ] `web/local_accounts.py` + `/data/console/accounts.db` (mode 600):
  - `accounts(id, username, email, headscale_user, role, pw_hash, totp_secret,
    totp_last_step, recovery_codes, disabled, created, updated)`
  - `tokens(kind[invite|reset], token_hash, account_id, role, email, expires,
    used_at)`: tokens are stored hashed and are single use.
- [ ] Passwords: `hashlib.scrypt` (n=2^15, r=8, p=1, per-user salt),
      `hmac.compare_digest`, minimum length, no other rules.
- [ ] TOTP: RFC 6238 with `hmac`/SHA-1, 30 s, ±1 step, replay protection
      (`totp_last_step`), hashed recovery codes, QR via `web/qr.py`.
- [ ] `MFA_REQUIRED` modes (admins / everyone / optional) kept; `web/mfa.py`
      gets a local backend.
- [ ] Invitations and reset links: keep the UI in `web/accounts.py` behind a
      small backend interface (`LocalBackend`, `AuthentikBackend` only during
      deprecation). Accepting an invitation creates the Headscale user
      (`POST /api/v1/user`) and links it. SMTP stays optional.
- [ ] Roles (admin, network admin, auditor, member) stored per account. OIDC
      keeps today's mapping (`PORTAL_*_GROUPS`, `PORTAL_ADMIN_EMAILS`).
- [ ] Sign-in modes, combinable: `local` (default), `oidc`, `apikey`
      (emergency). Session `kind = "local"`; reuse rate limiting and
      revocable sessions.
- [ ] Bootstrap the first admin from the wizard (phase 2) or from
      `HSE_ADMIN_EMAIL` (+ optional `HSE_ADMIN_PASSWORD`; otherwise a one-time
      invitation link is printed to the logs).
- [ ] Self-service pages: change password, set up / reset 2FA, my sessions.
- [ ] Tests: `tests/test_local_accounts.py` (hashing, RFC 6238 test vectors,
      replay, single-use and expired tokens, rate limit, roles) and extend
      `tests/test_security.py`.

**Done when** the whole tailnet can be run with local accounts only:
invite → set password → enrol TOTP → sign in → register a device (phase 0) →
manage only one's own machines.

### Phase 2 — AIO image + first-run wizard · L

- [ ] `aio/Dockerfile`, multi-stage:
      `FROM headscale/headscale:<pinned>` and `FROM caddy:<pinned>` → copy the
      binaries into `python:3.13-alpine` + `web/` + `aio/`. Confirm the binary
      path in the distroless Headscale image.
- [ ] `aio/supervisor.py` (stdlib): starts/restarts processes with backoff,
      forwards SIGTERM, prefixes logs, serves the helper protocol (`configtest`
      runs `headscale configtest` locally; `restart` restarts the child;
      `status` reports per process). Port the useful parts of
      `helper/helper.py` and `tests/test_docker_helper.py`.
- [ ] `aio/render.py`: Python port of the `install.sh` generators (D6).
      Unit tests that compare the output with today's generated files for a
      few typical `.env` setups.
- [ ] `/data` layout: `headscale/`, `caddy/`, `console/`, `config/`
      (`settings.json`, rendered `config.yaml`, `Caddyfile`, `derp.yaml`),
      `backups/`.
- [ ] Env vars (reuse current names where they exist): `HSE_PUBLIC_URL`,
      `HSE_TLS=auto|internal|off`, `ACME_EMAIL`, `HSE_DERP_PORT`,
      `HSE_ADMIN_EMAIL`, `OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`,
      `HEADSCALE_DB_TYPE` + `HEADSCALE_PG_*`, `UI_LANG`, `TZ`, plus today's
      console knobs.
- [ ] **Setup mode**: with no settings and no `HSE_PUBLIC_URL`, Caddy serves
      only the console on :80. A one-time setup token is printed to the logs,
      so the first visitor cannot take over the server. Wizard steps: language →
      public URL → TLS mode → admin account + TOTP → tailnet name/base domain,
      per-user isolation → backups. On submit: write settings, render config,
      start Headscale + Caddy, create the API key locally
      (`headscale apikeys create`; `web/apikey.py` renews it as today) and the
      admin's Headscale user. Then apply the network policy.
- [ ] Runs as a non-root uid with `NET_BIND_SERVICE` only. Check whether
      Headscale really needs `NET_ADMIN` (today's compose adds it).
- [ ] Healthcheck covering the three processes.
- [ ] CI: build the image; smoke test (`HSE_PUBLIC_URL=http://localhost`,
      `HSE_TLS=off`): healthy, `/healthz`, `/admin/healthz`, create a pre-auth
      key; record RAM and image size and fail above the targets in §3.

**Done when** `docker run -d -p 80:80 -p 443:443 -p 3478:3478/udp -v hse:/data <image>`
→ wizard → working tailnet, idle RAM < 100 MB, image < 250 MB.

### Phase 3 — Built-in backups · M

- [ ] Scheduler in the supervisor (it can read every file in `/data`):
      `BACKUP_SCHEDULE`, `BACKUP_KEEP_DAYS` as today.
- [ ] Consistent copies with `sqlite3.Connection.backup()` (Headscale's
      `db.sqlite` while running, plus the console's DBs), `pg_dump` only with
      external PostgreSQL, plus keys, `config/` and Caddy's CA → one
      `.tar.gz`. **Same archive layout as `backup/backup.sh`**, so
      `scripts/restore.sh` keeps working.
- [ ] `docker exec <c> hse backup` / `hse restore <file>` subcommands.
- [ ] Status page: last backup, size, "Back up now" button.
- [ ] Remote copies stay in the advanced edition (current `backup` image as a
      sidecar reading `/data/backups`).

### Phase 4 — Advanced edition · M

- [ ] `deploy/compose/docker-compose.yml`: the AIO image + optional profiles:
      `backup-remote` (sidecar), and examples that live outside the main file.
- [ ] `deploy/examples/`:
  - `authentik/`: compose + the current blueprint moved from `authentik/`,
    configured as an external OIDC provider (the zero-change path for current
    Authentik users).
  - `pocket-id/`, `keycloak.md`, `google.md`.
  - `postgresql/`: external PostgreSQL + `templates/headscale-pg-readonly.sql`.
  - Front proxies: move `templates/front-*` here as documentation.
- [ ] `install.sh` shrinks to: install Docker if missing, ask 3–4 questions
      (or none, and point to the wizard), write a small `.env`, `docker compose
      up -d`. Rendering happens inside the image (D6).

### Phase 5 — Migration from 1.x and deprecation · M

- [ ] `scripts/migrate-to-2.sh`: stop the stack, take a backup with today's
      tool, copy the volumes (`headscale-data` → `/data/headscale`,
      `data/web` → `/data/console`, `caddy-data` → `/data/caddy`), convert
      `.env` → env/`settings.json`, re-render the config keeping the DNS block,
      `extra_records`, DERP map and key expiry (the policy is already in the
      database), start the AIO container. Test it on a copy of a real install.
- [ ] Authentik users, two paths:
  1. **Keep Authentik** as external OIDC (`deploy/examples/authentik`): no
     change for users.
  2. **Move to local accounts**: for each Headscale user with an Authentik
     `providerId`, create a local account (name/email from Authentik through
     the existing token, role from its groups) and generate reset links
     (shown to the admin as a list, or emailed with SMTP). Nodes are untouched;
     re-authentication after key expiry uses the phase 0 flow.
- [ ] Bundled PostgreSQL users: keep it as "external PostgreSQL" (point the AIO
      image at the existing container through the example). No automatic
      PostgreSQL → SQLite migration (Headscale has no tool for it); document
      that.
- [ ] Timeline: the last 1.x release announces the deprecation (README,
      CHANGELOG, a console banner); 2.0 removes the bundled Authentik,
      bundled PostgreSQL, `hs-helper` and the split compose.

### Phase 6 — Docs and clean-up · M

- [ ] README / README.es: the quick start is one command; the advanced edition
      is a section with links.
- [ ] `docs/architecture.md`: new diagram and resource table (measured in
      CI); `docs/configuration*.md`: env var reference; `docs/getting-started*`,
      `docs/hardening.md`, `docs/why.md`.
- [ ] Remove after 2.0: `authentik/` (moved to examples), the Authentik code in
      `web/accounts.py` and `web/mfa.py`, `helper/`, the
      `headscale-postgresql` and `authentik-*` services, most of `install.sh`,
      and the CI jobs for the removed images.
- [ ] CHANGELOG and ROADMAP entries.

---

## 6. Order and dependencies

```text
Phase 0 ──▶ Phase 1 ──▶ Phase 2 ──▶ Phase 3
                          │
                          └──▶ Phase 4 ──▶ Phase 5 ──▶ Phase 6
```

- Phase 0 decides D2/D3. If it fails, switch to the Pocket ID fallback and
  rewrite phase 1.
- Phase 1 is useful in 1.x already: it gives an IdP-free sign-in to the
  current compose.
- 1.x keeps receiving fixes until 2.0 ships; no new features go into the split
  compose or the Authentik integration.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Our own auth code (passwords, TOTP) has bugs | stdlib primitives only, RFC test vectors, `test_security.py`, independent review (S3) before 2.0 |
| Headscale changes the registration URL or the API | Pin Headscale (D7); CI test for the phase 0 flow on every bump |
| The setup wizard is hijacked before the owner gets to it | One-time token from the logs; setup mode only answers the wizard |
| Several processes in one container | A small supervisor with restarts and health per process; it is the usual pattern for self-hosted all-in-one images |
| Migration loses data | Backup first, re-render instead of copying files, tested on real installs, documented rollback (start 1.x again from the backup) |
| Losing "Sign in with Google" from bundled Authentik | Google directly as external OIDC (docs example) |

## 8. Open questions

- [ ] **Image name**: publish the AIO image as `headscale-easy-aio` during 1.x
      and take over `headscale-easy` in 2.0? (Today `headscale-easy` is the
      console image used by the split compose.)
- [ ] Keep the split compose after 2.0 if people ask for it, or drop it?
- [ ] Passkeys (WebAuthn) for local accounts: in 2.0 or later?
- [ ] Remote backups inside the AIO image (bundle rclone, +~50 MB) or only
      through the sidecar?
