# Phase 3 Execution Plan — Built-in Backups

**Status:** ⏳ Not started.

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 3.  
**Estimated effort:** M (3-4 days, ~28-36h)  
**Goal:** The all-in-one image backs itself up on a schedule, with no extra
container, and can restore from any of its archives with one command. The
remote copy stays an advanced-edition sidecar.

**Done when:**
- a fresh AIO container with the default settings writes
  `/data/backups/headscale-easy-<stamp>.tar.gz` every night at 03:00 and prunes
  by `BACKUP_KEEP_DAYS`;
- `docker exec <c> hse backup` and the console's **Back up now** make the same archive;
- `hse restore <file>` on a stopped volume, or on a running container, brings
  back users, machines, keys, console accounts and settings, and devices stay
  registered;
- the Status page shows the last backup (time, size, result) and the next run;
- idle RAM stays < 100 MB and the image < 250 MB (both gates in CI), also
  *during* a backup.

---

## Decisions

- **Branch/PR flow:** one branch per block, PR against `next` (as in phases 2
  and 2.5). Phase 2.5 is merged first: it changes the wizard, `settings.json`
  and the console's status/nodes pages that this phase also touches.
- **Archive layout.** The top-level directories of `backup/backup.sh` are kept
  (`config/`, `headscale/`, `web/`, `caddy/pki/`) and the AIO adds `console/`
  and a `meta.json`. This is a **deliberate narrowing** of the "same layout, so
  `scripts/restore.sh` keeps working" line in SIMPLIFICATION_PLAN: that script
  maps `config/` file names onto the 1.x project directory (`data_web_api-key`
  → `data/web/api-key`) and restores Docker volumes, so it cannot restore an
  AIO archive. Instead:
  - `meta.json` (`{"format": 2, "edition": "aio", ...}`) tells the two apart;
  - `restore.sh` refuses an AIO archive with a pointer to `hse restore`;
  - `hse restore` refuses a 1.x archive with a pointer to Phase 5's migration.
- **Run as a subprocess, not as a supervisor thread.** The supervisor only
  decides *when*; `aio/backup.py` does the work in its own process
  (`python aio/backup.py create`). A slow tar or an OOM never takes the
  supervisor, and with it Headscale, down.
- **One lock** (`/data/backups/.lock`, `flock`) for scheduled, manual and
  restore runs. A second request while one is running is rejected, not queued.
- **What is *not* backed up:** `console/sessions.db` (a restored session would
  revive logins that were revoked after the backup; everyone signs in again),
  `caddy/` certificates other than the internal CA (re-issued automatically),
  logs, `backups/` itself.
- **The newest successful backup is never pruned**, whatever
  `BACKUP_KEEP_DAYS` says (a host that was off for 20 days must not delete its
  only archive).
- **Restore is the risky half.** Both modes ship, but online restore is its own
  block with a cut line (see Block 3): if it grows, ship offline first.
- No new dependencies in `web/` or `aio/`, apart from the PostgreSQL client in
  Block 1.4 (only if the measured cost fits the image budget).

## Findings that shape the design

- `backup/backup.sh` is the reference: it already does the hard parts
  (consistent online `.backup`, `integrity_check`, a `nodes`-table check on the
  `pg_dump`, a flat `config/`, keys, `caddy/pki`, retention with `find -mtime`).
  The Python port must produce the same guarantees, not just the same files.
- AIO data paths come from `aio/render.py:489` (`console_env`):
  `console/{sessions,audit,accounts}.db`, `console/api-key`,
  `console/mfa-required`; the supervisor persists `config/session-secret`
  (`aio/supervisor.py:316`). Headscale's `db.sqlite` and `*.key` live in
  `headscale/`; the wizard writes `config/settings.json`.
- **`config.yaml` must be in the archive, not only `settings.json`.** The
  renderer keeps the marked DNS / key-expiry / DERP blocks of an *existing*
  config, and the console edits DNS in `config.yaml` directly. Re-rendering from
  `settings.json` alone would lose those edits.
