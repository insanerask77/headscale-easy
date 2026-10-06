# Phase 4 Execution Plan — Advanced Edition

**Status:** ✅ Done on `feat/phase4-integration` (not merged yet). Beyond the plan: it found and fixed that, behind a
proxy, every sign-in was counted against the proxy's address (`HSE_TRUSTED_PROXIES` + `trusted_proxies_strict`); that
`cap_drop: ALL` made Caddy unrunnable and a root-owned backups volume crash-looped the image; that nothing exposed
who may sign in through the provider (`HSE_OIDC_ALLOWED_*`) or the console's group roles and scope; and that a dump
of `pg_dump` 17+ cannot be restored on PostgreSQL 16 (`SET transaction_timeout`). Left over: the optional
`hse proxy-snippet`, a run of the moved 1.x Authentik stack, the `shellcheck` run (CI), and the legacy installer's
own `make` targets.

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 4.  
**Estimated effort:** M (4-5 days, ~32-38h)  
**Goal:** Everything a company or a careful self-hoster adds to the all-in-one
(AIO) image — an identity provider, an external PostgreSQL, a proxy in front,
remote backups — is a documented, tested, copy-and-paste option, and the
installer is small enough to read. The AIO image stays the only thing we build.

**Done when:**
- `deploy/compose/docker-compose.yml` starts the AIO image from a ten-line `.env`,
  and `--profile backup-remote` adds the sidecar that uploads each backup;
- an AIO behind nginx, Traefik, Caddy or Nginx Proxy Manager works with the HTTPS
  the browser sees (cookies, redirects, device sign-in, real client IPs);
- an existing 1.x **Authentik** keeps working with the **same issuer URL**, so no
  Headscale user loses their link, and Pocket ID, Keycloak and Google have a
  working example each;
- external PostgreSQL works end to end in CI: Headscale, the console through a
  **read-only** role, and backups;
- `./install.sh` installs Docker if missing, asks at most four questions (or none),
  writes a small `.env` and runs `docker compose up -d`, and says it ends in the
  setup wizard;
- idle RAM < 100 MB and image < 250 MB still hold (the PostgreSQL client counts).

---

## Decisions

- **Branch/PR flow:** one branch per block, PR against `next` (as in phases 2, 2.5
  and 3).
- **Copy, do not move, while 1.x lives.** The root `docker-compose.yml` still
  mounts `./authentik/` and the 1.x installer still reads `templates/front-*`.
  SIMPLIFICATION_PLAN says "moved"; until phase 6 deletes the 1.x pieces they are
  **copied** into `deploy/`. Phase 6 removes the originals.
- **The 1.x installer is kept, not rewritten in place.** `install.sh` moves to
  `legacy/install-1x.sh` (same content) and a new, small `install.sh` takes its
  name. Two things depend on the old one: `scripts/gen_render_goldens.sh` sources
  its generator functions by line markers, and phase 5's migration reads what it
  wrote. The 1.x compose and its `make` targets keep working through the legacy
  script until 2.0.
- **A new compose file in `deploy/compose/`, the root one untouched.** Two files
  with the same name in different places is the price of not breaking 1.x; the
  README quick start points only at the new one.
- **The installer is self-contained.** It writes the compose file and `.env`
  itself (a heredoc), so it works from a clone and as `curl … | bash` with no
  repository around it. The compose file in `deploy/compose/` is the reference and
  CI checks that the installer's copy matches it.
