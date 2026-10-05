#!/bin/sh
# Runs backup.sh on BACKUP_SCHEDULE (cron syntax) with busybox crond.
# BACKUP_SCHEDULE=off disables the schedule; the container then just waits,
# so manual backups (make backup) still work.
set -eu

# BACKUP_MODE=sync (all-in-one image): the image makes the backups itself in
# /data/backups; this container only copies them to BACKUP_REMOTE. Mount that
# directory at /backups. Every BACKUP_SYNC_INTERVAL minutes (default 15) each
# archive the remote lacks is pushed, then the remote retention runs.
# BACKUP_SYNC_ONCE=1 does one pass and exits (tests, cron on the host).
if [ "${BACKUP_MODE:-create}" = "sync" ]; then
    [ -n "${BACKUP_REMOTE:-}" ] || { echo "backup: BACKUP_MODE=sync needs BACKUP_REMOTE" >&2; exit 1; }
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
fi

SCHEDULE="${BACKUP_SCHEDULE:-0 3 * * *}"
if [ "$SCHEDULE" = "off" ]; then
    echo "scheduled backups: off"
    exec sleep infinity
fi

# crond runs jobs without the container's environment: pass it through a file
env | grep -E '^(BACKUP_|PG|TZ=|AUTHENTIK_|HEADSCALE_|AWS_|RCLONE_)' | sed 's/^/export /; s/=\(.*\)$/="\1"/' > /etc/backup.env
echo "$SCHEDULE . /etc/backup.env; /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > /etc/crontabs/root
echo "scheduled backups: '$SCHEDULE' (TZ=${TZ:-UTC}), keeping ${BACKUP_KEEP_DAYS:-14} days in /backups"
exec crond -f -l 8
