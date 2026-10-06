# Phase 5 Execution Plan — Migration from 1.x and Deprecation

**Status:** 📋 Planned (not started).

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 5.
**Estimated effort:** M (4-5 days, ~30-36h)
**Goal:** Anyone running the 1.x split stack (Headscale + Caddy + console + hs-helper,
optionally Authentik and a bundled PostgreSQL) moves to the AIO image with one
command, keeping every Headscale user, node, ACL, DNS record and key. Nothing is
lost, and going back is documented and tested. The last 1.x release tells people
2.0 is coming.

**Done when:**
- `scripts/migrate-to-2.sh` takes a real 1.x install (SQLite, Authentik, bundled
  PostgreSQL — one fixture each) to a running AIO container, with the same users,
  nodes, policy, DNS block, `extra_records`, DERP map and key expiry;
- an Authentik install keeps working **with the same issuer URL** (path 1), and
  `tailscale up` works for existing and new devices without re-creating users;
- Authentik users can instead be moved to **local accounts** (path 2): accounts and
  roles created from Authentik, reset links listed for the admin, nodes untouched;
- the bundled PostgreSQL is reused as an *external* PostgreSQL, with no automatic
  PostgreSQL → SQLite conversion and a clear doc saying so;
- a failed or aborted migration leaves the 1.x stack as it was, and `--rollback`
  brings it back from the backup;
- the 1.x release announces the deprecation (README, CHANGELOG, console banner);
- phase 4's open item — *a real 1.x Authentik stack moved to the new image* — is
  closed by the test above.

---

## Decisions

- **Branch/PR flow:** one branch per block, PR against `next`, as in phases 2–4.
- **Re-render, do not copy config.** The migration never copies 1.x's
  `headscale-config.yaml`, `Caddyfile` or `docker-compose*.yml` into `/data/config/`.
  It reads the facts out of them (see Block 1) and lets `aio/render.py` write new
  ones (D6). The only parts carried over verbatim are the ones `render.py` already
  preserves when it has an `existing` config: the DNS block, `extra_records` and the
  key-expiry block.
- **The migrator is a Python module inside the image; the shell script is a thin
  driver.** `aio/migrate.py` (stdlib) does the parsing, the mapping and the checks and
  has unit tests; `scripts/migrate-to-2.sh` only needs Docker: it stops the stack,
  runs `docker run --rm … --entrypoint hse <image> migrate …` against the old
  volumes and starts the new container. The logic that can corrupt data is tested
  in Python, not in bash.
- **Never modify the 1.x volumes.** The old volumes are mounted **read-only** as
  sources; data is copied to a **new** `hse` volume. Rollback is "start the old
  stack"; deletion of old volumes is a manual, explicit step the script prints but
  never runs.
- **Backup first, and refuse without one.** The script runs 1.x's `backup/backup.sh`
  (the tool that exists today) and checks the archive before touching anything. It
  aborts if the backup fails. `--skip-backup` exists but requires `--yes-i-have-a-backup`.
- **Authentik: keep the issuer URL.** Headscale identifies an OIDC user by issuer +
  subject (see phase 4). Path 1 therefore requires the AIO to serve Authentik at the
  same `https://<domain>/authentik/…`. Phase 4's `HSE_AUTHENTIK_UPSTREAM` route does
  this; the migration only has to point it at the **existing** Authentik container
  and join the AIO to that network.
- **Path 2 changes who signs in, not who the user is.** A local account is linked to
  the existing Headscale user by name (`headscale_user`). The Headscale user keeps
  its `providerId`/issuer; we do not edit Headscale's database. After key expiry the
  node re-authenticates through the phase 0 flow (`/admin/register/<id>`) against
  that same user.
- **Bundled PostgreSQL = external PostgreSQL.** The `headscale-postgresql` container
  keeps running (as a one-service compose in `deploy/examples/postgresql/`) and the AIO
  gets `HEADSCALE_DB_TYPE=postgres` + `HEADSCALE_PG_*`. No conversion tool exists in
  Headscale, so none is built.
