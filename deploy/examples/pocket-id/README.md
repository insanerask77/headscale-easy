# Headscale Easy with Pocket ID

[Pocket ID](https://pocket-id.org) is a small OIDC provider (a ~100 MB image, no database
server) that signs people in with passkeys. It is the lightest way to have real accounts, with
nothing to run besides it.

| File | What |
|---|---|
| [`docker-compose.yml`](docker-compose.yml) | a Caddy in front, Pocket ID, and the Headscale Easy image |
| [`Caddyfile`](Caddyfile) | the Caddy: `vpn.example.com` to the image, `id.example.com` to Pocket ID |
| [`.env.example`](.env.example) | every value |

Why a Caddy in front: passkeys need HTTPS and Pocket ID needs a domain of its own, and two
domains cannot both own port 443. The Caddy takes the HTTPS for both; the image runs with
`HSE_TLS=off` and trusts that Caddy only (`HSE_TRUSTED_PROXIES=172.30.0.10/32`, its fixed address
in the project's network). Inside the project both domain names lead to the Caddy (network
aliases), so Headscale and the console reach Pocket ID, their issuer, at its public address with
its real certificate.

## Steps

The order matters: Headscale refuses to start with an issuer that does not answer, and the OIDC
client's id and secret exist only after Pocket ID is up.

1. Point `vpn.example.com` and `id.example.com` at the host; ports 80 and 443 must reach it.
2. `cp .env.example .env`, set the two domains, `ACME_EMAIL`, and
   `POCKET_ID_ENCRYPTION_KEY` (`openssl rand -base64 32`; keep it, without it Pocket ID's data cannot
   be read).
3. `docker compose up -d proxy pocket-id`
4. Open `https://id.example.com/setup`, create the administrator and its passkey.
5. In Pocket ID: **Administration → OIDC Clients → Add**:

   | Field | Value |
   |---|---|
   | Name | Headscale Easy |
   | Callback URLs | `https://vpn.example.com/admin/callback` and `https://vpn.example.com/oidc/callback` |
   | Logout callback URL | `https://vpn.example.com/admin/` |
   | Public client | off |
   | PKCE | on |

   Copy the client id and the secret into `.env` as `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
6. **Administration → Application configuration**: turn on **Emails verified**. Without it Pocket ID
   sends `email_verified: false` and the console, correctly, ignores `PORTAL_ADMIN_EMAILS` (an address
   nobody verified never makes an administrator): everyone would be a member.
7. `docker compose up -d`, then open `https://vpn.example.com/admin/` and *Sign in with SSO*. The
   addresses in `PORTAL_ADMIN_EMAILS` are administrators.

Devices: `tailscale up --login-server https://vpn.example.com` opens Pocket ID's sign-in.

The menu and field names above are Pocket ID 1.16's; the settings themselves (the client's callback
URLs, `isPublic`, `pkceEnabled`, `emailsVerified`, `allowUserSignups`) were set and checked through its API,
not by clicking through the screens.

## Who can sign in

Headscale registers **whoever the provider lets in**. Restrict it with `HSE_OIDC_ALLOWED_DOMAINS` (the domain of the e-mail), `HSE_OIDC_ALLOWED_USERS` (addresses) or `HSE_OIDC_ALLOWED_GROUPS` (Headscale's `oidc.allowed_*`; comma-separated, empty = no restriction). Keep
Pocket ID closed: its **Allow user sign-ups** setting is *Disabled* by default, so only the people you
create or invite can sign in. Do not change it to *Open* on a server you do not want strangers to join.

## What was run

The compose file as it is, with a test variant for what needs a public domain: HTTP instead of HTTPS
(`http://vpn.test`, `http://id.test`, a Caddyfile with `auto_https off`), nothing published on host ports 80,
443 or 3478. Pocket ID 1.16.0, the steps above in that order (the administrator and the client through
Pocket ID's API, the same calls its pages make).

| Check | Result |
|---|---|
| steps 3 to 7 | the image starts and is healthy with Pocket ID's address as issuer |
| *Sign in with SSO* | redirects to Pocket ID with the client id, `redirect_uri=…/admin/callback`, PKCE `S256` |
| Pocket ID authorizes, the console's callback | the code is exchanged and a session is created |
| with **Emails verified** off | the user is a *member* (`/admin/users` and `/admin/backups`: 403) |
| with **Emails verified** on | the user in `PORTAL_ADMIN_EMAILS` is an administrator (200 on both) |

**Not run:** the passkey screens (the consent step was done through Pocket ID's API as the signed-in
administrator), HTTPS and Let's Encrypt, a real Tailscale client registering through Pocket ID.

## Limits

- **Roles by group** need Pocket ID to release the `groups` claim, which it does only when `groups` is
  requested: set `OIDC_SCOPE=openid profile email groups` (Headscale and the console both use it, and
  `PORTAL_ADMIN_GROUPS` names the group). Checked before this setting reached the console: with the console
  asking for `openid profile email` only, a `vpn-admins` group did not make the user an administrator. **Not
  re-checked end to end since**; if in doubt, `PORTAL_ADMIN_EMAILS` is the proven way.
- `https://id.example.com` must stay what Pocket ID was set up with (`APP_URL`): that is the issuer, and
  Headscale identifies users by it.
