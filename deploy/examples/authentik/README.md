# Headscale Easy with Authentik

The all-in-one image next to an [Authentik](https://goauthentik.io) of its own, used as an
external OIDC provider: people sign in to the console and register their devices with their
Authentik account (passwords, Authentik's own two-factor, "Sign in with Google").

Authentik only signs people in. The console does **not** call Authentik's API: invitations,
password resets and the console's two-factor belong to its local accounts, as with any other
provider (Keycloak, Pocket ID, Google).

| File | What |
|---|---|
| [`docker-compose.yml`](docker-compose.yml) | the image, Authentik (server, worker, PostgreSQL) |
| [`.env.example`](.env.example) | every value, and how to generate the secrets |
| [`blueprints/headscale.yaml`](blueprints/headscale.yaml) | what Authentik is set up with on its first start |
| [`branding/`](branding/) | the theme the blueprint uses |

## How it fits together

Authentik is served at `https://<your domain>/authentik/`, **through the image's own
Caddy** (`HSE_AUTHENTIK_UPSTREAM=authentik-server:9000`). The issuer URL is therefore

```
https://vpn.example.com/authentik/application/o/headscale/
```

That URL is not a detail: **Headscale identifies every OIDC user by their issuer plus
their subject**. Move Authentik to another address and every user stops matching the
account Headscale has for them. Keep it.

One OIDC client (`OIDC_CLIENT_ID`, default `headscale`) serves Headscale (`tailscale up`)
and the console. The `headscale` provider in the blueprint accepts the console's addresses
too:

| Address | Used by |
|---|---|
| `https://<domain>/oidc/callback` | Headscale |
| `https://<domain>/admin/callback` | the console (`REDIRECT_URI` in `web/app.py`) |
| `https://<domain>/admin/` (logout) | the console |

## A new install

1. Point `vpn.example.com` at the host; ports 80 and 443 must reach it.
2. `cp .env.example .env`, set `HSE_PUBLIC_URL`, `HSE_DOMAIN`, `ACME_EMAIL`, and generate
   every secret the file lists (`openssl rand -base64 36 | tr -d '/+=' | cut -c1-40`).
3. `docker compose up -d`.

The first start takes **several minutes**: Authentik creates its database and applies the
blueprint. Meanwhile the image keeps restarting Headscale (it refuses to start until the
issuer answers, by design) and the container shows `unhealthy`; both settle on their own.
Watch with `docker compose logs -f`.

Then:

- **Authentik**: `https://vpn.example.com/authentik/` as `akadmin` with
  `AUTHENTIK_BOOTSTRAP_PASSWORD`. The blueprint created the groups `headscale-users` (may
  join the tailnet) and `vpn-admins` (also administer the console); add people to them.
  Create their accounts in Authentik (Directory → Users).
- **The console**: `https://vpn.example.com/admin/` and *Sign in with SSO*. Members of
  `vpn-admins` are administrators; so are the addresses in `PORTAL_ADMIN_EMAILS`.

### Two-factor

The sign-in flow uses Authentik's own optional stage: it asks for a code only when the user
already has a second factor, which each person sets up in their Authentik settings. To make it
mandatory, bind a policy to that stage in Authentik. The console's own two-factor
(`MFA_REQUIRED`) applies to its local accounts, not to people who sign in through Authentik.

### Behind a proxy

Set `HSE_TLS=off`, `HSE_TRUSTED_PROXIES=<the proxy>/32` and `HSE_SELF_IP=<the proxy's address>`
(see [`../front-proxy`](../front-proxy)). `HSE_SELF_IP` is what makes the image's own public
name lead to the proxy instead of to itself (`extra_hosts`): Headscale and the console fetch the
issuer's configuration at that name, and need a certificate they trust.

## What was run

On an earlier version of this example (before the console stopped calling Authentik's API), on
one host, with the compose file as it is (only the address, the ports and the secrets set; HTTP
instead of HTTPS because there was no public domain):

| Check | Result |
|---|---|
| `docker compose up -d`, Authentik 2026.8.3 first start | migrations and blueprint applied, several minutes on a slow machine (the first attempt, with `depends_on: service_healthy`, failed: that is why the image only waits for Authentik to *start*) |
| the issuer's discovery document through the image's Caddy | `issuer: http://vpn.test/authentik/application/o/headscale/` |
| Headscale, after Authentik answers | starts by itself (it had logged `503 authentik starting` meanwhile); `/key` answers |
| console: *Sign in with SSO* | redirects to Authentik's authorize endpoint with `client_id=headscale` and `redirect_uri=…/admin/callback` |
| Authentik and the three addresses | `/admin/callback` and `/oidc/callback`: accepted, go on to the sign-in flow; an address that is not registered: refused |

**Not run, and not re-run on the trimmed blueprint** (the blueprint lost its add-user,
invitation, password-reset and two-factor-policy parts and the console's service account):
a person signing in through Authentik's own screens and landing in the console, Authentik's
groups giving the admin role, Google as a source, and HTTPS with Let's Encrypt (the loopback
`extra_hosts` trick relies on Caddy serving a certificate Headscale trusts). The blueprint
still parses and every reference in it resolves, but it was not applied to a live Authentik
after the cut.

## Limits

- Roles by group: the default admin group is `vpn-admins`, the one the blueprint creates. Other
  names, or Authentik's own `authentik Admins`: `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`
  and `PORTAL_AUDITOR_GROUPS` (comma-separated). The scopes asked at sign-in are `OIDC_SCOPE`
  (default `openid profile email`).
- Signing out of the console ends the provider's session through Authentik's end-session endpoint,
  which needs a valid ID token: after the token expires (an hour) Authentik refuses the request and
  shows an error page. The console's own session is ended either way.
- Nothing from Authentik's own documentation about outposts or the Docker socket applies (and
  none is mounted).