- `accounts.db` holds password hashes and TOTP secrets, and `settings.json`
  holds the OIDC secret and session secret: archives are secrets. Directory
  700, files 600, nothing world-readable, and the docs say so.
- The settings already exist: `render.py:75-76` maps `BACKUP_SCHEDULE`
  (`0 3 * * *`) and `BACKUP_KEEP_DAYS` (`14`), and the wizard stores them
  (`aio/wizard.py:141`, with the page saying "arrive in a later release").
  `BACKUP_SCHEDULE=off` must disable the schedule, as in `backup/entrypoint.sh`.
- The helper protocol is a fixed table (`helper/helper.py:46`, no query, no body,
  404/405/400) and `tests/test_docker_helper.py` pins that contract. A new
  operation is a new route plus a backend, not a free-form call.
- `web/status_pages.py:35` renders `status["containers"]` from the supervisor's
  `be_status`. The backup summary is a new key beside it, so the 1.x helper
  (which does not send it) keeps working.
- **`TZ` may do nothing in the AIO image.** `render.py` passes `TZ`, but
  `python:3.13-alpine` ships no `/usr/share/zoneinfo`. The schedule is
  meaningless without it: check in Block 2 and add `tzdata` (~1 MB) if needed.
- `pg_dump` must be at least the server's major version. The sidecar solves that
  with several client versions (`backup/pg-client.sh`), which the AIO cannot
  afford. Phase 4 documents external PostgreSQL; here we need only enough
  client to dump it (Block 1.4).

---

## Pre-flight checks

Before starting:
- [ ] On `next` branch, CI passing, Phase 2.5 merged
- [ ] New branch per block, PR against `next`
- [ ] Reproduce a 1.x backup once (`make backup` on a scratch compose stack) and
      keep the archive as `tests/fixtures/backup/1x-archive.tar.gz` (fake keys,
      tiny DBs) for the "refuse a 1.x archive" test
- [ ] Measure the baseline: image size and RSS after 60 s idle (54 MB / ~64 MB at the end of Phase 2)
- [ ] Decide the open questions at the bottom of this file, or accept the defaults

---

## Block 1: `aio/backup.py` — create and verify (M, ~6-8h)

### 1.1 Create
**Files:** `aio/backup.py`

- [ ] `create(data_dir, out_dir=None, settings=None) -> Result`: stdlib only.
- [ ] Work in a `tempfile.mkdtemp(dir=<backups>)` (same filesystem, so the final
      rename is atomic), mode 700; cleaned in `finally`.
- [ ] Copy, in this layout:
      ```
      headscale-easy-<YYYYmmdd-HHMMSS>/
        meta.json        format, edition, created, headscale_version, db_type, files + sha256
        config/          settings.json config.yaml Caddyfile derp.yaml session-secret
        headscale/       db.sqlite | headscale.sql, *.key
        console/         accounts.db  api-key  mfa-required
        web/             audit.db     (same name as 1.x)
        caddy/pki/       only when there is an internal CA
      ```
- [ ] SQLite files with `sqlite3.connect("file:...?mode=ro", uri=True).backup(dst)`
      (consistent while Headscale / the console write; copying `-wal` by hand is
      not), then `PRAGMA integrity_check` on the *copy*.
- [ ] Missing optional files (no `derp.yaml`, no `caddy/pki`) are skipped;
      a missing `headscale/db.sqlite` or `config/settings.json` is an error.
- [ ] `meta.json` carries a sha256 per file so restore can verify before it
      touches anything.
- [ ] Archive: `tarfile.open(..., "w:gz")` into `.<name>.tar.gz.part` with
      `umask 077`, `fsync`, then `os.replace` to the final name. Owner/group
      numeric (the container uid), no absolute paths.

