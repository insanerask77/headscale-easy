# Remote backups

The container already makes a backup every night and keeps it on its volume (see
[Operations → Backups](../operations.md#backups)). A backup on the same server does not survive losing the
server. This add-on is a small second container that copies each new archive somewhere else.

```bash
docker compose -f compose.yaml -f advanced/backup-remote.yaml up -d
```

It sees only the backups volume, **read-only**, uploads each archive once and applies a retention on the
remote. rclone and rsync live in that container, not in the main image.

## Where to

Set `BACKUP_REMOTE` in `.env`, and put what the destination needs in `./remote-config/`
(`HSE_REMOTE_CONFIG` changes the folder).

| Destination | `BACKUP_REMOTE` | In `./remote-config/` |
|---|---|---|
| An rclone remote: S3, B2, SFTP, Google Drive and [dozens more](https://rclone.org/overview/) | `s3:my-bucket/headscale-easy` | `rclone.conf` (`rclone config` writes it) |
| S3 without a config file | `:s3,provider=AWS,env_auth=true,region=eu-west-1:my-bucket/dir`, plus `BACKUP_AWS_ACCESS_KEY_ID` and `BACKUP_AWS_SECRET_ACCESS_KEY` | nothing |
| rsync over SSH | `rsync:user@host:/srv/backups` (the directory must exist) | `id_ed25519`, optionally `known_hosts` (without it the first host key is accepted); `BACKUP_REMOTE_SSH_PORT` changes the port |

`BACKUP_REMOTE_KEEP_DAYS` sets the retention on the remote (default `BACKUP_KEEP_DAYS`), and
`BACKUP_SYNC_INTERVAL` how often it looks for new archives, in minutes (default 15).

Use a key or bucket that can write but **not delete** if you can: then a compromised server cannot erase its own
backups (set the retention in the bucket's lifecycle rules instead). If an upload fails the local backup is kept
and the sidecar reports it in `docker logs headscale-easy-backup-remote`.

To restore from the remote, download the archive and follow
[All-in-one → Backing up and restoring](../all-in-one.md#backing-up-and-restoring).

## What was run

`scripts/compose-smoke.sh` runs this overlay with a local directory as the remote: the first backup is
uploaded once, and restarting the sidecar uploads nothing again.

**Not run:** S3, B2, SFTP and rsync over SSH against a real server.
