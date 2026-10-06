#!/bin/sh
# Remote copies of the all-in-one image's backups (the backup-remote profile of deploy/compose).
#
# The image makes the backups itself in /data/backups; this container only copies them to
# BACKUP_REMOTE. Mount that directory at /backups. Every BACKUP_SYNC_INTERVAL minutes (default 15)
# each archive the remote lacks is pushed, then the remote retention runs.
# BACKUP_SYNC_ONCE=1 does one pass and exits (tests, cron on the host).
set -eu

[ "${BACKUP_MODE:-sync}" = "sync" ] || { echo "backup: BACKUP_MODE=${BACKUP_MODE} is not supported: this container only syncs" >&2; exit 1; }
[ -n "${BACKUP_REMOTE:-}" ] || { echo "backup: BACKUP_REMOTE is required" >&2; exit 1; }
INTERVAL="${BACKUP_SYNC_INTERVAL:-15}"
case "$INTERVAL" in ''|*[!0-9]*|0) echo "backup: BACKUP_SYNC_INTERVAL must be a number of minutes" >&2; exit 1 ;; esac
DIR="${BACKUP_SYNC_DIR:-/backups}"
echo "backup: sync mode, $DIR -> $BACKUP_REMOTE every ${INTERVAL} min"
trap 'exit 0' TERM INT
while true; do
    remote.sh sync "$DIR" || echo "backup: sync pass had errors (retrying next interval)"
    [ "${BACKUP_SYNC_ONCE:-0}" = "1" ] && exit 0
    sleep "$((INTERVAL * 60))" &
    wait $!
done
