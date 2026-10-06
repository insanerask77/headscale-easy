# Advanced configurations

The simple install is [`compose.yaml`](https://github.com/insanerask77/headscale-easy/blob/main/compose.yaml)
and an optional `.env`: `docker compose up -d`, then the setup wizard. It needs nothing from this section.

Everything here is optional and is added with **one more file**, never by editing `compose.yaml`. Compose
merges several files, so the options combine:

```bash
docker compose -f compose.yaml -f advanced/proxy.yaml -f advanced/postgres.yaml up -d
```

You need Docker Compose 2.24 or newer (the overlays use `!override`).

| I want… | Add | Guide |
|---|---|---|
| a PostgreSQL I already run, instead of SQLite | `advanced/postgres.yaml` | [PostgreSQL](postgres.md) |
| a PostgreSQL container beside it | `advanced/postgres-bundled.yaml` (instead of the line above) | [PostgreSQL](postgres.md) |
| my own proxy to take the HTTPS (nginx, Traefik, Caddy, Nginx Proxy Manager) | `advanced/proxy.yaml` | [A proxy in front](proxy.md) |
| every backup copied to S3, B2, SFTP or a server | `advanced/backup-remote.yaml` | [Remote backups](backup-remote.md) |
| sign-in with Authentik, Pocket ID, Keycloak or Google | `advanced/oidc/authentik.yaml`, `advanced/oidc/pocket-id.yaml`, or just variables | [Your own sign-in provider](oidc.md) |

Variables go in the same `.env` as always; each guide lists the ones it needs, and the complete list is the
[environment variable reference](../configuration.md#reference).

## What each guide says it checked

Every guide ends with what was run and what was only read. For the options that run on your machine, the
repository checks them on every change: `scripts/validate.sh` runs `docker compose config` on every overlay
and on the combinations above, and `scripts/advanced-smoke.sh` starts the PostgreSQL overlay on PostgreSQL
16, 17 and 18 and the proxy overlay behind a real nginx.
