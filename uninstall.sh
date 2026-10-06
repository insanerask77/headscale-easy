#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy: uninstaller for the all-in-one image
#  https://github.com/insanerask77/headscale-easy
#
#    ./uninstall.sh [--dir DIR]            stop and remove the container; the data stays
#    ./uninstall.sh [--dir DIR] --purge    also delete the volumes: users, devices, keys,
#                                          certificates AND the backups (IRREVERSIBLE)
#    --yes skips the question of --purge.
# =============================================================================
set -euo pipefail

DIR="${HSE_DIR:-./headscale-easy}"; PURGE=false; YES=false
INPUT="${HSE_INSTALL_INPUT:-/dev/tty}"

die() { printf 'x %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) [[ $# -ge 2 ]] || die "--dir needs a directory"; DIR="$2"; shift ;;
        --purge) PURGE=true ;;
        --yes|-y) YES=true ;;
        --help|-h) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,2\}//'; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[[ -f "$DIR/docker-compose.yml" ]] || die "no installation in $DIR (--dir)."
if [[ -f "$DIR/headscale-config.yaml" ]] || grep -qE '^(SSL_MODE|AUTH_PROVIDER)=' "$DIR/.env" 2>/dev/null; then
    die "$DIR holds a 1.x installation, which this script does not manage"
fi

if $PURGE && ! $YES; then
    printf 'This deletes every user, device, key, certificate and backup of %s.\n' "$DIR"
    answer=""
    if [[ -r "$INPUT" ]]; then read -r -p "Type DELETE to continue: " answer < "$INPUT" || true; fi
    [[ "$answer" == DELETE ]] || die "cancelled"
fi

cd "$DIR"
if $PURGE; then docker compose --profile '*' down -v; else docker compose --profile '*' down; fi
if $PURGE; then
    echo "Removed, with the data. The folder $DIR (compose file and .env) is still there: delete it if you want."
else
    echo "Removed. The data is kept in the Docker volumes: run the installer again to start it, or --purge to delete it."
fi