- **Idempotent and resumable.** Each step records itself in `/data/config/migration.json`
  (`started`, `copied`, `rendered`, `accounts`, `done`). Running the script again
  skips finished steps; `--rollback` and `--dry-run` use the same state.
- **No new dependencies** in `web/` or `aio/`. The Authentik client is the one in
  `web/accounts.py`.

## Findings that shape the design

- **1.x state lives in four places:** Docker volumes (`headscale-data`, `caddy-data`,
  `caddy-config`, optionally `headscale-db`, `authentik-*`), `./data/web` (the
  console's `apikey`, `audit.db`, MFA mode, and phase 1's `accounts.db` if the user
  already used local accounts on 1.x), `./data/caddy-logs`, and the generated files in
  the project directory (`.env`, `headscale-config.yaml`, `headscale-derp.yaml`,
  `Caddyfile`). The migrator must read all four and tolerate missing ones.
- **`.env` names differ from the AIO's** (`SERVER_URL`/`DOMAIN`/`SSL_MODE`/
  `HEADSCALE_DERP_PORT`/`OIDC_ISSUER_URL`/`PORTAL_*` vs `HSE_PUBLIC_URL`/`HSE_TLS`/
  `HSE_DERP_PORT`/`OIDC_ISSUER`/`HSE_OIDC_ALLOWED_*`). The mapping is the main
  piece of Block 1 and must be a table in code, covered by a test per row.
- **`SSL_MODE` has four 1.x values** (`letsencrypt`, `selfsigned`, `none`, `front`) vs
  three AIO values (`auto`, `internal`, `off`). `front` maps to `off` **plus**
  `HSE_TRUSTED_PROXIES` from `FRONT_PROXY_IP`; losing that would make every client
  look like the proxy (phase 4 finding).
- **Ports and ownership.** The 1.x Caddy and the AIO both want 80/443/3478. The old
  stack must be **stopped, not removed** before the AIO starts. The AIO runs as
  uid 1000 (phase 2): copied data must be `chown`ed, as phase 4 learned with
  `/data/backups`.
- **The API key is per-install.** The console's 1.x key (`./data/web/apikey`) is
  valid against the old Headscale database, which is the same database we copy, so it
  can be reused; but `web/apikey.py` renews it anyway. If it is unreadable the AIO
  creates a new one (`headscale apikeys create`); that must not fail the migration.
- **`hse restore` refuses 1.x archives** and `scripts/restore.sh` refuses AIO ones
  (phase 3). The 1.x backup taken in Block 2 is therefore only the *safety net*; the
  data path is the direct volume copy, not a restore.
- **Authentik access for path 2** needs the same token the console already uses
  (`PORTAL_AUTHENTIK_TOKEN`), read from the 1.x `.env`. Authentik superusers and the
  `authentik Admins` group are *protected* in `web/accounts.py` and must keep their
  role mapping.

---

## Pre-flight checks

