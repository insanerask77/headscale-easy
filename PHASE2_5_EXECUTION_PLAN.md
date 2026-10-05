# Phase 2.5 Execution Plan — Findings from testing the AIO image

**Status:** ⏳ Not started.

Detailed implementation plan for SIMPLIFICATION_PLAN.md Phase 2.5.  
**Estimated effort:** M-L (several days)  
**Goal:** Fix what showed up when trying the `:next` AIO image by hand, **before**
phase 3 (built-in backups) starts.

**Done when:**
- the console's language selector works;
- a fresh install with no DERP settings runs on the container's own DERP/STUN;
- the wizard has a DERP step and an open-registration step;
- users created in the console get a username and password;
- sign-up from the main page works in its three modes (open / invitation key / off);
- the tailnet's base domain is configurable (default derived from "Headscale Easy");
- a device appears or disappears in the UI within seconds, without refreshing;
- **Add device** has a **Docker** tab that brings up Tailscale as a container
  on this tailnet.

---

## Decisions

- **Branch/PR flow:** one branch per block, PR against `next` (as in phase 2).
- **Sign-up default is `off`.** Nobody can register unless the admin turns it on.
- **Live updates:** WebSocket if it fits the stdlib-only budget; otherwise
  Server-Sent Events (SSE). Block 5 starts with a short spike to decide. The
  nodes page must keep working (manual refresh) if the channel drops.
- **Embedded DERP is the default** when the admin does not configure DERP.
- **Tailnet naming default:** slug of "Headscale Easy" (`headscale-easy`),
  shown in the form so it is never a surprise. Exact slug confirmed in block 4.
- Everything new that has a wizard field also has an `HSE_*` env var (headless
  start) and appears in `docs/all-in-one.md` / `.es.md`.
- No new dependencies in `web/` or `aio/` unless a block says otherwise.

## Where things are today (starting points, verify before editing)

- Wizard: `aio/wizard.py`, `STEPS = ("language", "server", "admin", "network",
  "backups", "finish")`; `check_network()` validates the tailnet name; the
  network page says devices get `device.<name>.headscale.net`.
- Rendering: `aio/render.py` (`derp_paths_block`, DERP settings
  `HSE_DERP_PORT`, `DERP_USE_PUBLIC`; `dns_block` writes `base_domain`).
- Console language switch: `web/ui.py` (a `POST {BASE}/settings/language`
  form with one submit button per language), strings in `web/i18n.py` and
  `web/locales/`.
- Local accounts: `web/local_accounts.py` (`create_account`,
  `update_password`, e-mail based invitations and reset tokens, TOTP).
- DERP console pages: `web/derp.py`, `web/derp_pages.py`.
- Nodes: `web/app.py` (`to_machines`, `visible_nodes`), Headscale client in
  `web/headscale.py`, front-end JS in `web/static/app.js`.

---

## Pre-flight checks

- [ ] On `next`, CI passing, working tree clean
- [ ] Phase 2 merged (it is)
- [ ] Reproduce each reported issue on the running `hse-aio` image and note
      the exact steps in the PR (language selector, DNS name, no live updates)
- [ ] Confirm what a fresh Headscale does with `derp.server.enabled` and
      whether the AIO container can expose STUN 3478/udp without extra flags

---

## Block 1: Language selector in the console (S, ~2-3h)

### 1.1 Reproduce and fix

> **Root cause:** with five languages the `.lang-switch` flex row in the user menu
> overflowed the popover; Deutsch/Português sat outside it, so the click hit the
> page behind. The handler, cookie and `pick_lang` were fine. Fixed with a grid.
**Files:** `web/ui.py`, `web/i18n.py`, `web/app.py` (the `/settings/language`
handler), `web/static/app.js`

