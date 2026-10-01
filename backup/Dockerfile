# Headscale Easy — scheduled backups
# https://github.com/insanerask77/headscale-easy
FROM alpine:3.22

# PostgreSQL clients 16 and 17: pg-client.sh picks the one matching the server,
# since a newer pg_dump writes settings an older server does not know.
RUN apk add --no-cache sqlite postgresql16-client postgresql17-client tar gzip tzdata

# Optional remote copies (BACKUP_REMOTE): rclone covers S3, B2, SFTP...; rsync
# + ssh for a plain server.
RUN apk add --no-cache rclone rsync openssh-client

COPY backup.sh entrypoint.sh pg-client.sh remote.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/backup.sh /usr/local/bin/entrypoint.sh /usr/local/bin/pg-client.sh /usr/local/bin/remote.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