Before starting:
- [ ] On `next`, CI passing, phase 4 merged (PR #70)
- [ ] New branch per block, PR against `next`
- [ ] Three **1.x fixtures** on a scratch host, installed with `legacy/install-1x.sh`
      and given real content (3 users, ≥ 5 nodes, an ACL, an `extra_records` entry, a
      custom DERP map, a non-default key expiry): **(a)** SQLite + Caddy Let's
      Encrypt-less (`SSL_MODE=selfsigned`), **(b)** Authentik + 3 Authentik users
      (one in `vpn-admins`), **(c)** bundled PostgreSQL + `SSL_MODE=front`
- [ ] Snapshot each fixture's volumes (`docker run … tar`) so each test run starts from
      the same bytes
- [ ] Record the fixtures' `headscale users list`, `nodes list` and `policy get` output
      as goldens — the migration must reproduce them
- [x] Open questions decided (see "Decisions on the former open questions")

---

## Block 1: `aio/migrate.py` — read 1.x, produce AIO settings (M, ~6-8h)

### 1.1 Discover a 1.x install
- [ ] `find_install(path)`: given the project directory, return
      `{env, headscale_config, derp, caddyfile, compose, data_web}` or raise with the
      list of what is missing. Accept `--project-dir` and default to the current one.
- [ ] Parse `.env` with the same rules the 1.x installer wrote (no shell evaluation:
      `KEY=value`, optional quotes, comments). Never `source` it.
- [ ] Detect the edition: Authentik (`AUTH_PROVIDER=authentik` or the `authentik-server`
      container/volume exists), bundled PostgreSQL (`HEADSCALE_DB_TYPE=postgres` and
      the `postgres` profile in `COMPOSE_PROFILES`), external PostgreSQL
      (`HEADSCALE_DB_TYPE=postgres` without the profile), front proxy
      (`SSL_MODE=front`).
- [ ] Refuse, with a clear message: no `.env`; a running AIO container already using the
      target volume; Headscale older than the pinned version when the schema would need
      a downgrade (read `headscale version` from the old image; the AIO image can only
      upgrade the database).

### 1.2 The mapping table (`ENV_MAP`)
- [ ] One table, one row per 1.x variable → AIO variable or `settings.json` key,
      with a transform and a note. At least:

      | 1.x | AIO |
      |---|---|
      | `SERVER_URL` / `HEADSCALE_PUBLIC_URL` / `DOMAIN` + `URL_SCHEME` | `HSE_PUBLIC_URL` |
      | `SSL_MODE` `letsencrypt`/`selfsigned`/`none`/`front` | `HSE_TLS` `auto`/`internal`/`off`/`off`+proxy |
      | `ACME_EMAIL` | `ACME_EMAIL` |
      | `HEADSCALE_DERP_PORT` | `HSE_DERP_PORT` |
      | `DERP_USE_PUBLIC`, custom DERP map | `HSE_DERP_MODE` (`public`/`custom`/`embedded`) + `HSE_DERP_URL` |
      | `TAILNET_NAME`, `NETWORK_ISOLATION` | tailnet name, isolation, `HSE_BASE_DOMAIN` (kept from the existing DNS block) |
      | `FRONT_PROXY_IP` | `HSE_TRUSTED_PROXIES` |
      | `ENABLE_OIDC`, `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID/SECRET`, `OIDC_SCOPE`, `OIDC_EMAIL_CLAIM` | `OIDC_ISSUER`, `OIDC_CLIENT_ID/SECRET`, scope, claim |
      | `PORTAL_ADMIN_GROUPS`, `PORTAL_ADMIN_EMAILS` | same names (console knobs are reused) |
      | `PORTAL_OIDC_*`, `PORTAL_SESSION_SECRET` | console OIDC client; keep the secret so sessions survive |
      | `MFA_REQUIRED`, `PORTAL_API_KEY_LOGIN`, `UI_LANG`, `TZ`, `LOG_LEVEL` | same names |
      | `SMTP_*` | same names |
      | `BACKUP_ENABLED`, `BACKUP_SCHEDULE`, `BACKUP_KEEP_DAYS`, `BACKUP_DIR` | same names; `BACKUP_DIR` → a bind mount of `/data/backups` |
      | `HEADSCALE_DB_TYPE` + `HEADSCALE_PG_*` | `HEADSCALE_DB_TYPE` + `HEADSCALE_PG_*` |
      | `AUTHENTIK_*`, `PORTAL_AUTHENTIK_TOKEN` | **not mapped** to the AIO; used only by path 1/2 (see Block 3) |
      | `HTTP(S)_PORT`, `HEADSCALE_HTTP/GRPC/METRICS_PORT`, `DOCKER_GID`, `*_IMAGE_TAG`, `HSE_VERSION`, `PORTAL_UID/GID`, `BACKEND_HOST` | dropped, listed in the report as "no longer needed" |
- [ ] Anything not in the table is **kept in the report as "unmapped"**, never dropped
      silently; the script ends with a short list the admin can read.
- [ ] Output: a dict that `aio/render.load_settings()` accepts, written to
      `/data/config/settings.json` (mode 600) when the value is a setting and to a
      generated `migrated.env` (mode 600) for secrets that are env-only (OIDC
      client secret, SMTP password, PostgreSQL password).

### 1.3 Carry over what only exists in the old config
- [ ] Read `headscale-config.yaml` and pass it to `render.render_headscale_config(...,
      existing=...)` so the DNS block, `extra_records` and key-expiry block survive
      exactly (they are the parts `render.py` already preserves between renders).
- [ ] Read `headscale-derp.yaml`: if it differs from the stock map, keep it as
      `/data/config/derp.yaml` and set `derp_mode=custom`.
- [ ] Compare the **rendered** result against the old file for the fields Headscale
      cares about (`server_url`, `listen_addr`, `prefixes`, `dns.base_domain`,
      `dns.nameservers`, `derp.*`, `database.*`, `policy.mode`, `oidc.issuer`) and print
      any difference. Differences are allowed (the AIO listens on different addresses)
      but must be *explained* in the report, not found out later.
- [ ] `ip_prefixes` (`IP_PREFIXES_V4/V6`) **must** be kept identical: changing them
      renumbers every node. Fail the migration if they would differ.

### 1.4 Tests
- [ ] `tests/test_migrate.py`: one test per `ENV_MAP` row; the three fixtures' `.env` +
      configs live in `tests/fixtures/migrate/{sqlite,authentik,pgfront}/`;
      golden comparison of the rendered `config.yaml`, `Caddyfile` and `settings.json`
      (extend `scripts/gen_render_goldens.sh` pattern, no second generator).
- [ ] Unknown variable → in the report; quoted values; Windows line endings; empty
      file; `.env` with secrets never printed in full (assert the report masks them).
- [ ] `ip_prefixes` mismatch → migration refuses.

---

## Block 2: `scripts/migrate-to-2.sh` — stop, back up, copy, start (M, ~6-8h)

### 2.1 Driver script (target: ≤ 250 lines, readable top to bottom, `shellcheck` clean)
- [ ] Flags: `--project-dir`, `--image` (default the pinned `headscale-easy-aio`),
      `--volume` (default `hse`), `--dry-run`, `--rollback`, `--yes`,
      `--auth keep|local` (Block 3), `--skip-backup --yes-i-have-a-backup`.
- [ ] Pre-checks: Docker ≥ 20.10, project directory has `.env`, enough free disk
      (size of the volumes × 2), the target volume is empty or absent, ports 80/443/3478
      are held only by the old stack.
- [ ] `--dry-run`: runs the discovery, mapping and rendering inside a throwaway
      container, prints the report and the exact commands it would run, changes nothing.

### 2.2 Steps (each recorded in `migration.json`)
1. [ ] **Back up with the 1.x tool:** `backup/backup.sh` (or `make backup`); verify the
       archive lists `headscale/` and the console data; print its path and checksum.
2. [ ] **Stop, do not remove:** `docker compose stop` in the project directory (keeps
       containers, networks and volumes for rollback). Wait until Headscale's SQLite
       file is closed (no `-wal` growth for 2 s).
3. [ ] **Copy the volumes** into the new `hse` volume with a helper container that
       mounts the old ones **read-only**:

       | From | To |
       |---|---|
       | `headscale-data:/var/lib/headscale` (`db.sqlite*`, `noise_private.key`, `private.key`, DERP key) | `/data/headscale/` |
       | `./data/web` (`apikey`, `audit.db`, `accounts.db`, `sessions`, MFA mode) | `/data/console/` |
       | `caddy-data` (ACME account, certificates, local CA) | `/data/caddy/` |
       | `./data/backup-remote` (if present) | stays where it is, documented for the sidecar |
       | `./data/caddy-logs` | not copied (logs) |

       - SQLite copied with `sqlite3.Connection.backup()` (as in phase 3), **not** `cp`,
         so a `-wal` file cannot give a torn copy.
       - `chown -R 1000:1000` and `chmod 600` on keys and DBs.
       - PostgreSQL edition: nothing to copy for Headscale (the data stays in
         `headscale-db`); only the console and Caddy data move.
4. [ ] **Render:** `hse migrate render` (Block 1) writes `/data/config/{settings.json,
       config.yaml,Caddyfile,derp.yaml}`.
5. [ ] **Start the AIO:** `docker run -d --name headscale-easy … -v hse:/data -p 80:80 -p 443:443 -p 3478:3478/udp <image>`
       — or, when the project used the bundled PostgreSQL or Authentik, a generated
       `docker-compose.aio.yml` from `deploy/examples/` (Block 3/4) that also joins the
       existing network. Print the exact command and the compose file path.
6. [ ] **Verify** (`hse migrate verify`): container healthy, `/healthz`, `/admin/healthz`;
       `headscale users list` / `nodes list` / `policy get` equal the goldens taken in
       step 1; every node's `last_seen` is older than the downtime (nobody was
       re-created); the console's API key works. Any failure → print the failing
       check, **do not** continue, suggest `--rollback`.
