# Headscale Easy — remote copies of the backups
# https://github.com/insanerask77/headscale-easy
FROM alpine:3.22

# rclone covers S3, B2, SFTP...; rsync + ssh for a plain server (BACKUP_REMOTE).
RUN apk add --no-cache rclone rsync openssh-client tzdata

COPY entrypoint.sh remote.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/remote.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
