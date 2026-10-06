# Phase 7 Execution Plan — Headscale Easy 2.0: simple Compose app, advanced configurations, no trace of 1.x

**Status:** 🚧 In progress.

Follows PHASE6_EXECUTION_PLAN.md (the 1.x stack and the Authentik code are already gone).
**Estimated effort:** M/L (5-6 days)
**Goal:** Headscale Easy 2.0 is released as one thing:

- **Simple install = a Docker Compose app.** One `compose.yaml` and one `.env`, versioned as 2.0
  (the image tag is pinned, never `latest`): `docker compose up -d` and open the wizard. No installer
  script, no generated files.
- **Advanced configurations** are optional add-ons next to it: external PostgreSQL, a custom OIDC provider
  (Authentik, Pocket ID, Keycloak, Google), a proxy in front, remote backups. Each is a small Compose
  overlay (`-f compose.yaml -f advanced/<name>.yaml`) plus a page in the docs. Nobody who wants the simple
  install ever reads them.
- **No trace of 1.x** in the repository: no code, file, doc, test, workflow or changelog entry that
  mentions the split stack, the old installer, migration, "legacy" or a 1.x number. The history stays in
  git; the tree describes only 2.0.

**Done when**
- `docker compose up -d` from the repo's `compose.yaml` (or a copy of it) starts a healthy container on the
  pinned 2.0 image and ends in the first-run wizard, with no other file needed except an optional `.env`;
- every advanced configuration works by adding one overlay file and a few variables, and is tested
  (`docker compose config` for all, a real PostgreSQL in CI, the proxy and OIDC examples run by hand and
  said so);
- `git grep -i -E "1\.x|legacy|split stack|hs-helper|migrat|headscale-easy-aio|preview"` finds nothing that
  refers to a previous version (a guard test enforces it);
- the 2.0 gaps listed in Block 1 are closed;
- all tests, `check_i18n.py`, `validate.sh`, `shellcheck`, `mkdocs build --strict` and the AIO smoke pass;
  image < 250 MB, idle RAM < 100 MB.

---

## Decisions

- **No installer.** Compose apps (Immich, Vaultwarden, Gitea) are installed with `curl` of the compose file
  + `.env` and `docker compose up -d`. `install.sh`, `uninstall.sh` and `scripts/embed-compose.sh` go;
  the docs give the three commands. (The wizard already asks everything the installer asked.)
- **The version is pinned.** `compose.yaml` uses `ghcr.io/insanerask77/headscale-easy:${HSE_VERSION:-2.0.0}`.
  A `VERSION` file at the root is the single source: the compose default, `web/version.py` and the image
  label are checked against it in CI, so a release cannot ship a stale tag. `latest` is published but never
  used in the docs.
- **One image name.** `headscale-easy`. The `headscale-easy-aio` alias is dropped: nobody uses it, and
  keeping it is a trace of the old naming.
- **Advanced = overlays, not profiles in the simple file.** The simple `compose.yaml` has exactly one
  service. `advanced/` holds `postgres.yaml` (external PostgreSQL settings, optionally a bundled server
  for people who do not have one), `backup-remote.yaml`, `proxy.yaml` (no published 80/443, trusted
  proxy), and `oidc/<provider>/` for Authentik, Pocket ID, Keycloak and Google (a compose overlay where a
  provider is run beside it, otherwise a page). Overlays only add or override; they never need edits to the
  base file.
- **Backups, TLS, DERP, local accounts** stay as built: they are part of the simple edition.
- **Planning documents leave the tree.** `PHASE*_EXECUTION_PLAN.md` and `SIMPLIFICATION_PLAN.md` are full
  of 1.x. Once phase 7 is accepted they are deleted (recoverable from git history); `ROADMAP.md` and
  `CHANGELOG.md` start from 2.0.

## Findings