### 1.2 Verify what was written
- [ ] Re-open the `.tar.gz`, read every member, check names (no `..`, no
      absolute, regular files and directories only) and sha256s against
      `meta.json`. A backup that cannot be read back fails the run and is deleted.
- [ ] Return `Result(ok, path, size, duration, files, error)`.

### 1.3 Retention and status
- [ ] `prune(out_dir, keep_days, now)`: delete `headscale-easy-*.tar.gz` older
      than `keep_days` by **mtime**, never the newest successful one, never a
      `.part` younger than 1 h.
- [ ] `/data/backups/status.json` (600), written atomically after every run
      (scheduled, manual, failed):
      `{"last": {"at","ok","file","size","duration","trigger","error"}, "last_ok": {...}, "count", "bytes"}`.
- [ ] Log lines prefixed `[backup]` through the supervisor sink; never log secrets
      or file contents.

### 1.4 PostgreSQL (external) — only when `HEADSCALE_DB_TYPE=postgres`
- [ ] `pg_dump --no-owner --clean --if-exists` to `headscale/headscale.sql`
      with the connection from `HEADSCALE_PG_*` through `PG*` env vars (the
      password never on the command line); refuse a dump with no
      `CREATE TABLE public.nodes` (as `backup.sh` does).
- [ ] Spike first: `apk add postgresql17-client` (or `postgresql-client`) cost
      in MB. If it fits (target: image stays well under 250 MB), add it to
      `aio/Dockerfile`. If not, fall back to documenting the sidecar for
      PostgreSQL and make `create` fail with a clear message instead of
      silently skipping the database.
- [ ] If the server is a newer major than the client, fail with that sentence,
      not with `pg_dump`'s raw error.

### 1.5 Tests
**Files:** `tests/test_backup.py`

- [ ] Layout and `meta.json` on a fake `/data`; top-level names match
      `backup/backup.sh`'s (`config`, `headscale`, `web`, `caddy`) plus `console`.
- [ ] **Consistent copy under writes:** a thread inserts rows into a WAL-mode
      SQLite while `create` runs; the copy passes `integrity_check`.
- [ ] Corrupted source DB → run fails, no archive left, previous archives untouched.
- [ ] Secrets: archive 600, backups dir 700, no `sessions.db` inside.
- [ ] Retention: old pruned, newest kept even when older than the limit, `.part`
      handling, ignores unrelated files.
- [ ] Verification catches a truncated archive and a tampered member.
- [ ] `pg_dump` path with a fake `pg_dump` in `PATH` (as `test_backup_remote.py`
      does with `rclone`): ok, no `nodes` table, version mismatch, password not in argv.

**Verify:** `python3 -m unittest tests.test_backup`

---

## Block 2: Scheduler in the supervisor (S/M, ~3-4h)

### 2.1 Cron expressions
**Files:** `aio/cron.py` (new, pure functions)

- [ ] 5 fields: `*`, `a`, `a,b`, `a-b`, `*/n`, `a-b/n`; day-of-week 0-7 (0 and 7
      = Sunday); `off` / empty = disabled. The usual rule for day-of-month +
      day-of-week (either matches when both are restricted) and no `@daily`
      aliases (say so in the docs, or add the five common ones: cheap).
- [ ] `next_run(expr, after: datetime) -> datetime`; invalid expression →
      `ValueError` with a message the wizard and the console can show.
- [ ] Local time via `TZ` (see 2.3).

### 2.2 Scheduler
**Files:** `aio/supervisor.py`

- [ ] A small thread, run-mode only, created beside the helper socket: sleeps
      on `self.stopping.wait(...)` until `next_run`, then starts
      `python aio/backup.py create --trigger scheduled` through `run_cmd`.
      Clock and `run_cmd` injectable for tests.
- [ ] Missed runs: if the container was stopped through the scheduled time,
      run **once** shortly after start (60 s delay), not once per missed slot.
