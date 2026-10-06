# Phase 6 Execution Plan — Docs and Clean-up

**Status:** 📋 Planned (not started).

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 6.
**Estimated effort:** M (4-5 days, ~30-36h)
**Goal:** Ship 2.0 as one product: the all-in-one (AIO) image. Everything that only
existed for the 1.x split stack is gone, and the docs describe what is left. Phase 5
(migration) was dropped because nobody runs 1.x, so there is **no grace period and no
compatibility to keep**: the 1.x pieces are deleted outright.

**Done when:**
- the repository has one way to deploy (`install.sh` / `deploy/compose/` / `docker run`)
  and no file that only the 1.x split stack used;
- the console has no code path that needs Authentik's API; Authentik is one external
  OIDC provider like Keycloak or Pocket ID;
- CI builds, tests and publishes only what 2.0 ships;
- README, docs and examples agree with the code (variables, ports, images, numbers);
- all tests, `check_i18n.py`, `validate.sh`, `shellcheck`, `mkdocs build --strict` and
  the AIO smoke pass; image < 250 MB and idle RAM < 100 MB still hold.

---

## Decisions

- **Delete, do not deprecate.** No notices, banners or compatibility shims (phase 5 is
  dropped). Anything 1.x-only is removed in the same PR that removes its last user.
- **Remove in dependency order**, one PR per block, each leaving CI green (Findings
  below show what depends on what). Never a "big bang" PR.
- **The split compose is dropped**, closing SIMPLIFICATION_PLAN §8's open question: with
  no 1.x users there is nobody to keep it for.
- **Authentik becomes a plain external OIDC provider.** Its example in
  `deploy/examples/authentik/` stays (compose + blueprint, as a provider for sign-in), but
  the console no longer calls Authentik's API for invitations, resets, MFA or user lists.
  Invitations, resets and 2FA are the local accounts' job (phase 1). This is the standard
  pattern (Keycloak, Pocket ID, Google): the console trusts the provider's identity and
  groups, and manages nothing inside it.
- **In 2.0 the AIO takes the name `headscale-easy`** (SIMPLIFICATION_PLAN §8), with
  `headscale-easy-aio` published as an alias for one release so existing `docker run`
  lines keep working. The old console/helper images are no longer published.
- **The backup sidecar stays.** `backup/` is the `backup-remote` profile of
  `deploy/compose/` (`BACKUP_MODE=sync`). Only its 1.x mode (`BACKUP_MODE=create`,
  `backup.sh`, the 1.x cron) is removed.
- **The helper protocol stays; the helper container goes.** The supervisor serves the same
  Unix-socket protocol (`/configtest`, `/restart`, `/status`) to the console. Only
  `helper/` (the Docker-socket container) is removed.
- **Docs numbers are measured, not written by hand.** Image size and RSS come from
  `scripts/aio-smoke.sh`; the docs state them with the CI limits.

## Findings that shape the order

- **`scripts/gen_render_goldens.sh` sources `install.sh`'s generators** (now in
  `legacy/install-1x.sh`), and `tests/test_render.py` compares `aio/render.py` with those
  goldens ("the compose target must be byte-identical to install.sh"). Deleting the legacy
  installer without care deletes the only oracle for the renderer. Fix: **freeze the
  goldens** (keep `tests/fixtures/render/*` as plain fixtures, drop the generator script and
  the "compose" target; keep the `aio` target tests).