7. [ ] **Report:** what moved, what was dropped, what is unmapped, the old volumes that
       can now be deleted (as commands the admin runs by hand), and the rollback command.

### 2.3 Rollback
- [ ] `--rollback`: stop and remove the AIO container (keep the `hse` volume, renamed
      `hse-failed-<date>`), `docker compose start` in the project directory, run the
      same verification against the old stack. If the old volumes were already
      deleted, restore from the backup taken in step 1 with `scripts/restore.sh`.
- [ ] A migration that fails **before step 5** rolls back automatically; after step 5 it
      asks (or `--yes`).

### 2.4 Tests
- [ ] `tests/test_migrate_script.py` (stubbed `docker`, as `tests/test_install.py` does):
      argument handling, every pre-check refusal, the exact `docker` commands for each
      edition, resume after a failure at each step, `--dry-run` makes no `docker`
      call that changes state, `--rollback` order of operations.
- [ ] Copy helper (`aio/migrate.py::copy_data`): a WAL-mode SQLite with an open writer
      copies consistently; permissions and ownership; refuses a non-empty target.

---

## Block 3: Authentik users — keep it, or move to local accounts (M, ~7-9h)

### 3.1 Path 1 — keep Authentik as external OIDC (zero change)
- [ ] The migration detects Authentik and generates `docker-compose.aio.yml` from
      `deploy/examples/authentik/` (the files phase 4 copied): the AIO container, **the
      existing `authentik-server`/`worker`/`postgresql`** (same volumes, not recreated)
      on the same network, and `HSE_AUTHENTIK_UPSTREAM=http://authentik-server:9000`
      so the AIO serves it at `/authentik/` — the **same issuer URL** as before.