- [ ] Shared lock with manual runs; a run that overlaps the next slot skips
      that slot and logs it.
- [ ] `hse reload` / SIGHUP re-reads `BACKUP_SCHEDULE` and `BACKUP_KEEP_DAYS`.
- [ ] A failed run is logged and shown in `status.json`; it does not retry in a
      loop (next slot only) and never restarts or touches the other processes.
- [ ] Nothing starts in setup mode.

### 2.3 Time zone
- [ ] Check whether `TZ=Europe/Madrid` works in the built image
      (`docker run … date`). If not, `apk add tzdata` in `aio/Dockerfile`.
      Fixes the same silent bug for log timestamps.

### 2.4 Tests
**Files:** `tests/test_cron.py`, `tests/test_supervisor.py`

- [ ] `next_run` table: every field type, month and year rollover, DST gaps,
      Sunday as 0 and 7, invalid expressions.
- [ ] Scheduler with a fake clock and a fake `backup.py`: fires on time,
      `off` never fires, missed run catches up once, overlap skipped,
      reload picks up a new schedule, stops on shutdown.

**Verify:** `python3 -m unittest tests.test_cron tests.test_supervisor`

---

## Block 3: Restore (M/L, ~6-8h)

### 3.1 Validate, then apply
**Files:** `aio/backup.py` (`restore`)

- [ ] `inspect(archive) -> Meta`: open safely (reject absolute / `..` /
      links / devices; extract with `filter="data"`), check `meta.json`
      (`edition == "aio"`, `format` known), verify every sha256, run
      `integrity_check` on each DB. Nothing in `/data` changes until this passes.
      A 1.x archive (no `meta.json`) → refuse with the Phase 5 pointer.
- [ ] `restore(archive, data_dir, *, offline)`:
  1. take a safety copy first: a normal `create` named
     `…-pre-restore-…` in `/data/backups` (skipped, with a warning, when
     there is nothing to back up yet, i.e. a fresh volume);
  2. extract into `/data/.restore-staging/` (same filesystem);
  3. for each file: write to `<name>.new`, `fsync`, `os.replace`; remove stale
     `db.sqlite-wal` / `-shm` and `accounts.db-wal` / `-shm` first;
  4. `config/` first, databases and keys after; modes forced to 600 / dirs 700;
  5. PostgreSQL archive: `psql -f headscale.sql` only on request
     (`--with-postgres`, needs the client from 1.4); otherwise say clearly that
     the SQL dump was not loaded.
- [ ] A failure mid-way restores the safety copy automatically; if that fails
      too, print the exact path of the safety copy and the manual command.
- [ ] Never restores `sessions.db`; the first start creates a fresh one.

### 3.2 Offline mode (the supported path)
- [ ] `docker stop c && docker run --rm -v hse:/data --entrypoint hse <image> restore /data/backups/<file>`
      — also the **fresh host** path: new volume, restore, then start normally:
      `settings.json` is there, so the container starts in run mode, no wizard.
- [ ] Refuse when the supervisor's pid file points to a live process
      (`/run/hse/supervisor.pid`) unless run through the online path.

### 3.3 Online mode (`docker exec c hse restore <file>`)
**Cut line: if this block runs over by more than ~3h, ship 3.2 only, document
"stop the container first" and move 3.3 to a follow-up.**

- [ ] `hse` runs `inspect` itself (so errors are immediate and in the user's
      terminal), writes `/run/hse/restore.json` (`{"archive": …, "requested": …}`,
      600) and signals the supervisor with `SIGUSR1`.
- [ ] The supervisor stops console → caddy → headscale (existing ordered stop),
      runs `restore(offline=True)` in-process, re-renders, starts the children
      in the usual order and writes the result to `/run/hse/restore-result.json`.
      `hse restore` waits for that file (timeout 120 s) and prints the result.
- [ ] Only the supervisor mutates `/data` during an online restore; `hse` never
      does. The backup lock is held for the whole operation.
