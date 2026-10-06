#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy: smoke test of deploy/compose (the advanced edition's compose file)
#
#  Usage: ./scripts/compose-smoke.sh [aio-image] [backup-image]
#         (defaults: hse-aio:ci and hse-backup:ci; tagged locally as the names the compose file uses)
#  Needs Docker with the Compose plugin; publishes host ports HOST_PORT (18095) and 3478/udp.
#  Fails when, with the file as shipped (no capability, no-new-privileges, named volumes):
#    - the stack is not healthy or /admin/healthz does not answer;
#    - `hse backup` cannot write into the named backups volume;
#    - with --profile backup-remote and a local-directory remote, the first backup is not uploaded
#      once, or a restart of the sidecar uploads it again.
# =============================================================================
set -uo pipefail

AIO="${1:-hse-aio:ci}"
BACKUP="${2:-hse-backup:ci}"
PORT="${HOST_PORT:-18095}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/deploy/compose"
WORK="$(mktemp -d)"
DEST="$WORK/remote-dest"
PROJECT="hse-compose-smoke"
TAG="smoke"

fail() { echo "FAIL: $1"; docker compose -p "$PROJECT" -f "$WORK/docker-compose.yml" -f "$WORK/override.yml" --profile backup-remote logs --tail 40 2>&1 | tail -60; exit 1; }
cleanup() {
    docker compose -p "$PROJECT" -f "$WORK/docker-compose.yml" -f "$WORK/override.yml" --profile backup-remote down -v >/dev/null 2>&1
    rm -rf "$WORK"
}
trap cleanup EXIT

wait_for() {  # wait_for <seconds> <description> <command...>
    local limit="$1" what="$2"; shift 2
    for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "$what"
}

# The compose file names the published images: tag the local builds as them
docker tag "$AIO" "ghcr.io/insanerask77/headscale-easy-aio:$TAG" || exit 1
docker tag "$BACKUP" "ghcr.io/insanerask77/headscale-easy-backup:$TAG" || exit 1

mkdir -p "$DEST" "$WORK/remote-config"
chmod 777 "$DEST"   # the sidecar runs as root without CAP_DAC_OVERRIDE for new files: keep the check simple
cp "$SRC/docker-compose.yml" "$WORK/docker-compose.yml"
cat > "$WORK/override.yml" <<YAML
services:
  backup-remote:
    volumes:
      - $DEST:/dest
YAML
cat > "$WORK/.env" <<ENV
HSE_PUBLIC_URL=http://localhost:$PORT
HSE_TLS=off
HSE_ADMIN_EMAIL=admin@example.com
HSE_ADMIN_PASSWORD=Compose-Smoke-Pw-123
HSE_VERSION=$TAG
HSE_HTTP_PORT=$PORT
HSE_HTTPS_PORT=18443
HSE_REMOTE_CONFIG=$WORK/remote-config
BACKUP_REMOTE=:local:/dest
BACKUP_SYNC_INTERVAL=1
ENV
COMPOSE=(docker compose -p "$PROJECT" -f "$WORK/docker-compose.yml" -f "$WORK/override.yml" --profile backup-remote)

is_healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' headscale-easy-aio 2>/dev/null)" = healthy ]; }
healthz() { [ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/admin/healthz")" = 200 ]; }
uploaded() { [ "$(find "$DEST" -name 'headscale-easy-*.tar.gz' | wc -l)" -ge 1 ]; }

echo "== up (no capability, named volumes)"
"${COMPOSE[@]}" up -d >/dev/null 2>&1 || fail "docker compose up failed"
wait_for 120 "the stack did not become healthy" is_healthy
wait_for 30 "/admin/healthz did not answer 200" healthz
echo "healthy, /admin/healthz ok"

caps=$(docker inspect headscale-easy-aio --format '{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}')
[[ "$caps" == *ALL* && "$caps" == *no-new-privileges* ]] || fail "the container is not hardened as the compose file says ($caps)"
echo "hardened: $caps"

echo "== backup into the named volume"
docker exec headscale-easy-aio hse backup >/dev/null 2>&1 || fail "hse backup failed in the named backups volume"
[ "$(docker exec headscale-easy-aio sh -c 'ls /data/backups/headscale-easy-*.tar.gz | wc -l')" -ge 1 ] || fail "no archive in /data/backups"
echo "archive written by uid $(docker exec headscale-easy-aio id -u)"

echo "== backup-remote: uploaded once, not again after a restart"
wait_for 150 "the sidecar did not upload the backup" uploaded
first=$(find "$DEST" -name 'headscale-easy-*.tar.gz' | sort)
"${COMPOSE[@]}" restart backup-remote >/dev/null 2>&1 || fail "could not restart the sidecar"
sleep 75   # more than one BACKUP_SYNC_INTERVAL
[ "$(find "$DEST" -name 'headscale-easy-*.tar.gz' | sort)" = "$first" ] || fail "the sidecar changed the remote after a restart"
docker logs headscale-easy-backup-remote 2>&1 | awk '/uploaded/ {n++} END {exit !(n == 1)}' || fail "the sidecar logged more than one upload"
echo "one upload, none after the restart"

echo "OK"