- **The zero-change Authentik path keeps the issuer URL.** Headscale identifies an
  OIDC user by issuer + subject. Moving Authentik to another URL orphans every
  user. The 1.x stack serves it at `https://<domain>/authentik/`; the AIO has no
  such route today (its Caddyfile says "Authentik is not part of the all-in-one
  image"). Block 1 adds an optional route to an external Authentik at the same
  path.
- **Examples are tested by what can be tested.** `docker compose config` for every
  compose file, the rendered config for every env block, a real PostgreSQL in CI,
  and a real Authentik by hand (too heavy for CI). Each example says which of
  those it had.
- **No new dependency in `web/` or `aio/`**, apart from the PostgreSQL client in
  Block 5, only if it fits the image budget.

## Findings that shape the design

- **No "proxy in front" mode in the AIO.** `aio/render.py` derives the SSL mode
  from `HSE_TLS` (`auto`, `internal`, `off` → `letsencrypt`, `selfsigned`,
  `none`). The 1.x compose target has a fourth mode, `front`, that forces
  `X-Forwarded-Proto: https`, drops HSTS (the proxy sends it) and lets the config
  trust the proxy. With `HSE_TLS=off` behind a proxy the AIO behaves as plain HTTP:
  expect wrong redirects or non-secure cookies. **Verify first** (Block 1.1), then
  fix.
- **Real client IPs.** The AIO trusts only `127.0.0.1/32` (Headscale's
  `trusted_proxies`). Behind a proxy every client appears as the proxy's address
  in logs and rate limits. A setting for the proxy's CIDR is needed, and it must
  not be on by default.
- **No Authentik route in the AIO** (see Decisions). The compose target has the
  code (`/authentik/*` → `authentik-server:9000`, `redir /add-user …`, the
  `X-Forwarded-Proto` rule): it has to become available to the `aio` target
  behind a setting.
- **External PostgreSQL gives the console the owner's credentials.**
  `console_env()` passes `HEADSCALE_PG_USER` / `HEADSCALE_PG_PASS` (Headscale's
  owner role) to the console; the 1.x design used a separate read-only role
  (`headscale_ro`, `templates/headscale-pg-readonly.sql`) that can only `SELECT`
  three columns of `nodes`. In the AIO nobody creates that role.
- **The AIO image has no PostgreSQL client**, so `hse backup` with external
  PostgreSQL fails with a clear message (left over from phase 3). `backup/` has a
  multi-version client wrapper (`pg-client.sh`); the AIO needs enough to dump the
  versions people run.
- **The sidecar image is not published.** `docker.yml` publishes `headscale-easy`,
  `headscale-easy-helper` and `headscale-easy-aio`. The 1.x compose builds
  `./backup` locally. A `backup-remote` profile that uses `image:` needs
  `ghcr.io/…/headscale-easy-backup` published.
- **Backups directory ownership.** The AIO runs as uid 1000. A bind mount for
  `/data/backups` that Docker creates is root-owned: the first backup would fail.
  The installer (and the compose docs) must create it with the right owner.
- **`gen_render_goldens.sh` and `shellcheck` both name `install.sh`**
  (`.github/workflows/ci.yml`, `scripts/validate.sh`, `Makefile`); all three move
  with the legacy script.

---

## Pre-flight checks

Before starting:
- [x] On `next`, CI passing, phase 3 merged (PR #68)
- [x] New branch per block, PR against `next` — *one branch per block for blocks 3 and 4, one integration branch and one PR*
- [x] Measure the baseline: image size and RSS after 60 s idle (219 MB / 71 MB at the end of phase 3)
- [x] A scratch host with Docker and two throwaway domains (or `/etc/hosts` entries) for the proxy and Authentik checks — *a local Docker network and `--resolve` instead of throwaway domains*
- [x] Decide the open questions at the bottom, or accept the defaults

---

## Block 1: AIO renderer — proxy in front, external Authentik, read-only PostgreSQL (M, ~6-8h)

### 1.1 Verify what happens behind a proxy today
- [x] Run the AIO (`HSE_PUBLIC_URL=https://vpn.example.test`, `HSE_TLS=off`) behind an
      nginx container that terminates TLS with a throwaway certificate. Record, with
      `curl -k`: sign-in redirects, the `Secure` flag of the session cookie,
      `/admin/register/<id>` after a device sign-in, and the client IP in the
      console's audit log and in Headscale's logs.
- [x] Write the failures down in the PR; they decide 1.2.

### 1.2 `front` mode
**Files:** `aio/render.py`, `templates/Caddyfile.tmpl` (only if unavoidable; the
compose target must stay byte-identical)

- [x] `HSE_TLS=off` with an `https://` public URL is the proxy-in-front case: derive
      `SSL_MODE=front` (not `none`) for the `aio` target. A plain `http://` URL
      keeps meaning "no HTTPS".
- [x] In `front` mode Caddy forces `X-Forwarded-Proto https` upstream, sends no HSTS
      (the proxy does) and keeps listening on `:80` for any host name.
- [x] `HSE_TRUSTED_PROXIES` (comma-separated CIDRs, empty by default, validated):
      added to Headscale's `trusted_proxies` and to Caddy's, so real client IPs reach
      the logs. A wrong value must fail at start with a message, not silently trust
      everything (`0.0.0.0/0` and `::/0` are refused unless `HSE_TRUSTED_PROXIES_ANY=1`).
- [x] The DERP/STUN note: UDP 3478 does not go through an HTTP proxy. The log line
      at start (and the docs) say to publish it straight to the container.

### 1.3 Optional route to an external Authentik
- [x] `HSE_AUTHENTIK_UPSTREAM` (`host:port`, validated, empty by default): the `aio`
      target gets the same `/authentik` block as the compose target (`redir /authentik
      /authentik/ 308`, `redir /add-user …`, `handle /authentik/*` with
      `X-Forwarded-Proto` from the mode) and `AUTH_PROVIDER=authentik` semantics
      (`email_verified: false`, as `render.py` already does for the compose target).
- [x] Reuse the compose target's code; do not copy it. Golden files stay identical.
- [x] Which `AUTHENTIK_*` variables does the console need to keep invitations and
      two-factor going through Authentik (`web/accounts.py`, `web/mfa.py`)? Check
      and document; if the AIO console cannot, the docs say those screens use local
      accounts and Authentik handles sign-in only.

### 1.4 Read-only PostgreSQL role for the console
- [x] `HEADSCALE_PG_RO_USER` / `HEADSCALE_PG_RO_PASS` (optional). When set,
      `console_env()` passes **those** to the console (as `HEADSCALE_PG_USER` /
      `HEADSCALE_PG_PASSWORD`); the owner credentials then stay with Headscale and
      the backup job only.
- [x] Without them the current behaviour stays (owner), with a log warning that says
      how to create the role. No silent change for anyone already running.

### 1.5 Tests
**Files:** `tests/test_render.py`, `tests/fixtures/render/<case>/`

- [x] `front` mode golden (Caddyfile and config) for the `aio` target; `none` — *assertions on the rendered Caddyfile and config rather than golden files*
      unchanged; the 1.x targets byte-identical.
- [x] `HSE_TRUSTED_PROXIES`: valid lists, whitespace, IPv6, refused `/0`, refused
      garbage, empty = today's output.
- [x] `HSE_AUTHENTIK_UPSTREAM`: the route appears only when set, validated, no
      newline or quote injection (parity with `validate_env_text`).
- [x] `HEADSCALE_PG_RO_*` reach the console and not the other processes.

**Verify:** `python3 -m unittest tests.test_render` and the manual checks of 1.1, repeated.

---

## Block 2: `deploy/compose/` (S/M, ~4-6h)

**Files:** `deploy/compose/docker-compose.yml`, `deploy/compose/.env.example`,
`deploy/compose/README.md`, `.github/workflows/docker.yml`

- [x] One service, `headscale-easy`: image `ghcr.io/insanerask77/headscale-easy-aio:${HSE_VERSION:-latest}`
      (the image-name question is closed in phase 2: it becomes `headscale-easy`
      in 2.0), ports `80`, `443` (tcp and udp) and `3478/udp`, `restart: unless-stopped`,
      the image's own healthcheck, `env_file: .env`, a named volume `hse-data:/data`
      and a bind mount `${HSE_BACKUPS:-./backups}:/data/backups`.
- [x] Hardening that the image allows (verify each, keep what works): `cap_drop: [ALL]`, — *`cap_drop: [ALL]`, `no-new-privileges`, log rotation; `read_only` was not tried and is left out*
      `security_opt: [no-new-privileges:true]`, `read_only` only if the image runs
      with it (it writes `/run/hse` and `/tmp`: tmpfs), a log rotation.
- [x] Profile `backup-remote`: the sync sidecar (`BACKUP_MODE=sync`) from
      `ghcr.io/insanerask77/headscale-easy-backup:${HSE_VERSION:-latest}`, mounting
      `${HSE_BACKUPS}` **read-only** and `${HSE_REMOTE_CONFIG:-./remote-config}` read-only,
      with the `BACKUP_REMOTE*` variables from `.env`. Nothing else is shared with it
      (not the whole `/data`).
- [x] `.env.example`: only what an install needs (`HSE_PUBLIC_URL`, `HSE_TLS`,
      `ACME_EMAIL`, `HSE_ADMIN_EMAIL`, `TZ`) and the rest commented with the AIO's
      real names (`docs/all-in-one.md` is the reference, linked, not copied).
- [x] `docker.yml` publishes `headscale-easy-backup` (context `backup/`) beside the
      other images, with the same tags.
- [x] Backups directory: documented `mkdir -p backups && chown 1000:1000 backups`, — *a named volume by default (the image now creates `/data/backups` for uid 1000); a bind mount needs the chown, and a root-owned one only warns*
      and the compose file does not start the stack with a root-owned one without
      saying so (an init step is not worth a service; the installer does it).
- [x] The compose project name, container name and volume names do not clash with
      the root 1.x compose (`headscale-easy`, `headscale-data`…): someone testing the
      new stack on a host that has 1.x must not touch its volumes.

### Tests
- [x] `docker compose -f deploy/compose/docker-compose.yml config` passes with and
      without the profile (in `scripts/validate.sh`, so CI runs it).
- [x] A smoke run: up with the example `.env`, healthy, `/admin/healthz`, down;
      with `--profile backup-remote` and a local-directory remote: one backup is
      uploaded once (reuses the fake-remote idea of `test_backup_remote.py`, here
      with the real image).

**Verify:** `docker compose -f deploy/compose/docker-compose.yml --profile backup-remote config`,
then the smoke run above on a clean host.

---

## Block 3: the new `install.sh` (M, ~4-6h)

**Files:** `install.sh` (new), `legacy/install-1x.sh` (the old one, `git mv`),
`uninstall.sh`, `Makefile`, `scripts/gen_render_goldens.sh`, `scripts/validate.sh`,
`.github/workflows/ci.yml`

### 3.1 Move the old installer
- [x] `git mv install.sh legacy/install-1x.sh` (history kept); fix its relative paths
      (`SCRIPT_DIR`, `templates/`), a one-paragraph header that says it is the 1.x
      installer, kept until 2.0.
- [x] `scripts/gen_render_goldens.sh`, `shellcheck`, `validate.sh` and the Makefile's
      1.x targets point at the new path. `make install` keeps running the 1.x one
      under an explicit name (`make install-1x`); `make install` becomes the new one.
- [x] `tests/` that read `install.sh` (the render goldens, `test_render.py`) still pass.

### 3.2 The new script (target: ≤ 200 lines, readable top to bottom)
- [x] Checks: running as root or able to `sudo`, Docker and the Compose plugin (offers
      to install Docker with the official convenience script, as the old one does),
      ports 80/443/3478 free (warn, do not stop).
- [x] Questions, each with a default and a flag/env var so it can run unattended
      (`--yes`, `HSE_PUBLIC_URL=… ./install.sh --yes`): 1) public URL or domain,
      2) HTTPS: `auto` (Let's Encrypt, asks for an email), `internal`, or `off` (a
      proxy in front; prints a pointer to `deploy/examples/front-proxy/`), 3) install
      directory (default `./headscale-easy`), 4) optionally an admin email and
      password (otherwise the wizard asks, and the token is in `docker logs`). Nothing
      else: DERP, DNS, OIDC, backups and the rest are the wizard's or the console's.