- [ ] Setup mode: refuse (nothing to restore *over*; use offline restore).

### 3.4 Tests
**Files:** `tests/test_backup.py`, `tests/test_supervisor.py`

- [ ] **Round trip:** create → change data → restore → data equals the original
      (users, nodes, keys, accounts, DNS block in `config.yaml`).
- [ ] Zip-slip / symlink / absolute-path / oversize member archives are rejected
      without writing anything.
- [ ] Tampered DB (sha256 mismatch) and truncated archive are refused up front.
- [ ] Failure injected in the middle of the apply → safety copy is put back.
- [ ] 1.x archive refused with the right message; AIO archive refused by
      `restore.sh` (Block 5).
- [ ] Online restore with fake children: order of stop/start, result file,
      lock held, refused in setup mode.

**Verify:** `python3 -m unittest tests.test_backup tests.test_supervisor`

---

## Block 4: `hse` CLI, helper protocol and Status page (M, ~4-5h)

### 4.1 CLI
**Files:** `aio/hse`

- [ ] `hse backup [--out DIR]` — runs `backup.create` directly (same filesystem
      and uid, no supervisor needed), prints the path and size; non-zero on failure.
- [ ] `hse backups` — list archives (name, size, age, ok/not) from `status.json`
      + the directory.
- [ ] `hse restore <file> [--yes]` — Block 3; asks for confirmation unless `--yes`.
- [ ] `hse backup-status` is not needed: `hse backups` covers it.
- [ ] Update the module docstring; `hse health` and `hse reload` unchanged.

### 4.2 Helper protocol
**Files:** `helper/helper.py`, `aio/supervisor.py`, `web/headscale.py`,
`tests/test_docker_helper.py`

- [ ] New route `POST /backup` → backend `backup` (starts a manual run and
      returns `{"ok": true, "started": true}` immediately, or
      `{"ok": false, "error": "already running"}`). No body, no query, same
      contract tests as the other routes.
- [ ] `be_status` gains `"backup": {...}` (the contents of `status.json` plus
      `schedule`, `next_run`, `running`, `keep_days`). Absent in 1.x; the console
      treats absence as "not available".
- [ ] `web/headscale.py`: `helper_backup()` and a `backup` field in the status
      it already returns. The 1.x helper answers 404 for `/backup`, which must
      map to "not available", not to an error page.

### 4.3 Status page
**Files:** `web/status_pages.py`, `web/app.py`, `web/locales/*`

- [ ] New card **Backups**: last backup (relative time + exact), size, result
      (red with the error text on failure), next run, retention, location
      (`/data/backups`) and a hint that the volume should be mounted somewhere
      that is not the same disk.
- [ ] **Back up now** button: `POST /admin/status/backup`, CSRF, admin role only,
      audit event `backup.run`, notification `backup.failed` through the existing
      notification channels when a *scheduled* run fails. The page polls the
      status until the run ends (the nodes page already refreshes live after
      Phase 2.5; reuse that channel if it is generic, otherwise a short meta refresh).
- [ ] Download of an archive from the console: **not** in this phase (archives
      contain every secret; adding a web download widens the attack surface).
      Mention it under open questions.
- [ ] Hidden entirely when the helper reports no `backup` key (1.x compose).
- [ ] Strings in all 6 locales; `python scripts/check_i18n.py` passes.

### 4.4 Tests
**Files:** `tests/test_docker_helper.py`, `tests/test_status.py`, `tests/test_security.py`

- [ ] Contract: `POST /backup` ok / busy / wrong method / body rejected.
- [ ] Status card renders ok / failed / never / not-available states.
- [ ] Back-up-now: needs admin, needs CSRF, audits, rejected for member /
      auditor / network-admin roles.

**Verify:** `python3 -m unittest tests.test_docker_helper tests.test_status tests.test_security`

---