- [ ] Carry the console's OIDC client (`PORTAL_OIDC_*`), `OIDC_*` for Headscale, group
      names (`PORTAL_ADMIN_GROUPS`) and `HSE_OIDC_ALLOWED_*` equivalents. The Authentik
      redirect URIs do not change because the public URL does not.
- [ ] Check at the end: `GET /authentik/-/health/live/` through the AIO, the issuer
      `.well-known/openid-configuration` is identical to the one before (byte-compare
      `issuer`, `jwks_uri`, `authorization_endpoint`), a console OIDC sign-in works.
- [ ] Close phase 4's open item: run it on fixture (b) and tick it there.

### 3.2 Path 2 — move to local accounts (`--auth local`, `hse migrate authentik-to-local`)
- [ ] Read-only pass over Authentik with the console's existing client
      (`web/accounts.py::accounts()`/`_account`): `username`, `email`, `name`, `groups`,
      `is_active`, `is_superuser`.
- [ ] Match each Authentik user to its Headscale user (`web/accounts.py::match`, by
      `providerId`/email). Report **unmatched in both directions**: Authentik users with
      no Headscale user (a local account and Headscale user are still created, as an
      invitation would), Headscale users with no Authentik account (left alone; the
      admin can invite them later).
- [ ] For each matched user: `local_accounts.create_account(username, email,
      password=<random, unusable>, role=…, headscale_user=…)` with `must_change=True`,
      TOTP **not** enrolled (MFA enrolment happens at first sign-in under the
      `MFA_REQUIRED` mode), then `create_reset_token(account_id, expires_hours=72)`.
- [ ] **Role mapping** from Authentik groups, same rules the console uses today:
      `vpn-admins` / `authentik Admins` / superuser → `admin`; the network-admin and
      auditor groups → those roles; everyone else → `member`. Disabled Authentik users →
      `disable_account`. Honour `PORTAL_ADMIN_EMAILS`.
