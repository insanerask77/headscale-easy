# Headscale Easy with Authentik

The all-in-one image next to an [Authentik](https://goauthentik.io) of its own: people
sign in to the console and register their devices with their Authentik account
(passwords, two-factor, invitations, password reset links, "Sign in with Google").
It is the path that changes nothing for people already running the 1.x stack.

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

which is exactly what the 1.x stack used. That URL is not a detail: **Headscale
identifies every OIDC user by their issuer plus their subject**. Move Authentik to
another address and every user stops matching the account Headscale has for them.
Keep it.

One OIDC client (`OIDC_CLIENT_ID`, default `headscale`) serves Headscale (`tailscale up`)
and the console. The 1.x stack had two clients; the blueprint still describes the second
(so the file applies cleanly to an existing Authentik) but this image does not use it.
The only change to the blueprint, compared with `authentik/blueprints/headscale.yaml`, is
that the `headscale` provider also accepts the console's addresses:

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
- **The console**: `https://vpn.example.com/admin/` and *Sign in with Authentik*. Members of
  `vpn-admins` (or `authentik Admins`) are administrators; so are the addresses in
  `PORTAL_ADMIN_EMAILS`. Invitations, password reset links and the two-factor mode work from
  the console because it has `AUTHENTIK_URL` and `AUTHENTIK_API_TOKEN`.

### Behind a proxy

Set `HSE_TLS=off`, `HSE_TRUSTED_PROXIES=<the proxy>/32` and `HSE_SELF_IP=<the proxy's address>`
(see [`../front-proxy`](../front-proxy)). `HSE_SELF_IP` is what makes the image's own public
name lead to the proxy instead of to itself (`extra_hosts`): Headscale and the console fetch the
issuer's configuration at that name, and need a certificate they trust.

## Moving an existing 1.x stack here

**This was not run end to end** (it needs a real 1.x install). What it takes, from the code:

1. Keep the **same public address**, so the issuer URL does not change.
2. Reuse the 1.x `.env` values: `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` (the `headscale` client),
   `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_PG_PASS` and `PORTAL_AUTHENTIK_TOKEN` (as
   `AUTHENTIK_API_TOKEN`). The secret key must be the same one: with another, the existing
   sessions and tokens stop working.
3. Bring Authentik's database along: `pg_dump` from the 1.x `authentik-postgresql` and load it into
   this one's before the first start (`docker compose cp`, `psql`), or point this compose at the
   1.x volumes.
4. Let the new `blueprints/headscale.yaml` apply: the existing `headscale` provider gets the two
   console addresses. If you prefer to do it by hand: Authentik → Applications → Providers →
   `headscale` → add `https://<domain>/admin/callback` (strict) and the logout address.
5. Headscale's own data (`headscale-data`, the keys and the database) goes to `hse-data` with
   `hse restore` from a backup: that is phase 5's migration script, which does 3 to 5 for you.

## What was run

On this branch's image, on one host, with the example compose file as it is (only the address,
the ports and the secrets set; HTTP instead of HTTPS because there was no public domain):

| Check | Result |
|---|---|
| `docker compose up -d`, Authentik 2026.8.3 first start | migrations and blueprint applied, several minutes on a slow machine (the first attempt, with `depends_on: service_healthy`, failed: that is why the image only waits for Authentik to *start*) |
| the issuer's discovery document through the image's Caddy | `issuer: http://vpn.test/authentik/application/o/headscale/`, the same path as 1.x |
| Headscale, after Authentik answers | starts by itself (it had logged `503 authentik starting` meanwhile); `/key` answers |
| console: *Sign in with SSO* | redirects to Authentik's authorize endpoint with `client_id=headscale` and `redirect_uri=…/admin/callback` |
| Authentik and the three addresses | `/admin/callback` and `/oidc/callback`: accepted, go on to the sign-in flow; an address that is not registered: refused |
| the console's environment | has `OIDC_ISSUER`, `AUTHENTIK_URL` and `AUTHENTIK_API_TOKEN` |

**Not run:** a person signing in through Authentik's own screens and landing in the console
(the flows are Authentik's and were not scripted), Authentik's groups giving the admin role,
invitations and reset links, Google as a source, HTTPS with Let's Encrypt (the loopback
`extra_hosts` trick relies on Caddy serving a certificate Headscale trusts), and the move of a
real 1.x stack.

## Limits

- Roles by group: the default admin groups (`vpn-admins`, `authentik Admins`) are the ones the blueprint
  creates, so Authentik works as is. Other names: `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS` and
  `PORTAL_AUDITOR_GROUPS` (comma-separated) in `.env`. The scopes asked at sign-in are `OIDC_SCOPE`
  (default `openid profile email`).
- The `x-authentik-env` block mirrors the 1.x compose file; nothing from Authentik's own
  documentation about outposts or the Docker socket applies (and none is mounted).