- [x] Writes the compose file and a `.env` (mode 600) in the directory, creates — *no backups directory to create any more: the compose file uses named volumes*
      `backups/` owned by uid 1000, runs `docker compose up -d`, waits for healthy and
      prints what to do next: the wizard URL and the token (setup mode) or the sign-in
      URL (headless).
- [x] Re-running it on an existing directory keeps the `.env` and the data and only
      pulls and recreates (an update), never regenerates secrets.
- [x] Refuses, with a clear message, to run on a directory that holds a 1.x install
      (`.env` with `AUTH_PROVIDER=` or `headscale-config.yaml`) and points at phase 5's
      migration; no half-converted installs.
- [x] `uninstall.sh` follows: stops and removes the new stack, keeps the volume unless
      `--purge`; the 1.x behaviour moves with the legacy script.

### 3.3 Tests
**Files:** `tests/test_install.py` (new), run in CI without Docker

- [x] The script run with a fake `docker` in `PATH` (as `test_backup_remote.py` fakes
      `rclone`): the questions, `--yes` with env vars, the written `.env` (mode,
      contents, no secret on a command line), the compose file written equals
      `deploy/compose/docker-compose.yml`, re-run keeps the `.env`, refuses a 1.x
      directory, validates the URL and the email.
- [ ] `shellcheck -S warning` clean. — *NOT DONE: `shellcheck` is not installed here; CI runs it (`bash -n` passes)*

