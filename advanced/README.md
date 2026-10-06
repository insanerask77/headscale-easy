# Advanced configurations

The simple install is `compose.yaml` and an optional `.env`: `docker compose up -d`. Everything here is
optional and is added with one more file, never by editing `compose.yaml`:

```bash
docker compose -f compose.yaml -f advanced/<file>.yaml up -d      # several -f files combine
```

| I want… | Add | Read |
|---|---|---|
| an external PostgreSQL instead of SQLite | `advanced/postgres.yaml` | [postgres.md](postgres.md) |
| a PostgreSQL container beside it | `advanced/postgres-bundled.yaml` (instead of the line above) | [postgres.md](postgres.md) |
| my own proxy to take the HTTPS (nginx, Traefik, Caddy, Nginx Proxy Manager) | `advanced/proxy.yaml` | [proxy.md](proxy.md) |
| every backup copied to S3, B2, SFTP or a server | `advanced/backup-remote.yaml` | [backup-remote.md](backup-remote.md) |
| sign-in with Authentik or Pocket ID (run beside it) | `advanced/oidc/authentik.yaml`, `advanced/oidc/pocket-id.yaml` | [oidc/README.md](oidc/README.md) |
| sign-in with Keycloak, Google or an Authentik you have | only variables in `.env` | [oidc/README.md](oidc/README.md) |

The full guide, with the reasoning, the checklists and what was run, is in the documentation: *Advanced
configurations*. Compose 2.24 or newer is needed (the overlays use `!override`).
