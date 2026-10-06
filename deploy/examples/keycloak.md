# Headscale Easy with Keycloak

[Keycloak](https://www.keycloak.org) as the identity provider of the all-in-one image. Keycloak runs
wherever you already run it; this page is the client you create in it and the variables the image gets.
Unlike Pocket ID, Keycloak can put the user's **groups** in what the console reads, so roles can follow
groups.

Replace `vpn.example.com` with the image's address and `sso.example.com` with Keycloak's.

## 1. In Keycloak

Create (or use) a realm, say `hse`. Its issuer is then `https://sso.example.com/realms/hse`.

**Clients → Create client**

| Field | Value |
|---|---|
| Client type | OpenID Connect |
| Client ID | `headscale` |
| Client authentication | **On** (a confidential client) |
| Authentication flow | Standard flow only |
| Valid redirect URIs | `https://vpn.example.com/admin/callback` and `https://vpn.example.com/oidc/callback` |
| Valid post logout redirect URIs | `https://vpn.example.com/admin/` |
| Proof Key for Code Exchange method | `S256` (Advanced settings) |

The two redirect URIs are the console's (`REDIRECT_URI` in `web/app.py`) and Headscale's. One client
serves both.

Copy the secret from **Credentials**.

**Groups, so roles follow them**: create a group `vpn-admins` and add the administrators. Then, in the
client: **Client scopes → `headscale-dedicated` → Configure a new mapper → Group Membership**:

| Field | Value |
|---|---|
| Name | `groups` |
| Token Claim Name | `groups` |
| Full group path | **Off** (the console compares the plain name) |
| Add to ID token | On |
| Add to access token | On |
| **Add to userinfo** | **On** (the console reads the groups from there) |

Users need an e-mail address, marked **Email verified** (the console ignores `PORTAL_ADMIN_EMAILS`
for an address the provider calls unverified).

## 2. The image's side

```bash
# .env of deploy/compose
OIDC_ISSUER=https://sso.example.com/realms/hse
OIDC_CLIENT_ID=headscale
OIDC_CLIENT_SECRET=<the secret>
#PORTAL_ADMIN_EMAILS=you@example.com   # administrators by address, in addition to the group
```

`docker compose up -d`. Headscale will not start until it can read
`<issuer>/.well-known/openid-configuration`, so Keycloak has to be reachable **from the container** at
that address (a name the container resolves, with a certificate it trusts).

Sign in at `https://vpn.example.com/admin/` with *Sign in with SSO*; devices with
`tailscale up --login-server https://vpn.example.com`.

## Roles

| In the console | Comes from |
|---|---|
| administrator | a member of the group `vpn-admins` (or the ones in `PORTAL_ADMIN_GROUPS`), or an address in `PORTAL_ADMIN_EMAILS` |
| member | everybody else: their own machines |

Other group names, and the narrower roles (*network admin*, *auditor*), go in `PORTAL_ADMIN_GROUPS`,
`PORTAL_NETWORK_ADMIN_GROUPS` and `PORTAL_AUDITOR_GROUPS` (comma-separated, in `.env`).

## Who can sign in

Headscale registers **whoever the provider lets in**. Restrict it with `HSE_OIDC_ALLOWED_DOMAINS` (the domain of the e-mail), `HSE_OIDC_ALLOWED_USERS` (addresses) or `HSE_OIDC_ALLOWED_GROUPS` (Headscale's `oidc.allowed_*`; comma-separated, empty = no restriction). In Keycloak,
leave **Realm settings → Login → User registration** *off* and do not enable a social login that
accepts anybody.

## What was run

Keycloak 26.8.0 (`start-dev`, plain HTTP on a private Docker network, `http://p4b4-kc:8080` as its
hostname) and the image, with the configuration above made through Keycloak's admin REST API, not
its screens:

| Check | Result |
|---|---|
| a realm, a group `vpn-admins`, a user in it (verified e-mail), the client with the two redirect URIs, S256, and the *Group Membership* mapper with *Add to userinfo* | created |
| the image with `OIDC_ISSUER=…/realms/hse` | starts, Headscale reads the issuer |
| *Sign in with SSO*, the Keycloak login form, back to `/admin/callback` | a session is created |
| the user, with **no** `PORTAL_ADMIN_EMAILS`, is only in the group | an administrator (`/admin/users` and `/admin/backups`: 200) |

**Not run:** the Keycloak admin screens as such (the labels above are those of 26.x, from its documentation
and from the REST fields that were set), HTTPS, two-factor, a real Tailscale client registering.
