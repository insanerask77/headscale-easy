# Phase 2 Execution Plan — AIO Image + First-run Wizard

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 2.  
**Estimated effort:** L (several days)  
**Goal:** One container (`headscale-easy-aio`) running Headscale + Caddy + the
console under a small Python supervisor, configured by env vars or a browser
wizard on first run. No Docker socket, no `install.sh` needed.

**Done when:**
`docker run -d -p 80:80 -p 443:443 -p 3478:3478/udp -v hse:/data ghcr.io/insanerask77/headscale-easy-aio`
→ wizard → working tailnet, idle RAM < 100 MB, image < 250 MB.

---

## Decisions

- **Image name:** `headscale-easy-aio` during 1.x; it becomes `headscale-easy` in 2.0.
- **Wizard:** a separate process, `aio/wizard.py`, started by the supervisor in
  setup mode instead of the console. `web/app.py` keeps requiring `PUBLIC_URL`
  and `HEADSCALE_API_KEY` at import; its startup is not changed.
- The 1.x split compose is untouched (maintenance only).

## Findings that shape the design

- `web/headscale.py:40` and `web/app.py:74` read `HEADSCALE_API_KEY` /
  `PUBLIC_URL` with `os.environ[...]`, so the console can only start after setup.
- Console data paths are already env-configurable (`SESSIONS_DB`, `AUDIT_DB`,
  `API_KEY_FILE`, `MFA_MODE_FILE`, `HEADSCALE_DB`, `HEADSCALE_CONFIG`,
  `HELPER_SOCKET`), except `lac.configure("/data/console/accounts.db")`
  (`web/app.py:2115`) and `DISKS` in `web/status.py:28`.
- The helper client (`web/headscale.py:323-470`) speaks `/configtest`,
  `/restart`, `/status` over `HELPER_SOCKET`. The supervisor serves exactly that
  protocol, so the console does not change.
- `web/status_pages.py:35` renders `status["containers"]` (name, service, state,
  health): the supervisor returns one entry per process in the same shape.
- `install.sh` ends with an unguarded `main "$@"`, so it cannot be sourced.
  Golden files for render tests are generated once and committed.

---

## Pre-flight checks

Before starting:
- [ ] On `next` branch, CI passing
- [ ] Phase 0 and Phase 1 merged
- [ ] New branch per block, PR against `next` (main and next are protected)
- [ ] Confirm the Headscale binary path in the distroless image (`docker create` + `docker cp`)

---

## Block 1: `aio/render.py` — config renderer (D6) (M, ~6-8h) ✅ DONE

### 1.1 Port the generators
**Files:** `aio/render.py`

- [x] Python port of `dns_block`, `key_expiry_block`, `database_block`,
      `derp_paths_block`, `generate_headscale_config`, `generate_caddyfile`
      (`install.sh:919-1155`) over the existing `templates/*.tmpl`.
- [x] Minimal `envsubst` equivalent (only `${VAR}`).
- [x] Keep the marked blocks (dns, key expiry, derp map) of an existing config,
      including a `dns:` section without markers (reads `base_domain` / `magic_dns`).

### 1.2 Settings and targets
- [x] `load_settings()`: precedence **env > `/data/config/settings.json` > defaults**.
- [x] Env names: `HSE_PUBLIC_URL`, `HSE_TLS=auto|internal|off`, `ACME_EMAIL`,
      `HSE_DERP_PORT`, `HSE_ADMIN_EMAIL`, `OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`,
      `HEADSCALE_DB_TYPE` + `HEADSCALE_PG_*`, `UI_LANG`, `TZ`, `TAILNET_NAME`,
      `NODE_KEY_EXPIRY`, `DERP_USE_PUBLIC`, `NETWORK_ISOLATION`, plus today's console knobs.
- [x] `HSE_TLS` mapping: auto → letsencrypt, internal → selfsigned, off → `:80`.
- [x] Target `compose`: 1.x paths and upstreams; output must be **byte-identical** to `install.sh`.
- [x] Target `aio`: upstreams on `127.0.0.1`, paths under `/data`,
      `trusted_proxies: 127.0.0.1/32`, no Authentik route, `/register/<id>` route
      on unless Headscale uses OIDC, Caddy logs in `/data/caddy/logs`.