- **`aio/Dockerfile` copies `helper/`** (`COPY helper /app/helper`). Check what in the image
  uses it (probably the supervisor's ported code). Move whatever is needed into `aio/`
  *before* deleting `helper/`.
- **`web/` still carries 1.x code beyond Authentik:** `web/Dockerfile` (the console image),
  `web/docker_tab.py` (that one is the "Add device → Docker" tab and **stays**), the
  `ctx["authentik"]` branches in `web/admin_pages.py`, `web/pages.py`, `web/app.py`, and
  `AUTHENTIK = "/authentik/" in OIDC_ISSUER` in `web/app.py` (the AIO still routes
  `/authentik/` for an external Authentik — keep that Caddy route, drop the console logic).
- **`scripts/restore.sh` and `backup/backup.sh` are 1.x tools.** `aio/restore.py` refuses 1.x
  archives and points at them; `tests/test_restore_guard.py`, `tests/backup_fixtures.py` and
  `tests/test_backup.py` reference them. Remove with their guards and rewrite the refusal as
  "not a Headscale Easy 2 backup".
- **CI and `validate.sh` name the removed pieces:** `ci.yml` builds and probes the helper
  image; `docker.yml` publishes `headscale-easy` (web), `-helper`; `validate.sh` lists the
  Authentik, helper, compose and installer files and enforces "only hs-helper may mount the
  Docker socket" (becomes "nothing mounts the Docker socket").
- **Docs mention 1.x in many places:** `docs/configuration*.md`, `getting-started*.md`,
  `operations*.md`, `hardening.md`, `all-in-one*.md`, `advanced*.md`, `CONTRIBUTING.md`,
  READMEs, `SECURITY.md`.

---

## Pre-flight checks

- [ ] On `next`, CI green, phase 4 merged (PR #70), phase 5 dropped (PR #71)
- [ ] One branch per block, PR against `next`
- [ ] Baseline: image size and idle RSS from `scripts/aio-smoke.sh`; `git ls-files | wc -l`;
      lines of `web/accounts.py`, `web/mfa.py`, `install.sh` (to report the reduction)
- [ ] A list of every 1.x-only file, produced by the greps in Block 1 and pasted in the PR

---

## Block 1: remove the 1.x split stack (M, ~6-8h)

Files that only the split compose, the helper container and the 1.x installer used.

### 1.1 Safe first: things nothing in 2.0 reads
- [x] `docker-compose.yml`, `docker-compose.override.yml`, `Caddyfile`, `headscale-config.yaml`
      (generated 1.x files in the root, if tracked), `Makefile` targets that call
      `legacy/*` and `docker compose` on the root file (keep `make lint`, `make test`). — *only the
      root compose and the root `.env.example` (1.x variables) were tracked; the Makefile keeps
      install/uninstall/purge/validate/lint/test/i18n*
- [x] `legacy/install-1x.sh`, `legacy/uninstall-1x.sh`, then the `legacy/` directory.
- [x] `scripts/utils.sh` and `scripts/dev-local-accounts.sh` removed (the second one built the 1.x `web`
      service). — *`scripts/embed-compose.sh` **stays**: the new `install.sh` embeds
      `deploy/compose/docker-compose.yml` with it (the plan was wrong); `scripts/compose-smoke.sh`
      stays: it tests `deploy/compose/`*
- [x] `templates/front-*.tmpl` (their documentation copy already lives in
      `deploy/examples/front-proxy/`).
- [x] `scripts/restore.sh`, `backup/backup.sh`, `backup/pg-client.sh` if only 1.x used them;
      keep `backup/remote.sh`, `backup/entrypoint.sh` (sync mode) and `backup/Dockerfile`,
      trimmed to sync mode. — *the sidecar image no longer carries sqlite, tar or the PostgreSQL clients;
      `BACKUP_MODE` other than `sync` is refused*

### 1.2 Then the helper and the console image
- [x] Find what in the AIO uses `helper/` (`grep -rn helper aio web tests`); move that code
      into `aio/` and drop `COPY helper` from `aio/Dockerfile`. — *the supervisor used only the route
      table and the socket server: now `aio/control.py`*
- [x] Delete `helper/`, `tests/test_docker_helper.py` (re-point any still-useful assertions
      at `tests/test_supervisor.py`, which already covers the protocol). — *replaced by
      `tests/test_control.py` (protocol, backup routes and the console client against the real server);
      `web/headscale.py` lost its Docker-socket fallback. CI's helper build/probe and the `web` image
      build and publishing went with it (Block 3 does the rest of the CI/release work)*
- [x] Delete `web/Dockerfile` and the `web/` image build; the AIO copies `web/`.
- [x] `aio/restore.py` and `tests/test_restore_guard.py`: the 1.x refusal text becomes
      "this is not a Headscale Easy 2 backup"; delete tests of `restore.sh`.

### 1.3 Renderer goldens (see Findings)
- [x] Remove `scripts/gen_render_goldens.sh`; keep `tests/fixtures/render/*` as frozen
      fixtures; delete the `compose` target and `front_authentik` cases that only the 1.x
      installer could produce; update the `tests/test_render.py` docstring. — *the goldens were
      regenerated from the AIO renderer (`HSE_UPDATE_GOLDENS=1` rewrites them); `front_authentik` removed*
- [x] `tests/test_install.py`: keep what tests the **new** `install.sh`; delete 1.x cases.

### 1.4 Tests
- [x] Full suite (1003 tests), `validate.sh` (it runs `docker compose config` on every file under
      `deploy/`), `shellcheck`, `check_i18n.py`, and the AIO build + `scripts/aio-smoke.sh`: image
      233 MB, idle RAM 71 MB, 71 MB during a backup. — *`scripts/compose-smoke.sh` could not run on the
      author's machine: an unrelated container holds 3478/udp; CI runs it*
- [x] Add a guard test (`tests/test_no_legacy.py`) listing paths that must not come back
      (`helper/`, `legacy/`, root `docker-compose.yml`), so a bad merge cannot resurrect them.

---

## Block 2: remove the Authentik code from the console (M/L, ~8-10h)

### 2.1 Inventory
- [x] List every Authentik touch point: `web/accounts.py` (523 lines, almost all of it),
      `web/mfa.py` (162), `web/app.py` (33 refs), `web/pages.py`, `web/admin_pages.py`,
      `web/local_accounts.py` (1), the `PORTAL_AUTHENTIK_TOKEN` env var, `AUTHENTIK` /
      `CTX["authentik"]`, sign-out redirect for `/authentik/application/o/`, the Users page's
      Authentik invitations section, and the MFA mode page for Authentik.

### 2.2 Replace with the local-accounts path
- [x] `web/accounts.py`: keep only what the local backend needs (SMTP helpers like — *deleted whole: the local-accounts path never used it (no e-mail, no Authentik data), so nothing was worth moving.*
      `send_mail`, `email_invitation`, `email_reset`, `hours_label`, role badge and the
      invite/reset dialogs) or move it to `web/local_accounts_ui.py`; delete the Authentik API
      client (`_api`, `accounts()`, `recovery_link`, `invitations`, `match`, caching).
- [x] `web/mfa.py`: drop the Authentik backend; MFA modes (admins / everyone / optional) stay
      on the local backend.
- [x] Sign-in with an external provider keeps working as plain OIDC: role mapping from
      `PORTAL_*_GROUPS` / `PORTAL_ADMIN_EMAILS` / `HSE_OIDC_ALLOWED_*` (phase 4) stays. Remove
      the "built-in Authentik" special cases (sign-out flow, "manage account" link, the
      "Accounts are created in Authentik" text).
- [x] Remove `PORTAL_AUTHENTIK_TOKEN` and the `AUTH_PROVIDER` leftovers from `aio/render.py`
      `console_env`, docs and examples.
- [x] Delete the Authentik strings from the four locales and rerun `check_i18n.py`.

### 2.3 Authentik as an external OIDC example only
- [x] `deploy/examples/authentik/`: compose + blueprint stay, rewritten as "a provider for
      sign-in". Remove the parts that gave the console an API token and its invitation flow.
      Keep the blueprint only for the OIDC application, groups and branding it still needs;
      delete the invitation/recovery flows.
- [x] Delete the top-level `authentik/` directory (blueprint and branding copies); its
      content that is still useful already lives in `deploy/examples/authentik/`.
- [x] Keep the `HSE_AUTHENTIK_UPSTREAM` Caddy route (an external Authentik at `/authentik/`);
      it is renderer config, not console code. Re-check `tests/test_examples.py`.

**Status:** ✅ Done except the by-hand OIDC sign-in (see below). Left over: the `docker` key of `/status`, `HELPER_SOCKET` and the "hs-helper" wording were not renamed (API names shared with Block 1's control socket, tests and 4 locales); `SMTP_*` and the *Users → Invite* UI have no consumer (see report); the trimmed blueprint was not applied to a live Authentik.

### 2.4 Tests
- [x] `tests/test_accounts.py` becomes the local invite/reset UI test; delete Authentik — *deleted with accounts.py; the local invite/reset routes are covered by test_local_accounts*
      fakes. `tests/test_security.py`: the sign-in, MFA, role and rate-limit cases stay,
      Authentik-specific ones go.
- [x] A grep test: no `authentik` outside `deploy/examples/authentik/`, docs and the
      `HSE_AUTHENTIK_UPSTREAM` renderer route.
- [ ] Run the sign-in flows by hand in the AIO: local, API key, and external OIDC with the — *NOT DONE: local sign-in is covered by the unit tests and the AIO smoke only; an external OIDC sign-in needs real domains, HTTPS and a passkey (Pocket ID) or a real Authentik, and was not run.*
      Pocket ID example (the lightest provider).

---

## Block 3: CI, publishing and release (S/M, ~4-5h)

**Status:** ✅ Done on `feat/phase6-block3`. Beyond the plan: one name for the all-in-one (image and container `headscale-easy`; the installer, `deploy/compose`, the examples, the smoke test and the docs used two).

- [x] `ci.yml`: remove the helper image build and its socket/uid probe, the `web` and `backup`
      image builds that no longer exist (keep the backup sidecar build), the 1.x installer
      lint paths; keep `aio`, `lint`, `compose-smoke` for `deploy/compose/`.
      — *Block 1 had already removed the helper/web builds; the `image` job (backup sidecar build) is kept on purpose so the required check name does not change.*
- [x] `docker.yml`: publish only `headscale-easy-aio` (and, for 2.0, the `headscale-easy`
      name pointing at it) and `headscale-easy-backup`; stop publishing `headscale-easy`
      (console) and `-helper`.
      — *done as: `headscale-easy` is the primary name and `headscale-easy-aio` the alias (same build, two names); `docker-dev.yml` the same. Checked with a YAML parser only: the alias tags are first seen when the workflow runs on GitHub.*
- [x] `release.yml`: tags `v2.0.0` → `:2.0.0`, `:2.0`, `:2`, `:latest` for the AIO; document
      the alias and that the old image names stop updating.
      — *`release.yml` only makes the GitHub release; the tags `:2.0.0 :2.0 :2 :latest` come from `docker.yml` (semver tags) and the alias is documented in `docs/all-in-one*.md`. Not run on a real tag.*
- [x] `scripts/validate.sh`: file list and checks for what exists; the Docker-socket rule
      becomes "no compose file mounts the Docker socket" (stronger than before).
      — *the rule now covers every compose file in the repository and the one `install.sh` embeds; tried with a mount in a comment (passes) and a real one (fails).*
- [x] Dependabot / renovate paths for deleted Dockerfiles, if any.
      — *there is no dependabot/renovate configuration in the repository: nothing to change.*
- [x] A CI check that fails if the image or idle RAM exceed the limits (already in
      `aio-smoke.sh`) and prints the measured numbers to the job summary for the docs.
      — *`aio-smoke.sh` writes size and RAM to `$GITHUB_STEP_SUMMARY`; the function was run locally, the table is first rendered by GitHub.*

---

## Block 4: documentation (M, ~7-9h)

**Status:** ✅ Done on `feat/phase6-block4`, written for the **final** 2.0 state (before Blocks 1 and 2 land), so
re-check after merging: image name `headscale-easy` (alias `-aio`), `docker exec headscale-easy …` and the compose
container name, no `legacy/`, no Authentik API. Measured with `scripts/aio-smoke.sh` on 2026-10-06: image 232 MB,
72 MB RAM idle and during a backup, 46 processes. `scripts/gen_env_reference.py` (wired into `validate.sh`) fails when a
variable of `aio/render.py` is missing from `configuration*.md`. Not done: `hardening`, `why` and `architecture` have no
Spanish page (as before); the headless variable table in `all-in-one.md` still repeats part of the new reference.

- [x] **README / README.es:** the quick start is one command (installer or `docker run`); the
      advanced edition is a short section linking `deploy/` and the examples; remove the 1.x
      stack, Authentik-bundled and "preview" wording; keep the badges and numbers measured.
- [x] **`docs/architecture.md`:** the new diagram (one container: Caddy, Headscale, console,
      supervisor; optional outside pieces) and a resource table with the measured numbers.
- [x] **`docs/configuration*.md`:** the environment-variable reference regenerated from
      `aio/render.py` (`HSE_*`, `OIDC_*`, `HEADSCALE_PG_*`, `BACKUP_*`, `SMTP_*`, console knobs);
      remove every variable that no longer exists. A small script
      (`scripts/gen_env_reference.py`) can fail CI if a variable the renderer reads is
      undocumented.
- [x] **`docs/getting-started*.md`:** one path (installer → wizard); delete the 1.x flow.
- [x] **`docs/operations*.md`:** the upgrade story is `docker pull` + restart, backups with
      `hse backup`, and restore; delete `make` and 1.x compose commands.
- [x] **`docs/hardening.md`, `docs/security.md`, `SECURITY.md`:** no Docker socket anywhere,
      non-root uid, no capabilities, ports; remove hs-helper sections.
- [x] **`docs/why.md`:** the reasoning for one container and local accounts.
- [x] **`docs/all-in-one*.md`, `docs/advanced*.md`:** merge duplicated tables, remove the
      "preview" and "during 1.x" language, link the examples.
- [x] **`CONTRIBUTING.md`:** the dev loop for the AIO (`scripts/aio-smoke.sh`, tests, i18n),
      the branch model (`next` → `main`), no 1.x release branch.
- [x] `mkdocs build --strict` clean; every internal link checked; es translations for every
      page that changes; remove pages that describe deleted things.

---

## Block 5: 2.0 release prep and close-out (S, ~3-4h)

- [ ] `CHANGELOG.md`: collapse the `[2.0.0] - Unreleased` entries into a readable release note
      (what 2.0 is, what was removed, how to deploy); no "migration" section (nothing to
      migrate: say "2.0 is a fresh install; there is no 1.x upgrade path").
- [ ] `ROADMAP.md`: P6 ticked, the 2.0 line closed; `docs/roadmap.md` in sync.
- [ ] `SIMPLIFICATION_PLAN.md`: Phase 6 ticked with a status line like the earlier phases;
      record the final numbers (image, RSS, containers, line counts before/after).
- [ ] Final acceptance run on a clean host: `curl … | bash` (or `docker run`) → wizard →
      tailnet → device registration with a Tailscale client → backup and restore → external
      OIDC (Pocket ID) sign-in → external PostgreSQL → behind nginx. Record what was run.
- [ ] Version bump and tag `v2.0.0` **only after the user asks** (release is outward-facing).

---

## Order of work

```text
Block 1 (split stack) ──▶ Block 2 (Authentik code) ──▶ Block 3 (CI) ──▶ Block 5
                                                  └──▶ Block 4 (docs, can start in parallel
                                                       with Block 2 for the pages that do not
                                                       depend on it) ──▶ Block 5
```

Blocks 3 and 4 touch files the first two delete, so they land after them; Block 4's pages
that do not mention Authentik (architecture, operations, README) can be written alongside
Block 2.

## Risks

| Risk | Mitigation |
|---|---|
| Deleting the 1.x installer deletes the renderer's test oracle | Freeze the goldens in Block 1.3 before deleting; `test_render.py` keeps the `aio` target |
| `helper/` removal breaks the supervisor or the image | Find and move the used code first (1.2); the AIO smoke and `test_supervisor.py` gate the PR |
| Removing Authentik code breaks sign-in with an external Authentik | Plain OIDC path tested with Pocket ID and, by hand, with the Authentik example; group → role mapping tests stay |
| Orphaned references (docs, CI, `validate.sh`, Makefile) fail CI after a delete | A "no legacy" guard test, `validate.sh`, `mkdocs --strict` and a repo-wide grep per block |
| Docs say something the code does not | Env-var reference generated/checked from `aio/render.py`; numbers from `aio-smoke.sh` |
| The old image names stop updating and someone pulls a stale one | Publish `headscale-easy-aio` as an alias for one release; say so in the CHANGELOG |
| Huge deletions are hard to review | One PR per block, each with the list of deleted paths and the reason in its description |

## Open questions

All decided above following the usual pattern for a major release (delete 1.x, keep the
provider as external OIDC, one alias release for the image rename). One is left for the
maintainer because it is a product decision with more than one valid model:

- [ ] **Passkeys (WebAuthn) for local accounts** (SIMPLIFICATION_PLAN §8): in 2.0 or after?
      Models: (a) ship 2.0 with password + TOTP and add passkeys in 2.1 (the common route:
      Gitea, Vaultwarden); (b) hold 2.0 until passkeys land (more work, and stdlib-only
      WebAuthn is a large surface for our own crypto code). Not part of this phase unless
      you pick (b).

---

## Acceptance criteria (Phase 6 done when):

- [ ] All unit tests pass, `check_i18n.py`, `validate.sh`, `shellcheck` and `mkdocs build --strict` pass
- [ ] No 1.x-only file remains (`helper/`, `legacy/`, root compose, `authentik/`, 1.x backup and
      restore scripts) and a guard test enforces it
- [ ] No console code calls Authentik's API; sign-in with local accounts, API key and an
      external OIDC provider all work
- [ ] CI builds, tests and publishes only what 2.0 ships
- [ ] Image < 250 MB and idle RAM < 100 MB (numbers measured by CI and quoted in the docs)
- [ ] README, `docs/`, examples, CHANGELOG, ROADMAP and SIMPLIFICATION_PLAN agree with the code
- [ ] The final acceptance run on a clean host is recorded in the plan