- The 1.x leftovers in the tree (from a grep): the planning docs, `CHANGELOG.md`, `ROADMAP.md`,
  `install.sh` / `uninstall.sh`, `docker.yml` / `docker-dev.yml` (alias), `bug_report.yml`,
  `PULL_REQUEST_TEMPLATE.md`, `CONTRIBUTING.md`, `AI_USAGE.md`, `mkdocs.yml`, many docs pages,
  `tests/test_no_legacy.py`, comments in `web/*.py` and `aio/render.py`, golden fixtures.
- The root of a developer's checkout may hold git-ignored files of the old installer (`Caddyfile`,
  `docker-compose.override.yml`, `headscale-config.yaml`, `helper/`, `data/`). They are not in the repo; they
  are not touched here.
- `deploy/compose/docker-compose.yml` already has the right shape (one service, named volumes, no
  capability, `no-new-privileges`) but defaults to `latest` and carries the sidecar as a profile; it
  becomes the root `compose.yaml` and the overlays.
- `web/version.py` says `2.0.0-dev`; `release.yml` already reads the tag.
- Gaps found while closing phase 6: no screen shows the link of a local invitation; SMTP settings exist
  but nothing sends mail since the Authentik code left; the control-socket API still says `helper`
  (`HELPER_SOCKET`, "hs-helper is not running", the `docker` key of `/status`); `:latest` of
  `headscale-easy` points at an old image until the first 2.0 tag.

---

## Block 1: close the 2.0 gaps (M, ~8-10h)

- [x] **Invitation link screen.** Creating an invitation (Users → Invite) shows the single-use link once, with
      a copy button and the expiry; same for password-reset links. Tests in `test_local_accounts*.py`.
- [x] **Email for invitations and resets.** With `SMTP_*` set, an admin can click "Send by e-mail" (never
      automatic); stdlib `smtplib`, TLS/SSL as configured, no credentials in logs or the audit log. Without
      SMTP the button is absent and the link is the only way. If this proves too large, remove the `SMTP_*`
      settings and docs instead (decide in the PR and say why); do not leave settings nothing reads.
- [x] **Control socket naming.** `HELPER_SOCKET` → `CONTROL_SOCKET`, "hs-helper is not running" →
      "the supervisor is not running", the `docker` key of `/status` → `processes`, in code, tests, the four
      locales and docs (`check_i18n.py` green).
- [x] **Version surface.** `VERSION` file at the root (`2.0.0`); `web/version.py` reads it; the footer and
      Status page show it; the image carries `org.opencontainers.image.version`; a unit test fails if they
      differ.
- [x] **Sweep the console for dead code** left after phase 6 (unused imports, settings, strings): `python -m
      pyflakes`-style check through `compileall` + a grep test for unused locale keys.
- [x] Tests, `check_i18n.py`, `validate.sh`.

**Done.** E-mail was implemented (not removed): `web/mailer.py`, stdlib `smtplib`, only on an
administrator's click and only with `SMTP_HOST` set; the Users page also got an Invite dialog, a
"Password reset link" action, the pending invitations and a link box shown once (`tests/test_invite_links.py`).
The control socket is `control.sock` / `CONTROL_SOCKET`, `/status` has `processes` and no `docker` key.
`VERSION` (2.0.0) is read by `web/version.py`, copied into the image and used for its OCI label
(`tests/test_version.py`). 180 unused locale entries and four dead imports were removed, and
`check_i18n.py` now fails on unused entries. Suite 1032 tests; AIO smoke 232 MB image, 71 MB idle RAM,
72 MB during a backup. Not run: sending through a real SMTP server (a fake one is tested).

## Block 2: the simple install is a Compose app (M, ~6-8h)

- [x] Root `compose.yaml` (one service, pinned `${HSE_VERSION:-2.0.0}`, named volumes, no capability,
      `no-new-privileges`, `sysctls` for 80/443, healthcheck from the image) and `.env.example` (every line
      commented and optional: `HSE_PUBLIC_URL`, `HSE_TLS`, `ACME_EMAIL`, `TZ`, ports).