- [x] Reproduce in a real browser against `hse-aio` (the wizard's selector is
      fine; only the console's is broken): which language, which page, what
      happens (no change, wrong language, error, layout)
- [x] Find the root cause (handler, cookie/session storage, `get_lang()`
      resolution order, redirect target, CSS of `.lang-switch`)
- [x] Fix it

### 1.2 Regression test
**Files:** `tests/test_languages.py`

- [x] Test that posting each supported language changes the rendered page and
      survives the next request
- [x] `python3 scripts/check_i18n.py` stays green

**Done when:** every language can be selected from any console page and sticks.

---

## Block 2: Embedded DERP by default + DERP step in the wizard (M, ~6-8h)

### 2.1 Settings model
**Files:** `aio/render.py`, `aio/wizard.py`

- [x] New setting `derp_mode`: `embedded` (default) | `public` | `custom`
      (env `HSE_DERP_MODE`)
  - `embedded`: Headscale's embedded DERP + STUN on `HSE_DERP_PORT` (3478/udp);
    `derp.urls: []`, no third-party relay
  - `public`: Tailscale's public DERP map (today's `DERP_USE_PUBLIC=true`)
  - `custom`: a URL and/or an uploaded `derp.yaml`
- [x] When nothing is configured (no setting, no env) the result is
      `embedded`. Keep today's `DERP_USE_PUBLIC` working as an alias
- [x] Render the config for each mode; golden-file tests in `tests/test_render.py`
- [x] Make sure `server_url` / the DERP region hostname is the public URL's
      host, otherwise clients cannot reach the embedded DERP

### 2.2 Wizard step
**Files:** `aio/wizard.py`, `web/locales/*`

- [x] New step `derp` (after `network`): three radio options with a one-line
      explanation each; `embedded` preselected; `custom` shows the URL field
- [x] Add it to `STEPS`, the step dots and the final summary
- [x] Validation (URL scheme, reachable-format check only; no outbound calls)
- [x] Tests in `tests/test_wizard.py`

### 2.3 Console and docs
**Files:** `web/derp_pages.py`, `docs/all-in-one.md`, `docs/all-in-one.es.md`

- [x] The existing DERP page shows the active mode and does not offer to
      "fix" an embedded setup
- [x] Document the mode, the env var and the 3478/udp requirement

### 2.4 Verify
- [x] (verified on the AIO image: `urls: []`, STUN answers on 3478/udp) Start with no DERP settings → `headscale` config has the embedded DERP,
      the container answers STUN on 3478/udp, a Tailscale client connects
      through it (extends the smoke test: assert the rendered config)

**Done when:** a fresh install with no DERP choices runs entirely on the
container's own DERP, and the wizard lets you pick another mode.

---

## Block 3: Credentials for new users + self-registration (L, ~10-14h)

### 3.1 Create users with username and password
**Files:** `web/app.py` (create-user form/handler), `web/admin_pages.py`,
`web/local_accounts.py`