## Block 5: Wizard, settings, `restore.sh` guard (S, ~2-3h)

- [ ] **Wizard** (`aio/wizard.py:465`): replace "arrive in a later release" with
      what happens (a nightly archive in `/data/backups`, how long it is kept,
      the secrets warning); validate the schedule with `aio/cron.py` (accepts
      `off`) and keep days 1-3650; show the next run for the entered value.
- [ ] **Defaults:** backups **on** (`0 3 * * *`, 14 days) in the wizard and in
      headless starts. They were off by default in 1.x because they added a
      container; here they cost nothing, and a tailnet with no backup is the
      worse default. Keep the toggle.
- [ ] **render.py:** reject an invalid `BACKUP_SCHEDULE` at load time with the
      same message (headless starts fail fast instead of silently never
      backing up).
- [ ] **`scripts/restore.sh`:** after extracting, if `meta.json` says
      `edition: aio`, stop with a message that points to `hse restore`. No other
      change to 1.x behaviour (`tests/` for restore, if any, must still pass).
- [ ] Locales in all 6; `check_i18n.py` passes.

**Tests:** `tests/test_wizard.py` (schedule validation, defaults, headless
invalid schedule), `tests/test_render.py` (settings mapping), a `restore.sh`
case with an AIO archive.

---

## Block 6: Remote copies in the advanced edition (S/M, ~2-3h)

The AIO does not bundle rclone/rsync (open question below). The existing
`backup` image becomes a **sync sidecar** that reads the archives the AIO writes.

**Files:** `backup/entrypoint.sh`, `backup/remote.sh`, `backup/Dockerfile`,
`tests/test_backup_remote.py`

- [ ] `BACKUP_MODE=sync` (default stays `create`, so 1.x is unchanged): no
      `backup.sh`; every `BACKUP_SYNC_INTERVAL` (default 15 min) push each
      `headscale-easy-*.tar.gz` in `/backups` that is not yet on the remote
      (`remote.sh push`), then `remote.sh prune`. Skip `.part` and
      `*-pre-restore-*` archives.
- [ ] Idempotent and restart-safe: the "already pushed" state comes from the
      remote listing, not from a local file the sidecar might lose.
- [ ] Mount contract: the sidecar mounts the same volume (or the bind mount)
      read-only at `/backups`; document that the AIO's `/data/backups` is the
      path to share.
- [ ] A failed push is reported in the sidecar's logs and **does not** affect
      the AIO's own retention.
- [ ] The compose profile that wires this up is Phase 4 (`backup-remote`); this
      block only makes the image capable and tested.
- [ ] Tests extend `test_backup_remote.py` with the existing fake `rclone` /
      `rsync`: sync pushes new archives once, skips pushed ones, skips `.part`,
      survives a failing push and retries it next interval.

**Verify:** `python3 -m unittest tests.test_backup_remote`

---

## Block 7: CI, docs and close-out (S/M, ~3-4h)

### 7.1 CI
**Files:** `scripts/aio-smoke.sh`, `.github/workflows/ci.yml`

- [ ] Smoke test extension (headless image): `hse backup` → archive exists, 600,
      `meta.json` present → create a user and a pre-auth key → `hse backup` →
      delete the user → **offline restore** into the same volume → user is back.
- [ ] Online restore test: same, through `docker exec … hse restore --yes`,
      container stays `healthy` afterwards.
- [ ] Scheduled run in CI: start with `BACKUP_SCHEDULE="* * * * *"`, wait for
      one archive within ~90 s (proves the scheduler works in the real image
      with the real `TZ`).
- [ ] RSS gate: sample `docker stats` **while a backup runs** too; fail above
      100 MB. Image gate unchanged (250 MB); record the size delta from
      `tzdata` / the PostgreSQL client in the PR.
- [ ] `py_compile` already covers `aio/*.py`; confirm `aio/cron.py` and
      `aio/backup.py` are in it.

