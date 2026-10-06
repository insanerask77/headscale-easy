# Your own sign-in provider

Local accounts need nothing. To also let people sign in through Authentik, Pocket ID, Keycloak or Google, set
`OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` in `.env`, and register two redirect URIs with the
provider: `https://<domain>/admin/callback` (console) and `https://<domain>/oidc/callback` (Headscale).

| Provider | How |
|---|---|
| Authentik, run beside the image | `docker compose -f compose.yaml -f advanced/oidc/authentik.yaml up -d` (served at `/authentik/` through the image's own Caddy; blueprint and theme in [`authentik/`](authentik/)) |
| Pocket ID (passkeys) | `docker compose -f compose.yaml -f advanced/oidc/pocket-id.yaml up -d proxy pocket-id`, create the client, then `up -d` (a Caddy takes the HTTPS for both names) |
| Keycloak, Google, an Authentik you already run | only the variables in `.env` |

Who may sign in: `HSE_OIDC_ALLOWED_DOMAINS` / `_USERS` / `_GROUPS` (empty = everyone the provider lets in: **not
safe for Google**). Roles: `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS`,
`PORTAL_ADMIN_EMAILS`. Required variables of each overlay: the first lines of its file. Full guide, with the
client fields for each provider and what was run: documentation → *Advanced configurations → Your own sign-in
provider*.
