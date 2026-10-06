# Remote backups

A second container copies each new backup to S3, B2, SFTP (rclone) or a server (rsync over SSH). It sees only
the backups volume, read-only.

```bash
docker compose -f compose.yaml -f advanced/backup-remote.yaml up -d
```

```bash
# .env
BACKUP_REMOTE=s3:my-bucket/headscale-easy      # or rsync:user@host:/srv/backups
#BACKUP_REMOTE_KEEP_DAYS=14  BACKUP_SYNC_INTERVAL=15  HSE_REMOTE_CONFIG=./remote-config
```

The rclone config (`rclone.conf`) or the SSH key (`id_ed25519`) goes in `./remote-config/`. Prefer credentials that
can write but not delete. Checked by `scripts/compose-smoke.sh` (a local folder as the remote). Full guide:
documentation → *Advanced configurations → Remote backups*.
