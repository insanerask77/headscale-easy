# PostgreSQL

SQLite is the default and the right choice for most installs: nothing to run and nothing to back up
separately. Use PostgreSQL when you already run one, want its tooling, or want the database off the host.

**Choose before you add devices.** Headscale has no tool to move a tailnet from SQLite to PostgreSQL (or back):
a different database means a new tailnet and registering the devices again.

## A server you already have

```bash
docker compose -f compose.yaml -f advanced/postgres.yaml up -d
```

```bash
# .env
HEADSCALE_PG_HOST=db.example.com
HEADSCALE_PG_PASS=...            # the owner of the database; Headscale creates and migrates its tables with it
HEADSCALE_PG_RO_PASS=...         # the console's read-only role (below)
#HEADSCALE_PG_PORT=5432  HEADSCALE_PG_NAME=headscale  HEADSCALE_PG_USER=headscale
#HEADSCALE_PG_SSLMODE=require    # default for this overlay; verify-full for a server you reach over a network
```

Create an empty database and its owner first (`CREATE ROLE headscale LOGIN PASSWORD '…'; CREATE DATABASE headscale OWNER headscale;`).

## No server yet

```bash
docker compose -f compose.yaml -f advanced/postgres-bundled.yaml up -d
```

The same variables, minus the host: the overlay starts a `postgres` container (`POSTGRES_VERSION`, default 17)
with its own volume, and Headscale talks to it over the project's network. Use this file **instead of**
`advanced/postgres.yaml`.

## Two roles

| Role | Who uses it | What it can do |
|---|---|---|
| `headscale` (`HEADSCALE_PG_USER`) | Headscale | everything in its database |
| `headscale_ro` (`HEADSCALE_PG_RO_USER`) | the console | `SELECT id, host_info, endpoints` of `nodes`, in read-only transactions. Nothing else |

The console only needs what each device reports about itself (system, Tailscale version, relay, endpoints):
never the node keys, users or API keys, so it does not get them.

**The image creates the read-only role itself**, once Headscale is healthy and before every console start, with
the SQL in [`templates/headscale-pg-readonly.sql`](https://github.com/insanerask77/headscale-easy/blob/main/templates/headscale-pg-readonly.sql).
It is idempotent, so a restart repairs a dropped role. If the owner may not create roles (some managed
services), the image logs an `ERROR` and the console falls back to the owner's credentials: it keeps working
without the separation. In that case create the role as an administrator:

```bash
HSE_RO_USER=headscale_ro HSE_RO_PASS='the password' \
  psql -v ON_ERROR_STOP=1 -h <host> -U <admin> -d headscale -f templates/headscale-pg-readonly.sql
```

## Backups

`hse backup` and the scheduled backups run `pg_dump` (a PostgreSQL 18 client is in the image) and put
`headscale.sql` in the archive. A restore from the console, or `hse restore <file> --with-postgres`, loads it
while Headscale is stopped (it drops and re-creates the Headscale database's objects). `pg_dump` dumps servers
**up to its own major version**: 13 to 18 here. For a newer server the backup fails with a sentence that says
so; use a PostgreSQL 18 or older server. The dump omits the one line PostgreSQL 17 and later add that a
PostgreSQL 16 would refuse, so a backup restores on any server from 13 to 18.

The console's accounts, the keys and the configuration are not in PostgreSQL: they are in the archive's other
files.

## What was run

`scripts/advanced-smoke.sh postgres 16|17|18` with `advanced/postgres-bundled.yaml` and a real PostgreSQL of
each version, on the image built from this repository. For each: Headscale healthy; the console's process runs
with `headscale_ro`; that role reads `id, host_info, endpoints` and is refused `node_key`, `users`, `api_keys`
and any write; `hse backup` writes `headscale.sql`; a user deleted after the backup comes back with
`hse restore --with-postgres` and the console is healthy again.

**Not run:** `advanced/postgres.yaml` against a managed PostgreSQL (the `require` / `verify-full` paths and an
owner that cannot create roles), and a real Tailscale client registering on PostgreSQL in this round.
