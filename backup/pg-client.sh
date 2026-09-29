#!/bin/sh
# pg-client.sh <pg_dump|psql|...> [args]: runs the PostgreSQL client whose major
# version matches the server (PGHOST/PGUSER/PGPASSWORD), falling back to the
# newest installed one.
set -eu
tool="$1"; shift
major=$(/usr/libexec/postgresql17/psql -tAqX -d "${PGDATABASE:-postgres}" -c 'SHOW server_version_num' 2>/dev/null | cut -c1-2 || true)
bin="/usr/libexec/postgresql${major}/${tool}"
[ -n "$major" ] && [ -x "$bin" ] || bin="/usr/libexec/postgresql17/${tool}"
exec "$bin" "$@"