- [x] Create-user form gets `username` and `password` (or "send an invitation
      / set a temporary password" choice) and `role`
- [x] Reuse `create_account` and the existing password rules; create the
      matching Headscale user
- [x] "Set / reset password" action on an existing user
      (`update_password`, invalidates their sessions)
- [x] Decide with the owner whether a temporary password forces a change on
      first sign-in (recommended: yes)
- [x] Tests in `tests/test_local_accounts.py`, `test_local_accounts_e2e.py`,
      `test_security.py` (no password in logs/audit, role checks, only admins)

### 3.2 Sign-up modes
**Files:** `web/local_accounts.py`, `web/app.py`, `web/pages.py` (login page),
`aio/render.py` / settings

- [x] Setting `signup_mode`: `off` (default) | `invite` | `open`
      (env `HSE_SIGNUP`)
- [x] `off`: no link on the login page and `/signup` answers 404
- [x] `open`: sign-up form (username, email, password); regular user only,
      never admin
- [x] `invite`: the same form plus an **invitation key**. Keys are created
      and revoked in the console (Users → Invitation keys), single or
      multi use, optional expiry, stored **hashed** (reuse the token helpers
      `_hash_token`/`verify_token`), shown once on creation
- [x] Wrong, expired or used-up key → the same generic error
- [x] Rate limit the endpoint (reuse the login limiter), CSRF on the form
- [x] Sign-up link on the main/login page according to the mode
- [x] Audit entries for sign-ups and key create/revoke

### 3.3 Wizard step
**Files:** `aio/wizard.py`

- [x] New step `signup` (after `admin`): off / restricted / open
- [x] Choosing *restricted* offers to create the first invitation key and
      shows it once on the finish page
- [x] Settings page gets the same control

### 3.4 Tests
- [x] `tests/test_security.py`: `off` returns 404, `invite` rejects missing /
      wrong / expired / revoked keys, `open` never creates an admin, rate
      limit kicks in, keys are never stored in plain text

> **Decisions (owner OK):** a temporary password forces a change at first sign-in;
> keys are single or multi use; no e-mail verification. Create-user without a
> password still makes a Headscale-only user (for servers).

**Done when:** an admin can create users with credentials, and people can
register from the login page according to the chosen mode, set from the
wizard or Settings.

---

## Block 4: Configurable tailnet base domain (S-M, ~3-4h)

### 4.1 Setting and validation
**Files:** `aio/render.py`, `aio/wizard.py`, `web/locales/*`

- [x] New setting `base_domain` (env `HSE_BASE_DOMAIN`). Today it is derived
      as `<tailnet>.headscale.net`
- [x] Default when empty: derived from "Headscale Easy"
      (`headscale-easy.<suffix>`); settle the exact value, and check that
      Headscale accepts it (must be a valid DNS name, different from the
      server's own domain)
- [x] Validate as a DNS name (labels, length) with a clear error message
- [x] Keep `render.py`'s rule that an existing value edited in the DNS page
      is not overwritten on re-render

### 4.2 Wizard
- [x] On the network step: editable "MagicDNS base domain" field, prefilled
      with the default, with the example line updated
      (`device.<base domain>`)
- [x] Tests in `tests/test_wizard.py`, `tests/test_render.py`, `tests/test_dns.py`

> **Decision (pending owner OK):** default `hse.net` (confirmed by the owner; editable in the wizard and on the console DNS page), AIO only; the compose
> install keeps `<tailnet>.headscale.net`. Rejected: no dots, bad labels, the
> server's own domain or a parent of it.

**Done when:** the wizard shows and lets you change the base domain; empty
means the Headscale Easy default; the DNS page still edits it later.

---

## Block 5: Live device status (M, ~6-8h)

### 5.1 Spike: transport (S, ~1h)
- [x] Check what the stdlib/WSGI-or-HTTP server in `web/app.py` supports.
      WebSocket by hand (RFC 6455 handshake + frames) vs SSE. Pick SSE if
      WebSocket would mean new dependencies or a lot of protocol code; record
      the decision in this file

> **Spike decision: SSE.** `ThreadingHTTPServer` already gives one thread per
> connection, so a stream needs no dependency. WebSocket by hand (handshake,
> framing, masking, ping/pong) is a lot of protocol for a one-way feed. Each
> message carries only `type`, `id`, `name`, `online`; the page then re-fetches
> its own, already filtered, HTML and patches the rows (the existing `data-live`
> machinery), so rendering stays in one place. Without the stream the page falls
> back to polling every 5 s as before.

### 5.2 Event source (server)
**Files:** `web/headscale.py`, `web/app.py`

- [x] A single background poller (not one per client) lists nodes every few
      seconds and diffs them: `online`, `offline`, `added`, `removed`, `renamed`
- [x] Fan out to subscribers with per-user filtering (reuse `visible_nodes`
      so members only see their own devices)
- [x] Endpoint behind the session + CSRF/Origin checks; limit connections per
      session; heartbeat to detect dead clients; clean shutdown with the
      supervisor's SIGTERM
- [x] Back off and keep going when Headscale is restarting

### 5.3 Front-end
**Files:** `web/static/app.js`, `web/pages.py`

- [x] The machines list subscribes and patches rows in place (status dot,
      last seen, new/removed rows) without a reload
- [x] Reconnect with backoff; visible "live"/"reconnecting" indicator; fall
      back to the current manual refresh
- [x] Respect `prefers-reduced-motion` for any highlight animation

### 5.4 Tests
- [x] Unit tests for the diff logic and the per-user filtering with a fake
      Headscale; auth tests for the endpoint (no session → 401/403)
- [x] Manual check on the AIO image with a real `tailscale/tailscale` container: `added` and
      `online` within ~2 s of `docker run`, `offline` after `docker stop`; stream works through Caddy

**Done when:** a device connecting or disconnecting shows up in the UI within
seconds with no page refresh.

---

## Block 6: "Docker" section in Add device (S-M, ~4-5h)

### 6.1 New tab
**Files:** `web/pages.py` (`add_page`: the `panels` dict and tab bar),
`web/app.py` (pre-auth key creation), `web/locales/*`

- [x] New **Docker** tab next to Linux / Windows / macOS / iOS / Android, with
      the same look (steps + copyable `code()` blocks)
- [x] Two ready-to-copy snippets pointing at this server's `public_url`:
      a `docker run` command and a `docker-compose.yml`, both using the official
      `tailscale/tailscale` image, `TS_AUTHKEY`, `TS_EXTRA_ARGS=--login-server=<url>`,
      `TS_STATE_DIR=/var/lib/tailscale` on a named volume, `TS_HOSTNAME`,
      `/dev/net/tun` + `NET_ADMIN` (or `--cap-add` variants), and
      `restart: unless-stopped`
- [x] A small form above the snippets: hostname, optional extras (advertise as
      **exit node**, **subnet routes** `--advertise-routes`, userspace
      networking without `/dev/net/tun`, accept DNS) that rewrite the snippet
- [x] Auth key: a **"Generate key"** button creates a pre-auth key (reusing
      the Settings → Keys code; expiry short by default, single use, tagged to
      the signed-in user) and fills it into the snippet; shown once, never
      stored in the page. Without it the snippet keeps `<auth-key>` and links
      to Settings → Keys, like the headless-devices card
- [x] Alternative without a key: the container prints a login URL in its logs
      (`docker logs`); show that as step 3 for interactive sign-in
- [x] Works for members too (their own user), admins can pick the user

### 6.2 Safety and tests
- [x] Escape everything that goes into the snippets (hostname, routes) and
      validate them (hostname label, CIDR list); no shell injection when a
      user copies the command
- [x] Key generation requires the same permissions and CSRF as Settings → Keys
- [x] Tests: the tab renders with the right `login-server`, options change the
      snippet, invalid hostname/routes are rejected, member vs admin
- [x] Manual check on `hse-aio`: run the snippet, the container appears in
      the machines list (also exercises Block 5)

### 6.3 Docs
- [x] Mention the Docker tab in `docs/all-in-one.md` / `.es.md`

**Done when:** from Add device → Docker an admin or member can copy a command
or compose file that starts a Tailscale container registered on this tailnet.

---

## Block 7: Docs, i18n, release notes (S, ~2h)

- [ ] `docs/all-in-one.md` / `.es.md`: DERP modes, sign-up modes, base
      domain, Docker tab, new env vars (`HSE_DERP_MODE`, `HSE_SIGNUP`, `HSE_BASE_DOMAIN`)
- [ ] Every new string translated; `scripts/check_i18n.py` green
- [ ] `CHANGELOG.md` entry (unreleased); mark Phase 2.5 ✅ in
      `SIMPLIFICATION_PLAN.md` and set the status line here
- [ ] `scripts/aio-smoke.sh` extended: embedded DERP present in the config,
      sign-up `off` → 404, image/RAM limits still respected

---

## Order

```text
Block 1 (language) ─┐
Block 4 (domain)  ──┼──▶ Block 2 (DERP) ──▶ Block 3 (users/sign-up) ──▶ Block 5 (live) ──▶ Block 6 (docker tab) ──▶ Block 7
                    └─ independent, can go in parallel
```

Blocks 1 and 4 are small and independent. Blocks 2, 3 and 4 all add wizard
steps, so merge them one at a time to avoid conflicts in `aio/wizard.py`
(`STEPS` and the finish summary). Block 5 is independent of the wizard.
Block 6 needs nothing from the others (the pre-auth key code already exists),
but testing it is nicer once Block 5 shows the new device live.

## Risks

| Risk | Mitigation |
|---|---|
| Embedded DERP unreachable behind NAT / wrong hostname | Render from the public URL's host; document 3478/udp; check with a real client |
| Open sign-up gets abused | Default `off`; rate limit; regular role only; audit log; invitation mode |
| Invitation keys leak | Stored hashed, shown once, expiry and revoke |
| WebSocket by hand has protocol bugs | Spike first; prefer SSE if in doubt |
| Poller loads Headscale | One poller shared by all clients, short but fixed interval |
| Changing `base_domain` on a live tailnet renames devices | Warn in the DNS page; the wizard only sets it at first run |

## Open questions

- [ ] Temporary password forced to change on first sign-in? (recommended: yes)
- [ ] Final default slug for the base domain (`headscale-easy`?) and its suffix
- [ ] Invitation keys: single use, multi use, or both? (plan assumes both)
- [ ] Should sign-up require e-mail verification? (no mailer in the AIO image;
      plan assumes no)
