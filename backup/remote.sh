#!/bin/sh
# Remote copies of the backups (S3, B2, SFTP... through rclone, or rsync over SSH).
#
#   remote.sh push  <file>          upload one backup archive
#   remote.sh prune                 delete remote backups older than the retention
#   remote.sh fetch <spec> <dir>    download one backup into <dir> (make restore)
#   remote.sh list                  names of the backups already on the remote
#   remote.sh sync <dir>            push every backup in <dir> that the remote lacks,
#                                   then prune (BACKUP_MODE=sync sidecar)
#
# BACKUP_REMOTE chooses the destination:
#   remote:path             an rclone remote, e.g. s3:my-bucket/headscale-easy
#                           (define it in data/backup-remote/rclone.conf, or inline
#                           with AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY:
#                           :s3,provider=AWS,env_auth=true,region=eu-west-1:bucket/dir)
#   rsync:[user@]host:/dir  rsync over SSH, key in data/backup-remote/id_ed25519
#                           (optional known_hosts next to it; BACKUP_REMOTE_SSH_PORT)
# BACKUP_REMOTE_KEEP_DAYS is the remote retention (default: BACKUP_KEEP_DAYS).
set -eu

CONF_DIR="${BACKUP_REMOTE_CONFIG_DIR:-/remote-config}"
REMOTE="${BACKUP_REMOTE:-}"
KEEP_DAYS="${BACKUP_REMOTE_KEEP_DAYS:-${BACKUP_KEEP_DAYS:-14}}"
PATTERN='headscale-easy-*.tar.gz'

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"; }
die() { log "remote: $*"; exit 1; }

if [ -f "$CONF_DIR/rclone.conf" ]; then export RCLONE_CONFIG="$CONF_DIR/rclone.conf"; fi

# Digits only: it ends up in a remote shell command
case "$KEEP_DAYS" in ''|*[!0-9]*) die "BACKUP_REMOTE_KEEP_DAYS must be a number of days" ;; esac

is_rsync() { case "$1" in rsync:*) return 0 ;; *) return 1 ;; esac; }

# rsync:user@host:/dir -> user@host and /dir
rsync_host() { s="${1#rsync:}"; echo "${s%%:*}"; }
rsync_dir()  { s="${1#rsync:}"; echo "${s#*:}"; }

ssh_cmd() {
    cmd="ssh -p ${BACKUP_REMOTE_SSH_PORT:-22} -o BatchMode=yes"
    if [ -f "$CONF_DIR/id_ed25519" ]; then cmd="$cmd -i $CONF_DIR/id_ed25519"; fi
    if [ -f "$CONF_DIR/known_hosts" ]; then
        cmd="$cmd -o UserKnownHostsFile=$CONF_DIR/known_hosts -o StrictHostKeyChecking=yes"
    else
        cmd="$cmd -o UserKnownHostsFile=/tmp/known_hosts -o StrictHostKeyChecking=accept-new"
    fi
    echo "$cmd"
}

check_rsync_dir() {
    case "$1" in
        /*[!A-Za-z0-9_./-]*|'') die "the rsync directory may only contain letters, digits and _ . / -" ;;
        /*) ;;
        *) die "the rsync directory must be an absolute path (rsync:user@host:/dir)" ;;
    esac
}

push() {
    file="$1"
    [ -f "$file" ] || die "no such file: $file"
    if is_rsync "$REMOTE"; then
        dir=$(rsync_dir "$REMOTE"); check_rsync_dir "$dir"
        rsync -e "$(ssh_cmd)" --chmod=F600 "$file" "$(rsync_host "$REMOTE"):$dir/" || return 1
    else
        # explicit: under "if push ..." (sync) set -e is off, and a failed upload must not log success
        rclone copyto "$file" "${REMOTE%/}/$(basename "$file")" || return 1
    fi
    log "uploaded $(basename "$file") to $REMOTE"
}

prune() {
    if is_rsync "$REMOTE"; then
        dir=$(rsync_dir "$REMOTE"); check_rsync_dir "$dir"
        # shellcheck disable=SC2046  # ssh_cmd is a word list on purpose
        $(ssh_cmd) "$(rsync_host "$REMOTE")" \
            "find '$dir' -maxdepth 1 -name '$PATTERN' -mtime +$KEEP_DAYS -print -delete" \
            | sed 's|.*/|removed old remote backup: |'
    else
        rclone delete "$REMOTE" --include "$PATTERN" --min-age "${KEEP_DAYS}d" -v 2>&1 \
            | sed -n 's|.*INFO *: *\(.*\): Deleted.*|removed old remote backup: \1|p'
    fi
}

fetch() {
    spec="$1"; out="$2"
    [ -d "$out" ] || die "no such directory: $out"
    if is_rsync "$spec"; then
        dir=$(rsync_dir "$spec"); check_rsync_dir "$dir"
        rsync -e "$(ssh_cmd)" "$(rsync_host "$spec"):$dir" "$out/"
    else
        rclone copyto "$spec" "$out/$(basename "$spec")"
    fi
}

list() {
    if is_rsync "$REMOTE"; then
        dir=$(rsync_dir "$REMOTE"); check_rsync_dir "$dir"
        # shellcheck disable=SC2046  # ssh_cmd is a word list on purpose
        $(ssh_cmd) "$(rsync_host "$REMOTE")" "find '$dir' -maxdepth 1 -name '$PATTERN' -print" | sed 's|.*/||'
    else
        rclone lsf "$REMOTE" --include "$PATTERN" | sed 's|.*/||'
    fi
}

# Idempotent: what is already pushed comes from the remote listing, not from a
# local state file the sidecar could lose. A failed push is retried on the next
# pass and does not stop the others. In-progress (.part) archives are skipped
# (the pattern does not match them) and so are the pre-restore safety copies.
sync() {
    dir="$1"
    [ -d "$dir" ] || die "no such directory: $dir"
    have=$(list) || die "could not list the remote"
    failed=0
    for f in "$dir"/$PATTERN; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        case "$name" in *-pre-restore-*) continue ;; esac
        if printf '%s\n' "$have" | grep -qxF "$name"; then continue; fi
        if push "$f"; then :; else log "remote: push of $name FAILED, will retry"; failed=1; fi
    done
    prune || { log "remote: prune FAILED"; failed=1; }
    return "$failed"
}

action="${1:-}"
[ -n "$action" ] || die "usage: remote.sh push <file> | prune | fetch <spec> <dir> | list | sync <dir>"
shift
case "$action" in
    push|prune|list|sync) [ -n "$REMOTE" ] || die "BACKUP_REMOTE is not set" ;;
esac
case "$action" in
    list)  list ;;
    sync)  sync "${1:?dir}" ;;
    push)  push "${1:?file}" ;;
    prune) prune ;;
    fetch) fetch "${1:?spec}" "${2:?dir}" ;;
    *) die "unknown action: $action" ;;
esac