- [ ] **Do not touch Headscale users**: no rename, no delete, no `providerId` edit.
- [ ] **Output for the admin:** `migration-accounts.csv` (mode 600: username, email,
      role, reset link, expiry) and the same list in the console under Users →
      "Migrated accounts", with a "Send by e-mail" button when SMTP is configured
      (reusing `email_reset`). Links are shown **once**; the CSV is the only copy.
- [ ] **First admin:** the person running the migration is made admin first, with a
      reset link printed to the terminal, so a bad role mapping can never lock everyone
      out. `HSE_ADMIN_EMAIL`/the API key (emergency mode) stay available.
- [ ] Sign-in mode becomes `local` (+ `apikey`); Authentik is **not** started by the
      generated compose. Offer to keep it stopped, not deleted.
- [ ] **Device re-auth (phase 0 open item):** verify on fixture (b) that after
      `headscale nodes expire <id>` an OIDC-created node re-authenticates through
      `/admin/register/<id>` and lands on the **same** Headscale user. Record the exact
      behaviour here and tick the "registering against an OIDC-created user" item in
      SIMPLIFICATION_PLAN phase 0.
- [ ] Idempotent: running it twice creates nothing twice (skips existing usernames,
      issues new reset links only with `--reissue`).

### 3.3 Tests
- [ ] `tests/test_migrate_authentik.py` with a fake Authentik API (as `test_accounts.py`
      does): group → role mapping for each group, disabled user, superuser, user with no
      email, duplicated email, user with no Headscale user, Headscale user with no
      Authentik user, 200 users (pagination), second run creates nothing.
- [ ] Security (`test_security.py`): reset links are single use and hashed in the DB;
      the CSV is mode 600; the unusable password cannot be used to sign in; role
      escalation is impossible (a `member` group never yields `admin`).
- [ ] Path 1: the generated compose passes `docker compose config`, contains no
      new volume for Authentik, and the rendered Caddyfile routes `/authentik/` to the
      upstream.

---

## Block 4: Bundled PostgreSQL and the migration of the other 1.x editions (S/M, ~3-4h)

- [ ] Bundled PostgreSQL: generate `docker-compose.aio.yml` from
      `deploy/examples/postgresql/` that keeps `headscale-postgresql` (same volume, same
      network alias) and points the AIO at it: `HEADSCALE_DB_TYPE=postgres`,
      `HEADSCALE_PG_HOST/PORT/NAME/USER/PASS` from `.env`, and the console's read-only
      role created by phase 4's `HEADSCALE_PG_RO_*` / `templates/headscale-pg-readonly.sql`.
      Check that the dump phase 3 takes works (`pg_dump` major version vs server — the
      phase 4 finding on `transaction_timeout`).
- [ ] External PostgreSQL (already external in 1.x): same, minus the container.
- [ ] Docs state plainly: **there is no automatic PostgreSQL → SQLite conversion**
      (Headscale has no tool for it); how to do it by hand is out of scope, and keeping
      PostgreSQL is supported.
- [ ] Front proxy edition: `SSL_MODE=front` → `HSE_TLS=off` + `HSE_TRUSTED_PROXIES`; the
      proxy's upstream changes (`caddy:80` → the AIO's HTTP port), so the report prints
      the one line to edit in the proxy's config and links `deploy/examples/front-proxy/`.
- [ ] Ports: if 1.x published non-default ports (`HTTP_PORT`, `HTTPS_PORT`,
      `HEADSCALE_DERP_PORT`), the generated `docker run`/compose publishes the same ones.
- [ ] 1.x installs that already use **local accounts** (phase 1 on 1.x): `accounts.db`
      comes across with the console data; verify a password and a TOTP secret still work
      after the copy.
- [ ] Tests: `tests/test_migrate.py` rows for each edition; the generated compose files
      pass `docker compose config` (hooked into `scripts/validate.sh`).

---

## Block 5: Deprecation in the last 1.x release (S, ~3-4h)