- [x] Outputs: `/data/config/{config.yaml,Caddyfile,derp.yaml}` and `console_env()`
      (env dict for the console: `SESSIONS_DB`, `AUDIT_DB`, … under `/data/console/`).
- [x] `render_setup_caddyfile()`: `:80` → only the wizard on `127.0.0.1:8000`.

### 1.3 Tests
**Files:** `tests/test_render.py`, `tests/fixtures/render/<case>/`

- [x] Golden files from `install.sh` for: sqlite + off, letsencrypt + OIDC,
      postgres + selfsigned, existing config with marked dns block.
- [x] Assertions for the `aio` target.
- [x] Precedence env / settings / defaults.
- [x] Reject values with newlines or quotes (parity with `validate_env_text`).

**Verify:** `python3 -m unittest tests.test_render`

---

## Block 2: `aio/supervisor.py` (M, ~6-8h) ✅ DONE

### 2.1 Process management
**Files:** `aio/supervisor.py`

- [x] Stdlib only, runs under `tini` as PID 1's child.
- [x] Start order: render → `headscale serve`, `caddy run --config /data/config/Caddyfile`,
      then the console (`python /app/web/app.py` with `console_env()`) **or** the
      wizard when there are no settings and no `HSE_PUBLIC_URL`.
- [x] Exponential backoff restart (1 s → 30 s, reset after 60 s healthy).
- [x] Log prefixes `[headscale]` / `[caddy]` / `[console]`.
- [x] Forward SIGTERM/SIGINT; ordered shutdown (console → caddy → headscale) with timeout.
- [x] The console waits for `headscale health` (replaces `depends_on: service_healthy`);
      API key read from `/data/console/api-key` (already renewed by `web/apikey.py`)
      and passed as `HEADSCALE_API_KEY`.

### 2.2 Helper protocol on a local socket
**Files:** `aio/supervisor.py`, `helper/helper.py`

- [x] Serve the helper protocol on `/run/hse/helper.sock` (mode 660), reusing
      `Handler` / `Server` / `serve()` from `helper/helper.py` (same contract:
      no query, no body, 404/405/400). Refactor the helper to accept a dict of
      backends instead of copying code.
- [x] `configtest` → `headscale configtest -c /data/config/config.yaml`.
- [x] `restart` → restart the child, wait for `headscale health`.
- [x] `status` → `{"api":1,"docker":true,"headscale":{...version},"containers":[one per process]}`
      (`docker: true` so `web/headscale.py:415` treats the backend as available).
- [x] `hse` control CLI: `hse reload` (re-render + restart Caddy/Headscale), `hse health`.

### 2.3 Tests
**Files:** `tests/test_supervisor.py`

- [x] Fake children (`python -c ...`): restart, backoff, signals, ordered stop.
- [x] Socket contract ported from `tests/test_docker_helper.py`
      (`HelperTest`, `WebClientTest`) against the supervisor.

**Verify:** `python3 -m unittest tests.test_supervisor tests.test_docker_helper`

---

## Block 3: `aio/Dockerfile` and `/data` layout (M, ~4-6h)

- [ ] Multi-stage: `FROM headscale/headscale:0.29.4` and a pinned
      `FROM caddy:<x.y.z>-alpine` → `python:3.13-alpine` + `tini` + `web/` + `aio/` +
      `templates/`. OCI labels as in `web/Dockerfile`.
- [ ] `/data` layout: `headscale/` (db, keys, socket), `caddy/` (certs, logs),
      `console/` (accounts/sessions/audit DBs, api-key, mfa-required),
      `config/` (settings.json, config.yaml, Caddyfile, derp.yaml, setup-token),
      `backups/`. The supervisor creates them (700 dirs / 600 files).
- [ ] Non-root (uid 1000). Ports 80/443 without capabilities: Docker ≥ 20.10 sets
      `net.ipv4.ip_unprivileged_port_start=0`; document `--sysctl` for other runtimes.