**Verify:** the tests above, then a real run on a clean VM: `curl -fsSL …/install.sh | bash`
equivalent (`bash < install.sh`) ends in a working wizard.

---

## Block 4: `deploy/examples/` (M, ~6-8h)

Each example is a folder with a `README.md` (what it is, prerequisites, the exact
steps, what was tested), the files, and the AIO variables it sets.

### 4.1 `front-proxy/` (copy of `templates/front-*`)
- [x] `nginx.conf`, `traefik.yml`, `caddy`, `nginx-proxy-manager.md`: the existing
      templates with their `${DOMAIN}` / `${BACKEND_HOST}` placeholders turned into
      plain, obvious ones (`vpn.example.com`, `HSE_HOST`), not envsubst templates.
      Each says to set `HSE_TLS=off`, `HSE_TRUSTED_PROXIES=<the proxy's address>`, and to
      publish UDP 3478 straight to the container (Block 1.2).
- [ ] Optional (cut line): `hse proxy-snippet <npm|nginx|traefik|caddy>` prints the — *NOT DONE: optional cut line, left out*
      snippet filled in from the configured public URL, so nobody edits placeholders.
- [x] Tested by hand with nginx and Traefik in Docker (from Block 1.1's setup); — *nginx, Traefik and Caddy were run in front of the image; Nginx Proxy Manager was only read*
      Caddy and NPM by reading the config they produce.

### 4.2 `authentik/` (the zero-change path)
- [x] `docker-compose.yml`: Authentik server, worker and its PostgreSQL, the AIO
      container on the same network, `AUTHENTIK_WEB__PATH=/authentik/` and
      `HSE_AUTHENTIK_UPSTREAM=authentik-server:9000`; the blueprint **copied** from
      `authentik/blueprints/headscale.yaml` (the 1.x stack keeps the original).
- [x] The README has two parts: **new install** (Authentik as the provider) and
      **an existing 1.x Authentik** (point the AIO at the same Authentik: same issuer
      URL, same client id and secret, `OIDC_*` from the old `.env`). The second part
      says why the issuer must not change.
- [x] Review the blueprint for the AIO: the OIDC provider and application, the
      groups (`vpn-admins`, `headscale-users`) and the redirect URIs are what the AIO
      needs; the Google source and the enrolment flows are optional. State which
      parts a 1.x user already has and which are new.
- [ ] Tested by hand against a real Authentik (a 1.x stack migrated, and a fresh one): — *NOT DONE: a fresh Authentik was run (sign-in, redirect URIs); moving a real 1.x stack was not*
      sign-in to the console, `tailscale up` sign-in, group mapping to roles.

### 4.3 `pocket-id/`, `keycloak.md`, `google.md`
- [x] `pocket-id/`: compose with Pocket ID and the AIO, the client created by hand,
      the three `OIDC_*` values. (~30 MB provider, the Phase 0 fallback in D2.)
- [x] `keycloak.md`: realm and client settings (redirect URIs
      `https://<domain>/admin/callback` for the console, `https://<domain>/oidc/callback` for
      Headscale, plus `…/admin/` as the post-logout URI; PKCE),
      the groups claim and `PORTAL_*_GROUPS`.
- [x] `google.md`: the OAuth client, `OIDC_ISSUER=https://accounts.google.com`, the
      allowed domains / emails (`PORTAL_ADMIN_EMAILS`), and that Google gives no groups.
- [x] Each lists the exact redirect URIs from the code (not from memory: the console's is
      `REDIRECT_URI` in `web/app.py`, the blueprint has both) and the scopes.

### 4.4 Tests
- [x] `docker compose config` for every example compose file (in `validate.sh`).
- [x] A test that renders the AIO config for each example's `.env` block (OIDC values
      set) and checks `oidc:` and, for Authentik, the `/authentik` route.