- [x] Delete `install.sh`, `uninstall.sh`, `scripts/embed-compose.sh`, the `Makefile` targets for them,
      `tests/test_install.py` (replace with `tests/test_compose.py`: `docker compose config` when Docker
      exists, no Docker socket, pinned tag equals `VERSION`, no `latest`, ports, hardening options).
- [x] `scripts/compose-smoke.sh` runs the root `compose.yaml` (start, healthy, wizard answers, backup, restart
      keeps the data, down/up keeps the volumes).
- [x] First-run path in the quick start, three commands, and the same in README / README.es.
- [x] Remove `deploy/compose/` (its content is the root file and the overlays of Block 3).

**Done (Block 2):** `compose.yaml` and `.env.example` at the root; the installer, uninstaller, `embed-compose.sh`,
`test_install.py` and `deploy/compose/` are gone; `tests/test_compose.py` (14 tests, with `docker compose config`);
`scripts/compose-smoke.sh` ran for real (healthy, hardened, a backup, `down && up -d` keeps the data and the
administrator signs in, the remote backup add-on uploads once and not again after a restart; it needed
`SMOKE_DERP_PORT` because a foreign container held 3478/udp). The sidecar is `advanced/backup-remote.yaml`
(an overlay), which Block 3 completes with the other add-ons. The quick start in the READMEs and
`docs/getting-started*` is the three commands; `docs/advanced*`, `operations*`, `hardening`, `why`, `index`,
CONTRIBUTING and the PR template no longer mention the installer.

## Block 3: Advanced configurations (M/L, ~8-10h)

- [x] `advanced/` with one overlay per option, each with a README of at most one screen:
      `postgres.yaml` (connection variables + read-only role; optional bundled server), `backup-remote.yaml`
      (the sidecar), `proxy.yaml` (+ nginx, Traefik, Caddy, Nginx Proxy Manager snippets), and
      `oidc/` (`authentik`, `pocket-id`, `keycloak`, `google`: the variables the console needs, the redirect
      URIs and scopes, who may sign in, role groups; a provider overlay where it can be run beside).
- [x] Move `deploy/examples/*` there, deleting anything that talks about 1.x, `legacy` or migration.
- [x] Each overlay is checked by `docker compose -f compose.yaml -f advanced/<x>.yaml config` (and all of
      them together) in `validate.sh` and `tests/test_advanced.py`. — *done; the combinations are the three listed in `validate.sh`*
- [x] The PostgreSQL overlay is run in CI against 16, 17 and 18 as the phase 4 rounds did (Headscale, the
      console through the read-only role, backup and restore). — *run for real on 16, 17 and 18 with `scripts/advanced-smoke.sh`, which found that PostgreSQL 18 needs the volume at `/var/lib/postgresql`; the CI steps are wired in `ci.yml` but have not run on GitHub*
- [x] Docs: one "Advanced configurations" section (`docs/advanced/`), linked from the README with one line;
      the simple docs never mention an advanced option except that link. — *the section is `docs/advanced/` (en and es); other pages still link into it where a setting is explained (a link, not a copy)*

## Block 4: versioning and release (S/M, ~4-5h)

- [x] CI check that `VERSION`, `compose.yaml`'s default tag, `web/version.py` and the changelog heading agree. — *`scripts/release_info.py check` (in `validate.sh` and `tests/test_release.py`, `test_version.py`); on a tag it also needs the tag = `VERSION` and a dated CHANGELOG entry*
- [x] `release.yml` / `docker.yml`: on tag `vX.Y.Z` publish `headscale-easy:X.Y.Z`, `:X.Y`, `:X`, `:latest`
      and `headscale-easy-backup` with the same tags; no `-aio` alias; GitHub release notes from the
      CHANGELOG; a job that pulls the published image and runs the compose smoke on it. — *written, parsed as YAML and covered by `tests/test_release.py` (tag list simulated by `release_info.py tags`); only a real tag on GitHub runs the `verify`, `publish`, `smoke-published` and release jobs*
