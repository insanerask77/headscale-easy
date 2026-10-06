# Headscale Easy with an external PostgreSQL

SQLite is the default and the right choice for most installs: nothing to run, nothing to back up
separately. Use PostgreSQL when you already run one, need its backups and tooling, or want the
database off the host.

```bash
cp .env.example .env && chmod 600 .env     # HSE_PUBLIC_URL and the two passwords
docker compose up -d
```

`docker-compose.yml` here starts a PostgreSQL container for the example. To use your own server, delete
the `postgres` service and the `depends_on`, and set `HEADSCALE_PG_HOST` (and `HEADSCALE_PG_SSLMODE`,
`HEADSCALE_PG_USER`, `HEADSCALE_PG_NAME`) in `.env`. The image takes the same variables everywhere:
`HEADSCALE_DB_TYPE=postgres`, `HEADSCALE_PG_*`. See [All-in-one: variables](../../../docs/all-in-one.md).

## Two roles

| Role | Who uses it | What it can do |
|---|---|---|
| `headscale` (`HEADSCALE_PG_USER`) | Headscale | everything in its database: it creates and migrates the tables |
| `headscale_ro` (`HEADSCALE_PG_RO_USER`) | the console | `SELECT id, host_info, endpoints` of `nodes`, in read-only transactions. Nothing else |

The console only needs the information each device reports (system, Tailscale version, relay,
endpoints). It never needs the node keys, the users or the API keys, so it does not get them.

**The image creates the read-only role itself.** Once Headscale is healthy (its tables exist), and before
every console start, it runs [`readonly-role.sql`](readonly-role.sql) as the owner. It is idempotent, so a
restart repairs a dropped or changed role. If the owner is not allowed to create roles (some managed
services), the image logs an `ERROR` and the console falls back to the owner's credentials: it keeps working,
without the separation. In that case create the role as an administrator and the image will find it there:

```bash
HSE_RO_USER=headscale_ro HSE_RO_PASS='the password from .env' \
  psql -v ON_ERROR_STOP=1 -h <host> -U <admin> -d headscale -f readonly-role.sql
```

Without `HEADSCALE_PG_RO_USER` the console connects as the owner, as before, and the log says so.

## Backups

`hse backup` and the scheduled backups run `pg_dump` (a PostgreSQL 18 client is in the image, +13 MB) and
write `headscale.sql` into the archive; **Backups** in the console lists, downloads and restores them like any
other. A restore from the console loads the dump when both the install and the archive use PostgreSQL;
`hse restore <file> --with-postgres` does the same from a terminal.

`pg_dump` dumps servers **up to its own major version**: this image's client is 18, so 13 to 18 work. For a
newer server the backup fails with `PostgreSQL is 19 and this image can dump up to 18`; use the sidecar
(`BACKUP_MODE=create` in `backup/`, which carries clients 16 and 17) or a newer image.

The dump goes without `SET transaction_timeout`, the one line a PostgreSQL 16 would refuse.

A restore drops and re-creates every object of the Headscale database (`pg_dump --clean`) while Headscale is
stopped. Nothing else of the server is in PostgreSQL: the console's accounts, the keys and the configuration
are in the archive's other files.

**Moving from SQLite to PostgreSQL** is not supported: Headscale has no tool for it. Start a new tailnet on
PostgreSQL and register the devices again.

## What was run

On the all-in-one image (`HEADSCALE_DB_TYPE=postgres`), against a real **PostgreSQL 17**:

- Headscale healthy on PostgreSQL; a user and a pre-auth key created; a real Tailscale client registered.
- The role created by the image; the console's connection (`application_name=headscale-easy`) logged in
  as `headscale_ro`, never as the owner, while it listed the registered device.
- The role: `SELECT id, host_info, endpoints FROM nodes` allowed; `SELECT node_key`, `UPDATE nodes`,
  `SELECT * FROM users`, `SELECT * FROM api_keys` and `CREATE TABLE` refused.
- `hse backup` wrote `headscale.sql`; a user created after it disappeared with `hse restore --with-postgres`,
  the deleted one came back, and the container was healthy afterwards.

The same round, scripted (`scripts/aio-smoke.sh` with `HSE_SMOKE_PG=1`), also ran against real **PostgreSQL 16 and
18**, and CI runs it for 16, 17 and 18 on every change. It found that `pg_dump` 17 and later write
`SET transaction_timeout = 0;` whatever the server is: a PostgreSQL 16 refuses the whole restore. The image removes
that one line from the dump (it only switches off a limit that is off by default), so a backup restores on any
server from 13 to 18.

**Not run:** a managed PostgreSQL (the `HEADSCALE_PG_SSLMODE=require` path and a restricted owner role), and
moving data between PostgreSQL versions.