### 7.2 Docs
- [ ] `docs/all-in-one.md` / `.es.md`: replace "Scheduled backups are not built
      in yet" (`:135`) and the "Reserved for the built-in backups" row; add a
      Backups section: what is inside, the secrets warning, schedule syntax,
      `hse backup` / `hse restore`, offline and online restore, fresh-host
      restore, mounting a NAS at `/data/backups`, the sidecar for remote copies.
- [ ] `docs/operations*.md`: point the AIO reader at the new section; keep the
      1.x `backup` container text as is.
- [ ] `docs/security*.md`: archives are secrets; where they live; no web
      download; sessions are not restored.
- [ ] `docs/configuration*.md`: `BACKUP_SCHEDULE`, `BACKUP_KEEP_DAYS`, `TZ`,
      `BACKUP_MODE`, `BACKUP_SYNC_INTERVAL`.
- [ ] CHANGELOG entry under [2.0.0] - Unreleased.
- [ ] Tick Phase 3 in `SIMPLIFICATION_PLAN.md` (reword the "same archive layout"
      bullet to match the decision above, and link this plan) and the matching
      item in `ROADMAP.md`; settle the §8 open question on remote backups.

---

## Acceptance criteria (Phase 3 done when):

- [ ] All unit tests pass, `check_i18n.py` passes
- [ ] Default AIO container writes a nightly archive and prunes by age; the
      newest successful archive is never deleted
- [ ] `hse backup`, `hse backups`, `hse restore` work from `docker exec` and
      from `docker run --rm -v hse:/data --entrypoint hse`
- [ ] Offline restore onto an **empty volume** yields a container that starts
      in run mode (no wizard) with the same users, machines, accounts and DNS
- [ ] Devices that were registered before the backup stay registered after the restore
- [ ] A failed or interrupted restore leaves the previous data intact
- [ ] A backup under write load passes `integrity_check`
- [ ] Archives and the backups directory are 600 / 700; no session data inside
- [ ] Status page: last backup, next run, size, result, **Back up now** (admin only, CSRF, audited)
- [ ] 1.x compose, `backup/backup.sh` and `scripts/restore.sh` behave as before
      (restore.sh only gains the AIO-archive guard)
- [ ] Idle RAM < 100 MB (also during a backup) and image < 250 MB, enforced in CI
- [ ] No Docker socket, container still non-root
- [ ] CHANGELOG, ROADMAP, SIMPLIFICATION_PLAN and docs updated

---

## Files created/modified (checklist):

### New files:
- [ ] `aio/backup.py`
- [ ] `aio/cron.py`
- [ ] `tests/test_backup.py`, `tests/test_cron.py`
- [ ] `tests/fixtures/backup/` (1.x archive, AIO archive samples; fake keys/IDs only)

### Modified files:
- [ ] `aio/hse` (`backup`, `backups`, `restore`)
- [ ] `aio/supervisor.py` (scheduler, `/backup` backend, status key, online restore)
- [ ] `aio/render.py` (schedule validation), `aio/wizard.py` (backups step)
- [ ] `aio/Dockerfile` (`tzdata`, PostgreSQL client if it fits)
- [ ] `helper/helper.py` (`POST /backup`), `tests/test_docker_helper.py`
- [ ] `web/headscale.py`, `web/app.py`, `web/status_pages.py`, `web/locales/*`
- [ ] `backup/entrypoint.sh`, `backup/remote.sh`, `tests/test_backup_remote.py`
- [ ] `scripts/restore.sh` (AIO guard), `scripts/aio-smoke.sh`
- [ ] `tests/test_supervisor.py`, `tests/test_wizard.py`, `tests/test_render.py`,
      `tests/test_status.py`, `tests/test_security.py`
- [ ] `.github/workflows/ci.yml`
- [ ] `docs/`, `README.md`, `README.es.md`, `CHANGELOG.md`, `ROADMAP.md`, `SIMPLIFICATION_PLAN.md`