- [x] `docker-dev.yml`: `:next` / `:edge` for the branch, clearly not for the compose file. — *`:next`, `:dev`, `:branch-*` there, `:edge` (main) in `docker.yml`; the docs and the workflow comments say they are not for `compose.yaml`*
- [x] A short "Upgrading" section: change `HSE_VERSION`, `docker compose pull && up -d`; backups are taken
      before (`hse backup`). — *`docs/operations*.md`, with rollback and which tag to use; `CONTRIBUTING.md` Releasing updated*

## Block 5: no trace of 1.x (M, ~6-8h)

- [x] Delete `PHASE*_EXECUTION_PLAN.md` and `SIMPLIFICATION_PLAN.md` (after the other blocks are accepted;
      their content is in git history); rewrite `ROADMAP.md` and `docs/roadmap.md` as a 2.x forward-looking
      list; `CHANGELOG.md` / `docs/changelog.md` start at `[2.0.0]` with a clean release note.
- [x] Remove every mention of 1.x, `legacy`, split stack, hs-helper, migration, `headscale-easy-aio`, "preview",
      "unreleased", "phase N" from code comments, docs (en and es), `mkdocs.yml`, issue and PR templates,
      CONTRIBUTING, AI_USAGE, workflows and tests; keep only what is true of 2.0.
- [x] Regenerate the render golden fixtures if their text changes; keep them frozen.
- [x] Replace `tests/test_no_legacy.py` with `tests/test_no_trace.py`: tracked files must not match the
      forbidden terms (an allowlist for words that are really about something else, each with a reason),
      and the removed paths stay removed.
- [x] Leave git-ignored local leftovers alone and say so.

> Done: the tracked tree has no reference to a previous version; `tests/test_no_trace.py` enforces it (its allowlist
> lists each word that means something else, with the reason). The earlier plans and the roadmap history were
> deleted; PHASE7_EXECUTION_PLAN.md is the last one and goes when the phase is accepted.

## Block 6: docs and final acceptance (M, ~6-8h)

- [x] README / README.es: what it is, the three-command quick start, a short "Advanced configurations" link
      (README.es gained the missing Screenshots section; "official Headscale image" corrected to "binary").
- [x] `docs/getting-started*`, `docs/configuration*`, `docs/operations*`, `docs/hardening.md`,
      `docs/architecture.md`, `docs/advanced/*`: written for 2.0 only; variable reference still checked by
      `scripts/gen_env_reference.py`; numbers measured by `aio-smoke.sh` (232 MB image, 71 MB idle RAM,
      71 MB during a backup).
- [ ] `mkdocs build --strict` clean (it is); Spanish pages in sync for every English page that changes
      (the paired pages are in step; `why`, `hardening`, `architecture`, `security`, `contributing`,
      `roadmap`, `changelog` and `ai-usage` have no Spanish page and the site falls back to English).
- [ ] **Final acceptance on a clean host** — recorded below (almost all of it ran; the gaps are listed).
- [ ] Tag `v2.0.0` **only when the user asks** (release is outward-facing).

### Recorded acceptance run (2026-10-06, local Docker, image built from this tree as `headscale-easy:2.0.0`)

Ran for real:
- `compose.yaml` + `.env.example` copied to an empty directory, `docker compose up -d` with the pinned
  default tag: healthy, `/admin/healthz` 200, `hse health` ok, `cap_drop: ALL` and `no-new-privileges`,
  Headscale v0.29.4, local sign-in works (headless `HSE_ADMIN_EMAIL`/`HSE_ADMIN_PASSWORD`).
- First run from a bare `compose.yaml` (no `.env`): setup mode, the one-time token is in
  `docker compose logs`, `/admin/setup` answers 200 and every other page redirects to it. The wizard form
  itself was not walked through click by click (the headless path covers the same settings).