- [ ] Verify whether Headscale really needs `NET_ADMIN` (today's compose adds it)
      and document the result.
- [ ] `HEALTHCHECK` via `hse health`: healthy when the three processes run
      (setup mode: wizard + caddy).
- [x] `web/app.py`: `lac.configure(os.environ.get("ACCOUNTS_DB", "/data/console/accounts.db"))`.
- [ ] `web/status.py`: configurable disks by env.

**Verify:** `docker build -f aio/Dockerfile -t hse-aio .` and `docker image inspect` < 250 MB.

---

## Block 4: Setup mode and `aio/wizard.py` (L, ~10-14h)

### 4.1 Setup mode and token
**Files:** `aio/wizard.py`, `aio/supervisor.py`

- [ ] Active when `/data/config/settings.json` is missing and `HSE_PUBLIC_URL` is unset.
- [ ] One-time token (`secrets.token_urlsafe(24)`) in `/data/config/setup-token`
      (600) and printed in the logs in a banner; checked with `hmac.compare_digest`;
      simple rate limit.
- [ ] The wizard only answers `/admin/setup*` and static files; everything else → 302.
- [ ] With `HSE_PUBLIC_URL` set: no wizard, direct start (headless); admin via
      `HSE_ADMIN_EMAIL` (+ `HSE_ADMIN_PASSWORD`) with the existing `bootstrap_admin()`
      (`web/app.py:2053`).

### 4.2 Wizard steps
Reuse `web/static`, `web/i18n.py`, `web/locales`, the TOTP code and `web/qr.py` from Phase 1.

- [ ] Token → language → public URL → TLS (auto/internal/off, ACME email if auto) →
      admin account (`lac.create_account`) + TOTP (confirmation code required) →
      tailnet name / base domain and per-user isolation → backups
      (only stores `BACKUP_SCHEDULE` / `BACKUP_KEEP_DAYS` for Phase 3).
- [ ] CSRF on every POST.

### 4.3 Finish
- [ ] Write `settings.json` (600) and a generated `SESSION_SECRET`.
- [ ] Render config, start Headscale, create the API key
      (`headscale apikeys create --expiration 90d` → `/data/console/api-key`),
      create the admin's Headscale user (`headscale users create`), apply the
      isolation policy if none exists (`headscale policy set -f /dev/stdin`, same
      JSON as `apply_network_policy`).
- [ ] Delete the setup token, stop the wizard, start the console, reload Caddy with the final Caddyfile, show a final page linking to the public URL.
- [ ] Idempotent / resumable: on a mid-way failure (e.g. ACME) show the error and
      allow retry without duplicating account, user or API key.
- [ ] New strings in all 6 locales (`python scripts/check_i18n.py` passes).

### 4.4 Tests
**Files:** `tests/test_wizard.py`, `tests/test_security.py`

- [ ] No token → 403; reused token → 403; CSRF enforced.
- [ ] URL / email validation.
- [ ] Full flow with a fake Headscale (mocked commands): settings.json, api-key,
      admin account with TOTP.
- [ ] No wizard when `HSE_PUBLIC_URL` is set.
- [ ] Setup takeover regression tests in `tests/test_security.py`.

**Verify:** `python3 -m unittest tests.test_wizard tests.test_security`

---

## Block 5: CI and publishing (S/M, ~3-4h)

**Files:** `.github/workflows/{ci,docker,docker-dev}.yml`

- [ ] `ci.yml`: add `aio/*.py` to `py_compile`; new `aio` job building `aio/Dockerfile`.
- [ ] Headless smoke test (`HSE_PUBLIC_URL=http://localhost HSE_TLS=off HSE_ADMIN_EMAIL=… HSE_ADMIN_PASSWORD=…`):
      healthy, `/healthz`, `/admin/healthz`, create a pre-auth key.
- [ ] Wizard smoke test (no env): token in the logs, `/admin/setup` answers.
- [ ] `docker stats --no-stream` after 60 s and `docker image inspect`:
      **fail above 100 MB RAM or 250 MB image**.
- [ ] `docker.yml` / `docker-dev.yml`: publish `ghcr.io/insanerask77/headscale-easy-aio`
      (context `.`, file `aio/Dockerfile`).
- [ ] Optional: E2E of the Phase 0 device flow with a Tailscale client container
      against the AIO image.

---

## Block 6: Docs and close-out (S, ~2h)

- [ ] `docs/` page and a short README section "Try the all-in-one image (preview)".
- [ ] `CHANGELOG.md` entry under [2.0.0] - Unreleased.
- [ ] Tick Phase 2 in `SIMPLIFICATION_PLAN.md` and P2 in `ROADMAP.md`; resolve the
      image-name open question in §8.

---

## Acceptance criteria (Phase 2 done when):

- [ ] All unit tests pass, `check_i18n.py` passes
- [ ] `docker run -d -p 80:80 -p 443:443 -p 3478:3478/udp -v hse:/data <image>`
      → wizard → working tailnet
- [ ] Headless start with env vars works (no wizard)
- [ ] Wizard cannot be taken over without the log token
- [ ] Device registration flow (Phase 0) works against the AIO image
- [ ] Killing a child process: the supervisor restarts it; `docker stop` < 10 s
- [ ] DNS edit in the console → configtest + restart through the supervisor socket
- [ ] Idle RAM < 100 MB, image < 250 MB (enforced in CI)
- [ ] No Docker socket anywhere; container runs as non-root
- [ ] 1.x compose and `install.sh` unchanged and still working
- [ ] CHANGELOG, ROADMAP and docs updated

---

## Files created/modified (checklist):

### New files:
- [ ] `aio/Dockerfile`
- [x] `aio/supervisor.py`
- [x] `aio/render.py`
- [ ] `aio/wizard.py`
- [x] `aio/hse` (CLI: `reload`, `health`)
- [ ] `tests/test_render.py` ✅, `tests/test_supervisor.py` ✅, `tests/test_wizard.py`
- [x] `tests/fixtures/render/*` (+ `scripts/gen_render_goldens.sh`)

### Modified files:
- [x] `helper/helper.py` (reusable handler with pluggable backends)
- [x] `web/app.py` (`ACCOUNTS_DB`)
- [ ] `web/status.py` (configurable disks)
- [ ] `web/locales/*` (wizard strings)
- [ ] `tests/test_security.py`
- [ ] `.github/workflows/ci.yml`, `docker.yml`, `docker-dev.yml`
- [ ] `docs/`, `README.md`, `README.es.md`, `CHANGELOG.md`, `ROADMAP.md`, `SIMPLIFICATION_PLAN.md`

---

## Estimated timeline:

| Block | Effort | Cumulative |
|-------|--------|------------|
| 1. Renderer | 6-8h | 8h |
| 2. Supervisor | 6-8h | 16h |
| 3. Dockerfile + layout | 4-6h | 22h |
| 4. Wizard | 10-14h | 36h |
| 5. CI + publishing | 3-4h | 40h |
| 6. Docs | 2h | 42h |
| **Total** | **~42h** | **~5-6 days** |

---

## Verification (end to end)

1. `python -m unittest discover -s tests -v` and `python scripts/check_i18n.py`.
2. `docker build -f aio/Dockerfile -t hse-aio .` → image < 250 MB.
3. Headless: `docker run -d -p 80:80 -p 3478:3478/udp -e HSE_PUBLIC_URL=http://localhost -e HSE_TLS=off -e HSE_ADMIN_EMAIL=admin@example.com -e HSE_ADMIN_PASSWORD=<pw> -v hse:/data hse-aio`
   → healthy, `curl localhost/healthz`, `curl localhost/admin/healthz`, sign in.
4. Wizard: same `docker run` without env → token in `docker logs` → complete the
   wizard in a browser → console with local sign-in + TOTP.
5. Phase 0 flow with a Tailscale client container: `tailscale up --login-server http://<host>`
   → `/register/<id>` → approve → node in Machines. `pkill headscale` → restarted.
6. Settings → DNS in the console → configtest + restart via the supervisor socket.
7. `docker stats` after 60 s idle < 100 MB.

---

## Notes for execution:

1. **Work in order:** blocks depend on the previous one (render → supervisor → image → wizard).
2. **Verify at every step:** run tests after each subtask; one commit per subtask.
3. **Never break 1.x:** `install.sh`, the split compose and the Authentik integration stay as they are.
4. **Test fixtures:** low-entropy fake keys/IDs only (GitGuardian).
5. **Test on the live stack by exact name,** never by list position.
6. **Phase 3 builds on this:** the scheduler goes into the supervisor; `hse backup` / `hse restore` extend the `hse` CLI.