- [x] A link/path check: every file an example README names exists.

**Verify:** the checks above; the manual runs of 4.1 and 4.2 recorded in the PR.

---

## Block 5: external PostgreSQL end to end (M, ~5-7h)

**Files:** `aio/Dockerfile`, `aio/backup.py`, `deploy/examples/postgresql/`,
`.github/workflows/ci.yml`, `scripts/aio-smoke.sh`

- [x] **Spike: the client.** `apk add postgresql17-client` (and 16/15 if they fit):
      size in MB, and which server majors a 17 client can dump (it dumps older and the
      same major; not newer). Measure image size and RSS before and after.
- [x] Decision from the spike: (a) one recent client in the image, and a clear message
      when the server is newer than the client; or (b) none, and the sidecar
      (`BACKUP_MODE=create`) backs PostgreSQL up. Default (a) if the image stays well
      under 250 MB.
- [x] `aio/backup.py` already runs `pg_dump` and fails clearly without it: wire the
      client in, and replace the major-version failure with the sentence the user needs
      ("PostgreSQL is 18, this image can dump up to 17").
- [x] `deploy/examples/postgresql/`: a compose with PostgreSQL and the AIO,
      `templates/headscale-pg-readonly.sql` **copied** (the 1.x installer still uses
      the original), the `HEADSCALE_DB_TYPE=postgres` block, `HEADSCALE_PG_RO_*` from
      Block 1.4, and a short script (`psql -f`) that creates the read-only role.
