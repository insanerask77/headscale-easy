# Security

> ⚠️ **Headscale Easy is young, security-sensitive networking software.** It
> started in September 2026, is maintained by one person, and was written with
> extensive help from an AI coding assistant (see [AI usage](https://insanerask77.github.io/headscale-easy/ai-usage/)).
> It has **not had an independent security audit**. Review it, and harden your
> deployment (see the [hardening guide](https://insanerask77.github.io/headscale-easy/hardening/)), before you expose
> it to the Internet or trust it with a network that matters.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub Security Advisories](https://github.com/insanerask77/headscale-easy/security/advisories/new).

- Include what you found, how to reproduce it, the version (shown at the bottom
  of the console's menu) and the sign-in mode (local accounts, your own
  OIDC provider, or API key).
- You will get an answer within a few days. A fix for a confirmed issue ships
  as a patch release; the advisory is published once the release is out.
- You will be credited in the release notes and the advisory if you want.

Vulnerabilities in Headscale, Caddy or your identity provider themselves should go to those
projects; tell us too if Headscale Easy's configuration makes them worse.

## Supported versions

Only the latest release receives fixes.

## Status of the code

| | |
|---|---|
| Independent security audit | **None yet.** Reviews are very welcome — open a discussion or a private advisory. |
| Self-review | The sign-in, session, CSRF, role and ownership checks, the supervisor's socket, input validation, redirects and static files were reviewed by the maintainer in September 2026; findings are listed in the [changelog](https://insanerask77.github.io/headscale-easy/changelog/). |
| Automated tests | Unit tests run in CI for every pull request, including permission boundaries (`tests/test_security.py`): forged or expired sessions, CSRF, admin-only pages and actions, members limited to their own machines and keys, path traversal, redirects, OIDC admin-by-email and demo mode. There are no end-to-end tests against a real Headscale in CI yet. |
| AI-assisted code | Most of the code was generated or modified with Claude Code and then reviewed, tested and integrated by the maintainer. Treat it like any code from a new contributor: read it. |

## What is exposed

With the default installation, one domain serves everything through the container's Caddy:

| Component | Reachable from | Path / port | Authentication |
|---|---|---|---|
| Headscale control plane | Internet | `https://<domain>/` (TCP 443, 80 for redirects and Let's Encrypt) | Tailscale node keys; registration through the console's sign-in, an external OIDC provider or auth keys |
| Headscale REST API | Internet, through Caddy | `https://<domain>/api/v1/…` | Headscale API key (Bearer) |
| Embedded DERP relay | Internet | `https://<domain>/derp`, **UDP 3478** (STUN) | Tailscale node keys |
| Headscale Easy console | Internet | `https://<domain>/admin` | Local account (password, optional two-factor), an external OIDC provider, or a Headscale API key |
| External Authentik (optional) | Internet | `https://<domain>/authentik/…`, only if you set `HSE_AUTHENTIK_UPSTREAM` | Its own sign-in |
| Headscale gRPC, metrics, the console's own port | Inside the container only | — | — |

Only TCP 80/443 and UDP 3478 are published (only 80 behind your own proxy). If the
console does not need to be public, restrict `/admin` to your LAN or tailnet in a
proxy in front — see the
[hardening guide](https://insanerask77.github.io/headscale-easy/hardening/#restrict-the-console).

## Security model

- **One container, one entry point.** Headscale's gRPC and metrics ports and the
  console's own port are only reachable inside the container. The container runs as
  an unprivileged user (uid 1000) with no added capability and `no-new-privileges`.
  Its three processes share it: the data directory is private to that user
  (`700` / `600`).
- **Console sessions** are HMAC-SHA256 signed, `HttpOnly`, `SameSite=Lax`
  cookies (`Secure` over HTTPS) valid for 8 hours, and are kept server-side so they
  can be revoked. Every form carries a CSRF token. A strict Content-Security-Policy
  blocks inline scripts, framing and third-party resources.
- **Local accounts** store passwords as salted hashes (never the password), compare
  in constant time, and accept a second factor: TOTP with replay protection and
  hashed recovery codes. Invitation, reset and sign-up keys are shown once and stored
  hashed; a wrong, expired, used-up or revoked one gets the same error. Self-registered
  accounts are always members, never admins.
- **OIDC sign-in** (an external provider) uses the authorization code flow with PKCE
  and a signed, short-lived `state`. Identity comes from the provider's userinfo
  endpoint. Admins are members of `PORTAL_ADMIN_GROUPS`, or owners of an e-mail in
  `PORTAL_ADMIN_EMAILS`; an e-mail the provider marks as not verified
  (`email_verified: false`) never grants admin. `HSE_OIDC_ALLOWED_*` limits who may
  sign in at all.
- **Roles:** admin, network admin (the ACL policy and DNS, nothing else), auditor
  (sees everything, changes nothing) and member. With local accounts the role is stored
  per account; with an external provider the narrower ones are group-based, off while
  their variable is empty, never granted by e-mail, and admin takes priority. The
  default `MFA_REQUIRED=admins` covers admins only: use `everyone` if the other roles
  should need two-factor too.
- **Members are isolated.** The console only shows a member their own machines
  and keys, and checks ownership on the server for every action. With
  `NETWORK_ISOLATION=true` the ACL policy also isolates them at the network level.
- **Sign-in attempts are rate limited** per client IP (10 failures in 600 seconds,
  then `429` with `Retry-After`). Behind a proxy, set `HSE_TRUSTED_PROXIES` so the
  real address is counted, not the proxy's.
- **The Headscale API key** used by the console is created at first start and kept in
  `/data/console/api-key` (`600`). It expires (90 days) and the console renews it
  15 days before, and expires the old one.
- **No Docker socket.** Access to it is **equivalent to root on the host**, so
  nothing in Headscale Easy mounts it: not the console, not a helper container. A
  small supervisor inside the container validates the config (`headscale configtest`),
  restarts Headscale and reports the health of the three processes, and answers
  the console only over a Unix socket in the container with that fixed set of
  requests. A compromised console cannot use it to run other commands. See
  [Hardening](https://insanerask77.github.io/headscale-easy/hardening/#no-docker-socket).
- **Headscale's database** is read by the console (SQLite read-only, or PostgreSQL
  through a read-only role limited to three columns of one table), because it needs
  each device's OS and client version, which the API does not expose.
- **The activity log** never stores secrets: auth keys and API keys are
  reduced to a prefix, invitation and password-reset links are not recorded.
- **Backups** (`/data/backups`) hold password hashes, two-factor secrets, the OIDC
  client secret and Headscale's private keys. The directory is `700` and each archive
  `600`, owned by the container's unprivileged user. Console sessions are never
  included (a restored session would revive revoked logins). Administrators can
  download, upload or restore a backup from the console (POST with the CSRF token,
  names checked against the backups directory, a typed confirmation to restore, all
  audited; uploads are streamed to disk, never held in memory), and a restore checks
  the format and a SHA-256 of every file before it changes anything. The optional
  `backup-remote` sidecar sees only the backups, read-only.
- **Secrets** (`.env`, `settings.json`, backups) never belong in git.

## Known limitations

These are deliberate trade-offs today; they are documented so you can decide.

- **Three processes in one container.** A flaw in one reaches the others and the
  data. The supervisor restarts what crashes, but it is not a sandbox between them.
- **API key sign-in** gives an admin session to anyone holding a Headscale API key.
  It is on only while no OIDC provider is configured; keep it off when you use OIDC,
  except as emergency access.
- **The first-run wizard is served over plain HTTP** (no certificate exists yet) and is
  protected by a one-time token from the logs. The administrator's password travels
  unencrypted: run setup from a trusted network, or start headless with `HSE_PUBLIC_URL`.
- **Headscale's REST API** is published on the same domain (Headscale serves it
  there). It needs an API key, but you can block `/api/` in your proxy if
  nothing outside the server uses it — the console talks to Headscale inside the
  container.
