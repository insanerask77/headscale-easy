# Your own sign-in provider

Local accounts (password and two-factor in the console) are the default and need nothing else. If your people
already have an identity provider, set `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` and they sign in
to the console and register devices (`tailscale up --login-server …`) through it. Local accounts keep working
beside it.

The provider only signs people in. The console and Headscale trust its identity and its groups and manage
nothing inside it: invitations, resets and two-factor stay with the console's local accounts.

**Register two redirect URIs** with the provider, for one client that serves both:

| URI | Used by |
|---|---|
| `https://vpn.example.com/console/callback` | the console |
| `https://vpn.example.com/oidc/callback` | Headscale |
| `https://vpn.example.com/console/` | logout (where the provider lists post-logout URIs) |

Headscale will not start until it can read `<issuer>/.well-known/openid-configuration`: the provider has to be
reachable **from the container**, at that address, with a certificate the container trusts.

## Who can sign in, and who is what

| Variable | What |
|---|---|
| `HSE_OIDC_ALLOWED_DOMAINS`, `HSE_OIDC_ALLOWED_USERS`, `HSE_OIDC_ALLOWED_GROUPS` | Who may sign in (Headscale's `oidc.allowed_*`), comma-separated. **Empty means everyone the provider lets in**: right for your own Authentik or Keycloak, wrong for Google, where any account qualifies |
| `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS` | Who is what in the console, by the groups your provider sends (default admin group: `vpn-admins`) |
| `PORTAL_ADMIN_EMAILS` | Administrators by e-mail, for providers without groups. Only counts for an address the provider calls **verified** |
| `OIDC_SCOPE` | Scopes asked at sign-in (default `openid profile email`; add `groups` where the provider releases groups only on request) |

Headscale identifies an OIDC user by the provider's **issuer URL** plus the user's id: pick the issuer address
once and keep it.

## Authentik

```bash
docker compose -f compose.yaml -f advanced/oidc/authentik.yaml up -d
```

Runs Authentik (server, worker, its own PostgreSQL) beside the image and serves it at
`https://<your domain>/authentik/` **through the image's own Caddy** (`HSE_AUTHENTIK_UPSTREAM=authentik-server:9000`),
so the issuer is `https://vpn.example.com/authentik/application/o/headscale/` and never moves.

```bash
# .env: generate each secret with   openssl rand -base64 36 | tr -d '/+=' | cut -c1-40
HSE_PUBLIC_URL=https://vpn.example.com
HSE_DOMAIN=vpn.example.com
AUTHENTIK_SECRET_KEY=...   AUTHENTIK_PG_PASS=...   OIDC_CLIENT_SECRET=...
AUTHENTIK_ADMIN_EMAIL=admin@example.com   AUTHENTIK_BOOTSTRAP_PASSWORD=...   # Authentik's first administrator ('akadmin')
#GOOGLE_CLIENT_ID=...  GOOGLE_CLIENT_SECRET=...     # "Sign in with Google" inside Authentik
```

The first start takes **several minutes** (Authentik creates its database and applies the blueprint); meanwhile
the image restarts Headscale and shows `unhealthy`. Then open `https://vpn.example.com/authentik/` as `akadmin`.
The blueprint
([`advanced/oidc/authentik/blueprints/headscale.yaml`](https://github.com/insanerask77/headscale-easy/blob/main/advanced/oidc/authentik/blueprints/headscale.yaml))
creates the OIDC application and the groups `headscale-users` (may join the tailnet) and `vpn-admins`
(also administer the console); add people to them. Behind a proxy of yours, also set `HSE_SELF_IP` to the proxy's
address so the image reaches its own public name through it.

Already have an Authentik? Skip the overlay: set `OIDC_ISSUER` to its issuer, or serve it under your domain by
running it next to the container and setting `HSE_AUTHENTIK_UPSTREAM=<host>:9000`.

## Pocket ID

[Pocket ID](https://pocket-id.org) is a small provider (about 100 MB, no database server) that signs people in
with passkeys. Passkeys need HTTPS and Pocket ID needs a domain of its own, and two domains cannot both own
port 443, so the overlay puts a small Caddy in front of both. The image runs with `HSE_TLS=off` and trusts that
Caddy's fixed address only.

```bash
# .env
HSE_DOMAIN=vpn.example.com   ID_DOMAIN=id.example.com   ACME_EMAIL=admin@example.com
POCKET_ID_ENCRYPTION_KEY=...   # openssl rand -base64 32; keep it: without it Pocket ID's data cannot be read
OIDC_ISSUER=https://id.example.com   PORTAL_ADMIN_EMAILS=admin@example.com
```

The order matters: Headscale refuses to start with an issuer that does not answer, and the client's id and secret
exist only once Pocket ID is up.

1. Point both domains at the host; ports 80 and 443 must reach it.
2. `docker compose -f compose.yaml -f advanced/oidc/pocket-id.yaml up -d proxy pocket-id`
3. Open `https://id.example.com/setup`, create the administrator and its passkey.
4. **Administration → OIDC Clients → Add**: name *Headscale Easy*, the two callback URLs above, logout callback
   `https://vpn.example.com/console/`, public client off, PKCE on. Copy the id and the secret into `.env` as
   `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
5. **Administration → Application configuration**: turn **Emails verified** on, or the console (correctly)
   ignores `PORTAL_ADMIN_EMAILS` and everybody is a member.
6. `docker compose -f compose.yaml -f advanced/oidc/pocket-id.yaml up -d`, then *Sign in with SSO* at
   `https://vpn.example.com/console/`.

Keep Pocket ID closed (**Allow user sign-ups** *Disabled*, the default) so only the people you create or invite
can sign in. Roles by group need `OIDC_SCOPE=openid profile email groups`; `PORTAL_ADMIN_EMAILS` is the simplest
way.

## Keycloak

Keycloak runs wherever you already run it. In a realm (say `hse`, issuer `https://sso.example.com/realms/hse`)
create a client:

| Field | Value |
|---|---|
| Client type / ID | OpenID Connect / `headscale` |
| Client authentication | **On** (a confidential client), Standard flow only |
| Valid redirect URIs | the two callbacks above |
| Valid post logout redirect URIs | `https://vpn.example.com/console/` |
| PKCE method | `S256` (Advanced settings) |

Roles by group: create a group `vpn-admins`, and in the client's dedicated scope add a **Group Membership** mapper
with *Token Claim Name* `groups`, *Full group path* **off** and *Add to userinfo* **on** (the console reads groups
there). Users need an e-mail marked *Email verified*. Then in `.env`:

```bash
OIDC_ISSUER=https://sso.example.com/realms/hse
OIDC_CLIENT_ID=headscale
OIDC_CLIENT_SECRET=<the secret from Credentials>
```

Leave **User registration** off in the realm and do not enable a social login that accepts anybody.

## Google

```bash
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=<client id>.apps.googleusercontent.com
OIDC_CLIENT_SECRET=<the secret>
PORTAL_ADMIN_EMAILS=you@your-company.com
```

Create the OAuth client in Google Cloud (*Credentials → OAuth client ID → Web application*) with the two
redirect URIs above. **Read this first:** Google lets in any Google account unless you limit it.

| Your Google | Safe to use directly? |
|---|---|
| **Workspace**, consent screen user type **Internal** | Yes: only your organisation can sign in |
| Personal Gmail accounts, consent screen **External** | **No**, unless you also set `HSE_OIDC_ALLOWED_DOMAINS` or `HSE_OIDC_ALLOWED_USERS` |

Google sends no groups: roles come from `PORTAL_ADMIN_EMAILS`; everybody else is a member. Without Workspace,
put Google behind a provider that decides who gets in (Authentik's blueprint has "Sign in with Google" as a source).

## What was run

- **Pocket ID overlay** (`advanced/oidc/pocket-id.yaml`, Pocket ID `v1`): with a test Caddyfile in plain HTTP
  (there is no public domain here), the Caddy, Pocket ID and the image start and become healthy; Pocket ID answers
  its discovery document through the Caddy, the image answers `/key` and redirects `/console` to its sign-in page
  through the Caddy, and the image publishes only UDP 3478.
- `scripts/validate.sh` runs `docker compose config` on the Authentik and Pocket ID overlays and on the
  combinations with the proxy, PostgreSQL and remote backups.

**Not run in this round, only read:** a person signing in through any provider (Authentik, Pocket ID, Keycloak,
Google), Authentik's first start and its trimmed blueprint on a live instance, Keycloak's mapper, Google's
consent screen, HTTPS with Let's Encrypt, and a real Tailscale client registering through a provider. The field
and menu names above are those of Pocket ID 1.16, Keycloak 26 and Google's console, from their documentation, and
may have moved. Treat these sections as a checklist to confirm.
