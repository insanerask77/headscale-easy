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
  of the web UI's menu) and the sign-in mode (built-in Authentik, your own
  OIDC provider, or none).
- You will get an answer within a few days. A fix for a confirmed issue ships
  as a patch release; the advisory is published once the release is out.
- You will be credited in the release notes and the advisory if you want.

Vulnerabilities in Headscale, Authentik or Caddy themselves should go to those
projects; tell us too if Headscale Easy's configuration makes them worse.

## Supported versions

Only the latest release receives fixes.

## Status of the code

| | |
|---|---|
| Independent security audit | **None yet.** Reviews are very welcome — open a discussion or a private advisory. |
| Self-review | The sign-in, session, CSRF, role and ownership checks, the Docker socket use, input validation, redirects and static files were reviewed by the maintainer in September 2026; findings are listed in the [changelog](https://insanerask77.github.io/headscale-easy/changelog/). |
| Automated tests | Unit tests run in CI for every pull request, including permission boundaries (`tests/test_security.py`): forged or expired sessions, CSRF, admin-only pages and actions, members limited to their own machines and keys, path traversal, redirects, OIDC admin-by-email and demo mode. There are no end-to-end tests against a real Headscale in CI yet. |
| AI-assisted code | Most of the code was generated or modified with Claude Code and then reviewed, tested and integrated by the maintainer. Treat it like any code from a new contributor: read it. |

## What is exposed

With the default installation, one domain serves everything through Caddy:

| Component | Reachable from | Path / port | Authentication |
|---|---|---|---|
| Headscale control plane | Internet | `https://<domain>/` (TCP 443, 80 for redirects and Let's Encrypt) | Tailscale node keys; registration through OIDC or auth keys |
| Headscale REST API | Internet, through Caddy | `https://<domain>/api/v1/…` | Headscale API key (Bearer) |
| Embedded DERP relay | Internet | `https://<domain>/derp`, **UDP 3478** (STUN) | Tailscale node keys |
| Headscale Easy web UI | Internet | `https://<domain>/admin` | OIDC sign-in, or a Headscale API key if `PORTAL_API_KEY_LOGIN=true` or there is no OIDC |
| Authentik (optional) | Internet | `https://<domain>/authentik/…`, `/add-user` | Its own sign-in; admin interface for `akadmin` and `authentik Admins` |
| Headscale gRPC, metrics | Docker network only | — | — |
| Authentik's PostgreSQL | Docker network only | — | — |

Only Caddy publishes TCP ports (80/443, or only 80 behind your own proxy). If
the web UI or Authentik do not need to be public, restrict those paths to your
LAN or tailnet in Caddy or your front proxy — see the
[hardening guide](https://insanerask77.github.io/headscale-easy/hardening/#restrict-the-web-ui).

## Security model

- **One domain, one entry point.** Headscale's gRPC and metrics ports, the
  web UI's own port and Authentik are only reachable inside the Docker network.
- **Console sessions** are HMAC-SHA256 signed, `HttpOnly`, `SameSite=Lax`
  cookies (`Secure` over HTTPS) valid for 8 hours. Every form carries a CSRF
  token. A strict Content-Security-Policy blocks inline scripts, framing and
  third-party resources.
- **OIDC sign-in** uses the authorization code flow with PKCE and a signed,
  short-lived `state`. Identity comes from the provider's userinfo endpoint.
  Admins are members of `PORTAL_ADMIN_GROUPS`, or owners of an email in
  `PORTAL_ADMIN_EMAILS`; an email the provider marks as not verified
  (`email_verified: false`) never grants admin.
- **Narrower roles are opt-in and group-based.** A network admin
  (`PORTAL_NETWORK_ADMIN_GROUPS`) can edit the ACL policy and DNS and nothing
  else; an auditor (`PORTAL_AUDITOR_GROUPS`) sees everything an admin sees and
  can change nothing. Both are off while their variable is empty, neither can
  be granted by email, and admin takes priority. The default
  `MFA_REQUIRED=admins` covers the admin groups only: use `everyone` if these
  roles should need two-factor too.
- **Two-factor authentication** (authenticator app or passkey) is required for
  admins by default with the built-in Authentik (`MFA_REQUIRED`), and optional
  for members.
- **Members are isolated.** The console only shows a member their own machines
  and keys, and checks ownership on the server for every action. With
  `NETWORK_ISOLATION=true` the ACL policy also isolates them at the network
  level.
- **The Headscale API key** used by the console lives in `.env` (`chmod 600`)
  and expires (90 days by default). The console renews it 15 days before it
  expires, keeps the new one in `data/web/api-key` (`chmod 600`) and expires
  the old one.
- **The Docker socket.** Access to it is **equivalent to root on the host**,
  so the console does not mount it. Only `hs-helper` does: a small standard
  library service with no network that answers the console, over a `660` Unix
  socket, with exactly three fixed operations on the `headscale` container —
  validate the config (`headscale configtest`), restart it, and report
  container health and Headscale's version. It accepts no parameters (no query
  strings, no bodies, no container names), so a compromised console cannot
  use it to reach other containers or run commands. Only admins can reach the
  forms that use it. If you do not need DNS or key-expiry editing from the
  console, stop `hs-helper` (`docker compose stop hs-helper`): everything else
  keeps working. See
  [Hardening](https://insanerask77.github.io/headscale-easy/hardening/#the-docker-socket).
- **Headscale's database** is mounted read-only in the console (it reads each
  device's OS and client version, which the API does not expose).
- **The activity log** never stores secrets: auth keys and API keys are
  reduced to a prefix, invitation and password-reset links are not recorded.
- **Backups** (`./backups`) contain `.env` and Headscale's private keys: they
  are created readable only by the owner of the project files. The backup
  container runs as root (Headscale's keys are root-only) with only the
  capabilities to read files, change their owner and run its schedule.
- **Backups of the all-in-one image** (`/data/backups`) hold password hashes,
  two-factor secrets, the OIDC client secret and Headscale's private keys. The
  directory is `700` and each archive `600`, owned by the container's
  unprivileged user. Console sessions are never included (a restored session
  would revive revoked logins), administrators can download, upload or restore a backup
  from the console (POST with the CSRF token, names checked against the backups
  directory, a typed confirmation to restore, all audited; uploads are streamed to disk, never held in
  memory), and a restore checks the format and a SHA-256 of every
  file before it changes anything.
- **Secrets** (`.env`, generated configuration, backups) never belong in git;
  they are all in `.gitignore`.

## Known limitations

These are deliberate trade-offs today; they are documented so you can decide.

- **Sessions cannot be revoked one by one.** They are stateless signed
  cookies: signing out deletes the cookie, but a copied cookie stays valid
  until it expires (8 hours). Removing someone from the admin group takes
  effect at their next sign-in. To end every session at once, change
  `PORTAL_SESSION_SECRET` in `.env` and run `docker compose up -d`.
- **API key sign-in** (`PORTAL_API_KEY_LOGIN=true`, or no OIDC at all) gives an
  admin session to anyone holding a Headscale API key. Keep it off when you use
  OIDC, except as emergency access.
- **Sign-in attempts are not rate-limited** by the console beyond a one-second
  delay after a wrong API key. Authentik has its own protections; put the
  console behind your proxy's rate limiting or restrict it to your LAN if it is
  public.
- **The Docker socket** (see above) is held by `hs-helper`, not the console. A
  vulnerability in the console can only validate and restart Headscale through
  it; a vulnerability in `hs-helper` itself would still reach the host.
- **Headscale's REST API** is published on the same domain (Headscale serves it
  there). It needs an API key, but you can block `/api/` in your proxy if
  nothing outside the server uses it — the console talks to Headscale inside
  the Docker network.

## Demo instances

A public demo must never hold real data or credentials. Set `DEMO_MODE=true`
in `.env` and run `docker compose up -d`: every page shows a
"DEMO ENVIRONMENT" banner, and the console refuses the actions that grant
access or change things for everyone (auth keys, API keys, registering
devices, invitations, password reset links, users, DNS, saving the ACL policy,
two-factor, key expiry, removing or expiring machines). Also reset its data
regularly and put it behind your proxy's rate limiting. A demo shows the user
interface; it says nothing about how secure a real installation is.