- [ ] **Version:** the last 1.x release (`1.x.last`) carries the notice, cut from
      `release/1.x`. `HSE_VERSION` in `.env` is read for it.
- [ ] **Console banner** (1.x console only): an admin-only dismissible banner —
      "Headscale Easy 2.0 replaces this stack. Run `scripts/migrate-to-2.sh --dry-run`" —
      linking the migration doc. Dismissal stored per account (not per browser);
      `HSE_DEPRECATION_BANNER=off` hides it for people who cannot migrate yet.
      Translations for es, fr, de, pt; `scripts/check_i18n.py` green.
- [ ] **`install.sh` / `legacy/install-1x.sh`:** prints the deprecation notice at the end
      of an install or update and points to the AIO.
- [ ] **README / README.es:** a "1.x is deprecated" box at the top, with the timeline.
- [ ] **CHANGELOG:** `[1.x.last]` entry (Deprecated) and `[2.0.0] - Unreleased` lists what
      goes away: bundled Authentik, bundled PostgreSQL, `hs-helper`, the split compose.
- [ ] **ROADMAP:** P5 ticked; the timeline written out (announcement release → date
      range for 2.0 → date after which 1.x gets no fixes).
- [ ] **Timeline policy:** 1.x keeps receiving security and bug fixes until 2.0 ships and
      nothing afterwards (no grace period); no new features go into the split compose or
      the Authentik integration (as SIMPLIFICATION_PLAN §6 already says).
- [ ] Tests: banner only for admins and only on 1.x; the env var turns it off; dismissal
      persists per account; the deprecation text is in every locale.

---

## Block 6: CI, docs and close-out (S/M, ~4-5h)

- [ ] **CI job `migrate`:** for each fixture (a, b, c), restore the volume snapshot, run
      the migration against the **built** AIO image, run `hse migrate verify`, then run
      `--rollback` and verify the old stack comes back. Heavy pieces (a full Authentik)
      run on a nightly/`workflow_dispatch` trigger, not on every push, like phase 4's
      Authentik run; SQLite and PostgreSQL fixtures run on every PR that touches
      `aio/`, `scripts/migrate-to-2.sh` or `web/accounts.py`.
- [ ] **Smoke:** extend `scripts/aio-smoke.sh` size/RSS gates; the image must stay under
      250 MB and 100 MB RSS with `migrate.py` in it (it is small and stdlib only).
- [ ] **`scripts/validate.sh`:** include `scripts/migrate-to-2.sh`; `shellcheck` it in CI.
- [ ] **Docs (`docs/migrating.md` / `.es.md`, new, in `mkdocs.yml`):** who should migrate;
      the three editions side by side; a dry-run walkthrough; the report explained; path 1
      vs path 2 for Authentik; what happens to devices; rollback; deleting the old
      volumes by hand; "no PostgreSQL → SQLite conversion"; troubleshooting (ports busy,
      wrong ownership, version mismatch).
- [ ] `docs/getting-started*` / `docs/advanced*`: link the migration guide where 1.x is
      mentioned; `docs/security.md` / `docs/hardening.md`: the migration CSV and
      reset-link handling.
- [ ] **Docs for developers:** `docs/development/` note on the fixtures and how to
      regenerate them.
- [ ] **SIMPLIFICATION_PLAN:** tick phase 5 tasks with a status line like the earlier
      phases; tick the phase 0 OIDC-user item; close the open question about keeping the
      split compose after 2.0 (or record the decision).
- [ ] **PHASE4_EXECUTION_PLAN:** close the "real 1.x Authentik stack moved to the new
      image" item and acceptance criterion with a pointer here.
- [ ] CHANGELOG and ROADMAP entries.

---

## Order of work

```text
Block 1 (migrate.py) ──▶ Block 2 (script) ──▶ Block 3 (Authentik) ──▶ Block 6 (CI/docs)
                      └──▶ Block 4 (PostgreSQL, proxy)  ─────────────┘
Block 5 (deprecation notice) is independent and can ship in the last 1.x release first.
```

Block 5 can land early: announcing the deprecation does not need the migration to be
finished, but its text links the guide, so publish them together.