- A user and a pre-auth key created with the Headscale CLI; a real `tailscale/tailscale` client
  (userspace networking, `--login-server=http://localhost:18081`) registered, got `100.64.0.1`, was online
  and showed up on the console's Machines page.
- `hse backup`; `docker compose down && up -d` kept the users, the node and the sign-in; `hse restore`
  brought back the state of the backup (a user created afterwards was gone).
- Overlays: `docker compose config` for every overlay and the combinations (`scripts/validate.sh`); a real
  run of `advanced/postgres-bundled.yaml` on PostgreSQL 16, 17 and 18 (read-only role, backup with the
  dump, restore) and of `advanced/proxy.yaml` behind a real nginx (Secure cookie, real client address,
  forged headers ignored) with `scripts/advanced-smoke.sh`; `advanced/backup-remote.yaml` with
  `scripts/compose-smoke.sh` (one upload, none after a restart; remote is a local directory).
- `scripts/aio-smoke.sh`: 232 MB, 71 MB idle, 71 MB during a backup, offline and online restore, scheduled
  backup, setup mode.

Found and fixed during the run:
- The `headscale` CLI inside the container needs `-c /data/config/config.yaml` (without it: "context
  deadline exceeded"); the Operations page now says so.
- `compose.yaml` published `3478:3478/udp` fixed while the relay advertises the port in `HSE_DERP_PORT`:
  moving only the host side would break the relay. Both sides now use `${HSE_DERP_PORT:-3478}` (also in
  `advanced/proxy.yaml` and `advanced/oidc/pocket-id.yaml`), documented in `.env.example` and the
  reference; the smoke scripts set the variable instead of editing the file.

Only read, not run: a real sign-in through an OIDC provider (Pocket ID, Authentik, Keycloak, Google),
Pocket ID/Authentik first start with their blueprints on a public domain, Traefik, Caddy and Nginx Proxy
Manager in front, Let's Encrypt, S3/B2/SFTP as the remote backup target, a managed PostgreSQL, a
second client reaching the first through the DERP relay, Spanish pages that do not exist.

---

## Order of work

```text
Block 1 (gaps) ─────────────────────────────┐
Block 2 (compose app) ──▶ Block 3 (advanced) ──▶ Block 4 (release) ──▶ Block 5 (no trace) ──▶ Block 6
```

Blocks 1 and 2 touch different files and run in parallel. Block 3 needs Block 2's `compose.yaml`; Block 4
needs both; Block 5 runs last because it deletes the plans and rewrites text everywhere; Block 6 closes.

## Risks

| Risk | Mitigation |
|---|---|
| Removing the installer loses the guided path | The wizard asks the same questions; `.env.example` is fully commented; the smoke test runs exactly the documented three commands |
| A release ships a stale tag in `compose.yaml` | `VERSION` is the single source and CI compares it with the compose default, `web/version.py` and the changelog |
| An overlay drifts from the base file | Every overlay and every combination goes through `docker compose config` in CI; PostgreSQL runs for real |
| The "no trace" sweep deletes something true | The guard test has a reasoned allowlist; deletions are done after the functional blocks, in one reviewed PR |
| Deleting the plans loses context | They stay in git history; the PR names the last commit that has them |
| Email sending leaks credentials | stdlib only, no secrets in logs/audit, tests assert it; never automatic |

## Acceptance criteria (Phase 7 done when)

- [ ] The simple install is the root `compose.yaml` and works with `docker compose up -d` on the pinned 2.0 image
- [ ] Every advanced configuration is an overlay under `advanced/`, validated in CI, documented in one place
- [ ] `git grep` finds no reference to a previous version; the guard test enforces it
- [ ] The Block 1 gaps are closed and tested
- [ ] Tests, `check_i18n.py`, `validate.sh`, `shellcheck`, `mkdocs --strict` and the AIO smoke pass; size and RAM limits hold
- [ ] The final acceptance run is recorded above
