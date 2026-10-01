#!/bin/sh
# Headscale Easy backup: one .tar.gz in /backups with
#   config/     .env, headscale-config.yaml, Caddyfile, compose override,
#               the web UI's renewed API key
#   headscale/  db.sqlite (consistent online copy) and the private keys
#   web/        audit.db, the web UI's activity log
#   authentik/  authentik.sql (pg_dump), if Authentik is used
#   caddy/      pki/ (the self-signed CA), if there is one
# deletes backups older than BACKUP_KEEP_DAYS and, with BACKUP_REMOTE set,
# also uploads each one (see remote.sh).
set -eu

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
OUT_DIR=/backups
STAMP=$(date +%Y%m%d-%H%M%S)
NAME="headscale-easy-${STAMP}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/$NAME/config" "$WORK/$NAME/headscale"
B="$WORK/$NAME"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"; }

# Configuration (the project directory, mounted read-only)
for f in .env headscale-config.yaml Caddyfile docker-compose.override.yml data/web/api-key data/web/mfa-required; do
    [ -f "/project/$f" ] && cp -p "/project/$f" "$B/config/$(echo "$f" | tr / _)"
done

# Headscale: SQLite's online backup is consistent while Headscale writes
# (copying db.sqlite + -wal by hand is not). The private keys keep every
# device's registration valid after a restore.
sqlite3 "file:/headscale/db.sqlite?mode=ro" ".backup '$B/headscale/db.sqlite'"
[ "$(sqlite3 "$B/headscale/db.sqlite" 'PRAGMA integrity_check;')" = "ok" ] || { log "integrity check failed"; exit 1; }
for f in /headscale/*.key; do [ -f "$f" ] && cp -p "$f" "$B/headscale/"; done

# The web UI's activity log (Logs page), same consistent online copy
if [ -f /project/data/web/audit.db ]; then
    mkdir -p "$B/web"
    sqlite3 "file:/project/data/web/audit.db?mode=ro" ".backup '$B/web/audit.db'"
fi

# Authentik: plain SQL dump (restorable into any PostgreSQL of the same major)
if [ -n "${PGPASSWORD:-}" ] && pg_isready -q -h "${PGHOST:-authentik-postgresql}" -U "${PGUSER:-authentik}" 2>/dev/null; then
    mkdir -p "$B/authentik"
    pg-client.sh pg_dump -h "${PGHOST:-authentik-postgresql}" -U "${PGUSER:-authentik}" -d "${PGDATABASE:-authentik}" \
        --no-owner --clean --if-exists > "$B/authentik/authentik.sql"
else
    log "Authentik database not reachable: skipped (fine if Authentik is not used)"
fi

# Caddy's internal CA (SSL_MODE=selfsigned): clients trust it
if [ -d /caddy/caddy/pki ]; then
    mkdir -p "$B/caddy" && cp -rp /caddy/caddy/pki "$B/caddy/"
fi

umask 077
tar -czf "$OUT_DIR/.$NAME.tar.gz" -C "$WORK" "$NAME"
mv "$OUT_DIR/.$NAME.tar.gz" "$OUT_DIR/$NAME.tar.gz"
# Owned by the owner of the project files, not root
[ -n "${BACKUP_UID:-}" ] && chown "${BACKUP_UID}:${BACKUP_GID:-$BACKUP_UID}" "$OUT_DIR/$NAME.tar.gz"
log "wrote $NAME.tar.gz ($(du -h "$OUT_DIR/$NAME.tar.gz" | cut -f1))"

# Remote copy (BACKUP_REMOTE: S3, B2, SFTP... via rclone, or rsync over SSH).
# A failure is reported at the end, after the local retention has run.
REMOTE_FAILED=0
if [ -n "${BACKUP_REMOTE:-}" ]; then
    if remote.sh push "$OUT_DIR/$NAME.tar.gz" && remote.sh prune; then :; else
        log "remote copy FAILED (the local backup is fine)"; REMOTE_FAILED=1
    fi
fi

# Retention
find "$OUT_DIR" -maxdepth 1 -name 'headscale-easy-*.tar.gz' -mtime +"$KEEP_DAYS" -print -delete \
    | sed 's|.*/|removed old backup: |'
[ "$REMOTE_FAILED" = 0 ] || exit 1