---

## Estimated timeline:

| Block | Effort | Cumulative |
|-------|--------|------------|
| 1. `backup.py` create + verify | 6-8h | 8h |
| 2. Scheduler | 3-4h | 12h |
| 3. Restore (offline + online) | 6-8h | 20h |
| 4. CLI, helper, Status page | 4-5h | 25h |
| 5. Wizard, settings, `restore.sh` guard | 2-3h | 28h |
| 6. Sync sidecar | 2-3h | 31h |
| 7. CI, docs, close-out | 3-4h | 35h |
| **Total** | **~28-35h** | **~4 days** |

Blocks 1 → 2 → 3 depend on each other. Blocks 4, 5 and 6 can start once Block 1
lands (4 needs 2 for `next_run`). If time is short, the cut is Block 3.3 (online
restore) and the download/remote niceties, never verification or the round-trip tests.

---

## Verification (end to end)

1. `python -m unittest discover -s tests -v` and `python scripts/check_i18n.py`.
2. `docker build -f aio/Dockerfile -t hse-aio .` → image < 250 MB.
3. Headless start (see Phase 2), `docker exec c hse backup` → archive in
   `/data/backups`, `tar tzf` shows the layout above, `meta.json` ok.
4. Create a user and register a device (Phase 0 flow). Back up. Delete the user.
   `docker stop c`, offline restore, `docker start c` → user and device are back,
   the device is still online without re-authenticating.
5. Same through `docker exec c hse restore --yes <file>` while running.
6. Empty volume: `docker run --rm -v new:/data --entrypoint hse hse-aio restore <file>`,
   then start normally → run mode, no wizard, same accounts and DNS.
7. `BACKUP_SCHEDULE="* * * * *"` → one archive per minute, retention prunes,
   `off` → nothing. `TZ=Europe/Madrid` shifts the time as expected.
8. Console → Status: card shows last/next backup; **Back up now** works as admin
   and is forbidden for a member. Break the DB file permissions → failure shows
   in red and fires the notification.
9. `docker stats` idle and during a backup < 100 MB.
10. Remote sidecar: `BACKUP_MODE=sync` with a local-directory rclone remote
    uploads each new archive once.

---

## Notes for execution:

1. **Work in order:** create → scheduler → restore; the UI comes after the engine.
2. **Verify at every step:** run tests after each subtask; one commit per subtask.
3. **Never break 1.x:** the split compose, `install.sh`, `backup/backup.sh` and
   `scripts/restore.sh` keep working; the only change to them is guarded.
4. **A backup you have not restored is not a backup:** every format change comes
   with a round-trip test, and CI restores for real.
5. **Test fixtures:** low-entropy fake keys/IDs only (GitGuardian); never real archives.
6. **Test on the live stack by exact name,** never by list position.
7. **Phase 4 builds on this:** the `backup-remote` compose profile wraps the sync
   sidecar from Block 6; Phase 5's migration reuses `inspect` to read 1.x
   archives, which this phase deliberately refuses.

---

## Open questions

- [ ] **Remote backups inside the AIO** (bundle rclone, +~50 MB) or only through the
      sidecar? This plan assumes the sidecar; revisit if Block 6 feels clumsy.
- [ ] **Download an archive from the console?** Convenient for off-site copies, but
      it exposes every secret to a web session. Default: no, `docker cp` /
      mounted volume only.
- [ ] **Restore from the setup wizard** ("I already have a backup")? Nice for
      migrating hosts, but it needs an upload on an unauthenticated-until-token
      page. Default: later; `hse restore` on a fresh volume covers it.
- [ ] **Encrypt archives** (passphrase)? Default: no in this phase; the volume and
      the remote's own encryption are the answer, and the docs say so.
- [ ] **Editable schedule in the console** (instead of env / `settings.json` +
      `hse reload`)? Default: read-only on the Status page for now.