## Risks

| Risk | Mitigation |
|---|---|
| Data loss during the copy | Backup first and refuse without one; old volumes mounted read-only and never deleted by the script; SQLite copied with the backup API; resumable state file; automatic rollback before step 5 |
| `ip_prefixes` or `server_url` change and every node is renumbered / loses its server | Fail the migration if they would differ; `verify` compares nodes against the goldens |
| Authentik issuer URL changes and users are orphaned | Path 1 serves it at the same `/authentik/` path; check byte-equal `.well-known` before declaring success |
| Path 2 locks the admins out | The runner becomes admin first; emergency API key mode stays on; reset links shown before Authentik is stopped; Authentik stays stopped, not deleted |
| Reset links leak (CSV, terminal, logs) | Mode 600, shown once, hashed in the DB, single use, 72 h expiry, masked in the report and logs |
| Bad role mapping gives someone more access | Mapping table with a test per group; superuser/protected handling copied from `web/accounts.py`; report lists every admin created |
| Port conflict when starting the AIO | Pre-check ports; the old stack is stopped, not removed; the report prints what holds a port |
| File ownership breaks the non-root AIO | Copy helper `chown`s; test for 1000:1000 and 600 modes (phase 4's `/data/backups` lesson) |
| `.env` has a variable we did not foresee | Unmapped list in the report; never dropped silently; `ENV_MAP` extended from real installs |
| PostgreSQL users expect a conversion | Docs say plainly it does not exist; PostgreSQL stays a supported external option |
| Headscale version on 1.x is newer than the AIO's pinned one | Refuse to migrate when it would need a database downgrade; tell the user to wait for the next bump |

## Decisions on the former open questions

Decided following what established projects do (GitLab, Grafana, Traefik, Keycloak).

- [x] **Last 1.x release:** a maintenance branch `release/1.x` cut from `main`, patch
      tags `1.x.y`; the last one carries the deprecation notice. `next` becomes 2.0.
- [x] **Support after 2.0:** **none** (chosen by the maintainer). 1.x gets fixes until 2.0
      ships and none afterwards, so the deprecation announcement must give a clear
      window *before* 2.0 (at least one release with the notice) and the docs must say 1.x
      ends with 2.0. No grace period to track.
- [x] **Authentik default:** keep it (path 1, zero change); `--auth local` is opt-in and
      non-interactive, never a prompt.
- [x] **Reset links by e-mail:** only on the admin's explicit click, with SMTP
      configured; never automatic.
- [x] **Image name:** `headscale-easy-aio` during 1.x (SIMPLIFICATION_PLAN §8); in 2.0 it
      takes over `headscale-easy`, and the old tag stays as an alias for one release.

---

## Acceptance criteria (Phase 5 done when):

- [ ] All unit tests pass, `check_i18n.py` passes, `shellcheck` and `validate.sh` pass
- [ ] Fixture (a) SQLite: migrated; users, nodes, policy, DNS block, `extra_records`,
      DERP map and key expiry equal the goldens; no node re-created; rollback tested
- [ ] Fixture (b) Authentik, path 1: same issuer URL, console and Headscale OIDC sign-in
      work, `tailscale up` works for an existing and a new device
- [ ] Fixture (b) Authentik, path 2: local accounts and roles created, reset links
      issued, every Headscale user and node unchanged, an expired node re-authenticates
      through `/admin/register/<id>` onto the same user
- [ ] Fixture (c) bundled PostgreSQL + front proxy: migrated as external PostgreSQL;
      real client IPs behind the proxy; console reads through the read-only role;
      backup and restore with the dump work
- [ ] A failed migration at each step leaves a working 1.x stack (or a working AIO) and
      `--rollback` is tested for all three fixtures
- [ ] Image < 250 MB and idle RAM < 100 MB still hold
- [ ] The deprecation banner, README, CHANGELOG and ROADMAP announce the timeline;
      `docs/migrating.md` exists in English and Spanish
- [ ] SIMPLIFICATION_PLAN phase 5 and the two carried-over items (phase 0 OIDC user,
      phase 4 real Authentik stack) are ticked with a status line