- [x] **CI against a real PostgreSQL**: a service container, the AIO headless with
      `HEADSCALE_DB_TYPE=postgres`: healthy, a user and a pre-auth key created, the
      console lists machines through the read-only role (not the owner), `hse backup`
      writes `headscale.sql`, a restore (`--with-postgres`) brings back what was deleted.
- [x] The role's grants checked: the console's connection can `SELECT` the three
      columns and nothing else (a test that tries `UPDATE` and another table and fails).
- [x] Migration note (feeds phase 5): SQLite → PostgreSQL has no tool; the README says so.

### Tests
- [x] `tests/test_backup.py`: the version-mismatch sentence, with fake `pg_dump` and
      `psql` printing different `server_version`.
- [x] `scripts/aio-smoke.sh` gains the PostgreSQL round, run only where a service
      container exists (CI), skipped with a message locally.

**Verify:** the CI job; locally `docker run postgres:17-alpine` plus the smoke script.

---

## Block 6: CI, docs and close-out (S/M, ~4-5h)

- [x] `scripts/validate.sh`: the file list gains `deploy/**`, `legacy/install-1x.sh`, the
      new `install.sh`; `docker compose config` for every compose file under `deploy/`.
- [x] `ci.yml`: `shellcheck` over `install.sh legacy/*.sh scripts/*.sh backup/*.sh`; the — *shellcheck now covers `install.sh uninstall.sh legacy/*.sh scripts/*.sh backup/*.sh`; the installer tests are unit tests; the PostgreSQL rounds and the compose smoke run in the `aio` job*
      installer tests; the PostgreSQL job (Block 5); the compose smoke (Block 2).
- [x] README / README.es: the quick start is the new installer (or one `docker run`);
      "Advanced" is a short section linking `deploy/` and the examples.
- [x] `docs/advanced.md` / `.es.md` (new, in `mkdocs.yml`): the compose file and its
      profiles, each example in two lines, the proxy checklist (the headers, the UDP
      port, `HSE_TRUSTED_PROXIES`), the PostgreSQL checklist, remote backups.
- [x] `docs/all-in-one.md` / `.es.md`: the new variables (`HSE_TRUSTED_PROXIES`,
      `HSE_AUTHENTIK_UPSTREAM`, `HEADSCALE_PG_RO_*`) in the table; the "Where the
      backups go" section points at the `backup-remote` profile.
- [x] `docs/getting-started*`: one path (installer → wizard); the 1.x flow moves to a
      "Using 1.x" note that links `legacy/`.
- [x] CHANGELOG entry under [2.0.0] - Unreleased; ROADMAP P4 ticked; SIMPLIFICATION_PLAN
      Phase 4 ticked with a status line like the earlier phases; settle the open
      question on remote backups and close "the compose profile is phase 4" in phase 3's
      notes.

