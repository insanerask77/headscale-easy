# PostgreSQL

SQLite is the default. Choose PostgreSQL **before** adding devices: there is no tool to move a tailnet between
the two.

```bash
docker compose -f compose.yaml -f advanced/postgres.yaml up -d           # a server you already have
docker compose -f compose.yaml -f advanced/postgres-bundled.yaml up -d   # or one in a container beside it (not both)
```

```bash
# .env
HEADSCALE_PG_HOST=db.example.com     # not needed with postgres-bundled.yaml
HEADSCALE_PG_PASS=...                # owner of the database: Headscale writes with it
HEADSCALE_PG_RO_PASS=...             # the console reads with a read-only role the image creates itself
```

Optional: `HEADSCALE_PG_PORT`, `HEADSCALE_PG_NAME`, `HEADSCALE_PG_USER`, `HEADSCALE_PG_SSLMODE` (`require` here),
`HEADSCALE_PG_RO_USER`, `POSTGRES_VERSION` (bundled, default 17). Backups run `pg_dump` (client 18: servers 13 to 18).
If the owner may not create roles, run [`templates/headscale-pg-readonly.sql`](../templates/headscale-pg-readonly.sql) as an
administrator.

Checked by `scripts/advanced-smoke.sh postgres 16|17|18`. Full guide: documentation → *Advanced configurations → PostgreSQL*.
