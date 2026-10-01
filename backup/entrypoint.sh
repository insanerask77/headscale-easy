#!/bin/sh
# Runs backup.sh on BACKUP_SCHEDULE (cron syntax) with busybox crond.
# BACKUP_SCHEDULE=off disables the schedule; the container then just waits,
# so manual backups (make backup) still work.
set -eu

SCHEDULE="${BACKUP_SCHEDULE:-0 3 * * *}"
if [ "$SCHEDULE" = "off" ]; then
    echo "scheduled backups: off"
    exec sleep infinity
fi

# crond runs jobs without the container's environment: pass it through a file
env | grep -E '^(BACKUP_|PG|TZ=|AUTHENTIK_|AWS_|RCLONE_)' | sed 's/^/export /; s/=\(.*\)$/="\1"/' > /etc/backup.env
echo "$SCHEDULE . /etc/backup.env; /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > /etc/crontabs/root
echo "scheduled backups: '$SCHEDULE' (TZ=${TZ:-UTC}), keeping ${BACKUP_KEEP_DAYS:-14} days in /backups"
exec crond -f -l 8