---

## Acceptance criteria (Phase 4 done when):

- [x] All unit tests pass, `check_i18n.py` passes, `shellcheck` and `validate.sh` pass — *`shellcheck` is not installed here: CI runs it*
- [x] `deploy/compose` runs the AIO from the example `.env`; the `backup-remote` profile
      uploads a backup once and survives a restart
- [x] Behind nginx and behind Traefik with `HSE_TLS=off`: sign-in works over HTTPS, — *checked with nginx, Traefik and Caddy: Secure cookie, redirects, real client IP, forged headers ignored; a real Tailscale client was not registered behind a proxy*
      cookies are `Secure`, the device sign-in redirect lands on the right URL, and the
      console's audit log shows real client IPs with `HSE_TRUSTED_PROXIES`
- [ ] A 1.x Authentik is reused with the same issuer URL; users sign in to the console — *NOT DONE: as above: the fresh Authentik path was run, a migrated 1.x stack was not*
      and `tailscale up` without being re-created; Pocket ID, Keycloak and Google
      examples are exact about redirect URIs and scopes
- [x] External PostgreSQL passes the CI job: the console uses the read-only role, and a
      backup and a restore work
- [x] The installer asks ≤ 4 questions, runs unattended with env vars, never
      regenerates secrets, and refuses a 1.x directory with a pointer to phase 5
- [ ] The 1.x installer, the root compose file and `make` targets still work from — *NOT DONE: the render goldens regenerate with no diff; `legacy/install-1x.sh` itself and its `make` targets were not run*
      `legacy/` (nothing 1.x broke)
- [x] Idle RAM < 100 MB and image < 250 MB, enforced in CI, with the PostgreSQL client
- [x] No Docker socket anywhere, containers run as non-root
- [x] CHANGELOG, ROADMAP, SIMPLIFICATION_PLAN and docs updated

---

## Files created/modified (checklist):

### New files:
- [x] `deploy/compose/{docker-compose.yml,.env.example,README.md}`
- [x] `deploy/examples/front-proxy/*`, `deploy/examples/authentik/*`,
      `deploy/examples/pocket-id/*`, `deploy/examples/keycloak.md`,
      `deploy/examples/google.md`, `deploy/examples/postgresql/*`
- [x] `install.sh` (the new one), `legacy/install-1x.sh`
- [x] `tests/test_install.py`, `tests/fixtures/render/front_*` — *assertion tests (`tests/test_render.py`), no new golden fixtures*
- [x] `docs/advanced.md`, `docs/advanced.es.md`

### Modified files:
- [x] `aio/render.py` (front mode, trusted proxies, Authentik route, read-only role),
      `aio/Dockerfile` (PostgreSQL client), `aio/backup.py` (version message), `aio/hse`
      (only if the optional `proxy-snippet` ships)
- [x] `tests/test_render.py`, `tests/test_backup.py`
- [x] `uninstall.sh`, `Makefile`, `scripts/{validate.sh,gen_render_goldens.sh,aio-smoke.sh}`
- [x] `.github/workflows/{ci.yml,docker.yml}`
- [x] `mkdocs.yml`, `docs/{all-in-one,getting-started,operations,configuration}*.md`, — *`all-in-one` and `getting-started` (EN/ES) changed; `operations` and `configuration` did not need to*
      `README.md`, `README.es.md`, `CHANGELOG.md`, `ROADMAP.md`, `SIMPLIFICATION_PLAN.md`

---

## Estimated timeline:

| Block | Effort | Cumulative |
|-------|--------|------------|
| 1. Renderer: front mode, Authentik route, read-only role | 6-8h | 8h |
| 2. `deploy/compose` | 4-6h | 14h |
| 3. New `install.sh` + legacy move | 4-6h | 20h |
| 4. `deploy/examples` | 6-8h | 28h |
| 5. External PostgreSQL end to end | 5-7h | 35h |
| 6. CI, docs, close-out | 4-5h | 40h |
| **Total** | **~29-40h** | **~4-5 days** |

Block 1 comes first: 2, 4 and 5 depend on its settings. Block 2 before 3 (the installer
writes that compose file). Blocks 3, 4 and 5 are independent once 1 and 2 are in and can
run in parallel. If time is short the cut is the optional `hse proxy-snippet` and the Pocket
ID example, never the Authentik zero-change path or the PostgreSQL CI job.

---

## Verification (end to end)

1. `python -m unittest discover -s tests -v`, `python scripts/check_i18n.py`,
   `./scripts/validate.sh`, `shellcheck -S warning install.sh legacy/*.sh scripts/*.sh backup/*.sh`.
2. Clean VM: `bash install.sh --yes` with `HSE_PUBLIC_URL=http://<ip>`, `HSE_TLS=off`
   → wizard URL and token printed → complete the wizard → working tailnet.
3. Same VM, `.env` with an admin: headless start, sign in, create a device key.
4. nginx in front: `HSE_PUBLIC_URL=https://…`, `HSE_TLS=off`, `HSE_TRUSTED_PROXIES` →
   sign in over HTTPS, cookie `Secure`, register a device with a Tailscale client.
5. Authentik: a migrated 1.x stack and a fresh one; sign in to the console, `tailscale up`.
6. PostgreSQL: the CI job, and by hand with a `postgres:16` and a `postgres:17`.
7. `docker compose --profile backup-remote up -d` with a local-directory remote: one
   backup uploaded; restart; not uploaded twice.
8. The 1.x path: from `legacy/`, `./install-1x.sh` on a scratch directory still renders
   the same files (the goldens say so).
9. `docker stats` idle < 100 MB; `docker image inspect` < 250 MB.

---

## Notes for execution:

1. **Block 1 first and verify before fixing:** 1.1 is an experiment. What it shows
   decides what 1.2 changes.
2. **One commit per subtask, tests after each.**
3. **Never break 1.x:** the root compose, `legacy/install-1x.sh`, `templates/`, `authentik/`
   and the render goldens stay as they are (phase 6 removes them).
4. **Do not change an issuer URL, ever, in an example:** say it in the Authentik README.
5. **Examples that nobody ran are fiction:** each README states what was run and where.
6. **Test fixtures:** low-entropy fake keys, passwords and IDs only (GitGuardian).
7. **Phase 5 builds on this:** `migrate-to-2.sh` writes the same `.env` and compose file the
   installer does, and its "keep Authentik" path is the Block 4.2 example.

---

## Open questions

- [x] **Installer: self-contained or needs the repository?** This plan assumes it writes the
      compose file itself (works with `curl | bash`). If you would rather keep one source of
      truth, it can download the file from the release, at the cost of needing network and a
      pinned URL.
      **Resolved:** Self-contained: it writes the compose file itself, and a test checks that it equals `deploy/compose/docker-compose.yml`.
- [x] **Where does the 1.x installer live on `next`?** `legacy/install-1x.sh` until phase 6
      deletes it; the alternative is to drop it from `next` now and keep it only on `main`,
      at the price of regenerating the render goldens differently.
      **Resolved:** `legacy/install-1x.sh` (and `legacy/uninstall-1x.sh`); the render goldens regenerate from there with no diff.
- [x] **PostgreSQL client in the image or in the sidecar?** Decided by the Block 5 spike (size
      and supported majors); default is one recent client in the image.
      **Resolved:** In the image: a PostgreSQL 18 client, +13 MB (image 232 MB), dumping servers 13 to 18.
- [x] **`HSE_AUTHENTIK_UPSTREAM` as a generic "external OIDC at a path"?** Naming it after
      Authentik is honest (it is the one case that needs the path), but Keycloak under a path
      has the same need. Default: Authentik-specific now, generalise if asked.
      **Resolved:** Authentik-specific for now (`HSE_AUTHENTIK_UPSTREAM`); generalise if someone needs another provider under a path.
- [x] **Does the console talk to Authentik's API in the AIO** (invitations, resets, 2FA state)?
      Block 1.3 finds out; if not, the docs say Authentik handles sign-in only.
      **Resolved:** Yes: the console detects Authentik from the issuer URL and needs `AUTHENTIK_URL` and `AUTHENTIK_API_TOKEN`; the image did not pass them and now does.
- [x] **Pocket ID: tested by hand only or in CI?** By hand: it is a ~30 MB container but its
      client has to be created through its UI.
      **Resolved:** By hand (a real Pocket ID run is in the example README); not in CI.
