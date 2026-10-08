#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy: smoke test of the Compose app (compose.yaml) and of the remote backup add-on
#
#  Usage: ./scripts/compose-smoke.sh [image] [backup-image]
#         (defaults: hse-aio:ci and hse-backup:ci; tagged locally as the names the compose files use)
#  Needs Docker with the Compose plugin; publishes host ports HOST_PORT (18095), 18443 and 3478/udp
#  (SMOKE_DERP_PORT=<port> publishes another UDP port when 3478 is taken).
#  Fails when, with the files as shipped (no capability, no-new-privileges, named volumes):
#    - the app is not healthy or /console/healthz does not answer;
#    - `hse backup` cannot write into the named backups volume;
#    - `docker compose down && up -d` loses the data (the administrator can no longer sign in);
#    - with advanced/backup-remote.yaml and a local-directory remote, the first backup is not
#      uploaded once, or a restart of the sidecar uploads it again.
# =============================================================================
set -uo pipefail

AIO="${1:-hse-aio:ci}"
BACKUP="${2:-hse-backup:ci}"
PORT="${HOST_PORT:-18095}"
DERP="${SMOKE_DERP_PORT:-3478}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
DEST="$WORK/remote-dest"
PROJECT="hse-compose-smoke"
TAG="smoke"
PASSWORD="Compose-Smoke-Pw-123"

# The sidecar overlay is the one in advanced/; only the remote's folder is added for the test
COMPOSE=(docker compose -p "$PROJECT" -f "$WORK/compose.yaml")
BOTH=(docker compose -p "$PROJECT" -f "$WORK/compose.yaml" -f "$WORK/advanced/backup-remote.yaml" -f "$WORK/override.yml")

fail() { echo "FAIL: $1"; "${BOTH[@]}" logs --tail 40 2>&1 | tail -60; exit 1; }
cleanup() {
    "${BOTH[@]}" down -v >/dev/null 2>&1
    rm -rf "$WORK"
}
trap cleanup EXIT

wait_for() {  # wait_for <seconds> <description> <command...>
    local limit="$1" what="$2"; shift 2
    for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "$what"
}

# The compose files name the published images: tag the local builds as them
docker tag "$AIO" "ghcr.io/insanerask77/headscale-easy:$TAG" || exit 1
docker tag "$BACKUP" "ghcr.io/insanerask77/headscale-easy-backup:$TAG" || exit 1

mkdir -p "$DEST" "$WORK/remote-config" "$WORK/advanced"
chmod 777 "$DEST"   # the sidecar runs as root without CAP_DAC_OVERRIDE for new files: keep the check simple
cp "$ROOT/compose.yaml" "$WORK/compose.yaml"
cp "$ROOT/advanced/backup-remote.yaml" "$WORK/advanced/backup-remote.yaml"
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
HSE_ADMIN_PASSWORD=$PASSWORD
HSE_VERSION=$TAG
HSE_DERP_PORT=$DERP
HSE_HTTP_PORT=$PORT
HSE_HTTPS_PORT=18443
HSE_REMOTE_CONFIG=$WORK/remote-config
BACKUP_REMOTE=:local:/dest
BACKUP_SYNC_INTERVAL=1
ENV

is_healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' headscale-easy 2>/dev/null)" = healthy ]; }
healthz() { [ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/console/healthz")" = 200 ]; }
uploaded() { [ "$(find "$DEST" -name 'headscale-easy-*.tar.gz' | wc -l)" -ge 1 ]; }
# the administrator the first start created can sign in (the data survived)
signin_code() {
    curl -s -o /dev/null -w '%{http_code}' --data-urlencode "username=admin" \
        --data-urlencode "password=$PASSWORD" "http://localhost:$PORT/console/login/local"
}
signs_in() { case "$(signin_code)" in 302|303) return 0 ;; *) return 1 ;; esac; }

echo "== up: docker compose up -d (compose.yaml, no capability, named volumes)"
"${COMPOSE[@]}" --env-file "$WORK/.env" up -d >/dev/null 2>&1 || fail "docker compose up failed"
wait_for 120 "the app did not become healthy" is_healthy
wait_for 30 "/console/healthz did not answer 200" healthz
echo "healthy, /console/healthz ok"

caps=$(docker inspect headscale-easy --format '{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}')
[[ "$caps" == *ALL* && "$caps" == *no-new-privileges* ]] || fail "the container is not hardened as the compose file says ($caps)"
echo "hardened: $caps"
[ "$(docker inspect headscale-easy --format '{{range .Mounts}}{{.Source}} {{end}}' | grep -c docker.sock)" = 0 ] || fail "the container mounts the Docker socket"

signs_in || fail "the administrator from HSE_ADMIN_EMAIL cannot sign in (HTTP $(signin_code))"
echo "the administrator signs in"

echo "== backup into the named volume"
docker exec headscale-easy hse backup >/dev/null 2>&1 || fail "hse backup failed in the named backups volume"
[ "$(docker exec headscale-easy sh -c 'ls /data/backups/headscale-easy-*.tar.gz | wc -l')" -ge 1 ] || fail "no archive in /data/backups"
echo "archive written by uid $(docker exec headscale-easy id -u)"

echo "== down && up -d keeps the data"
"${COMPOSE[@]}" --env-file "$WORK/.env" down >/dev/null 2>&1 || fail "docker compose down failed"
"${COMPOSE[@]}" --env-file "$WORK/.env" up -d >/dev/null 2>&1 || fail "docker compose up failed after down"
wait_for 120 "the app did not become healthy after down && up" is_healthy
wait_for 30 "/console/healthz did not answer after down && up" healthz
[ "$(docker exec headscale-easy sh -c 'ls /data/backups/headscale-easy-*.tar.gz | wc -l')" -ge 1 ] || fail "the backup is gone after down && up"
signs_in || fail "the administrator cannot sign in after down && up (the data was lost)"
echo "the backup is there and the administrator signs in"

echo "== advanced/backup-remote.yaml: uploaded once, not again after a restart"
"${BOTH[@]}" up -d >/dev/null 2>&1 || fail "could not add the backup-remote overlay"
wait_for 150 "the sidecar did not upload the backup" uploaded
first=$(find "$DEST" -name 'headscale-easy-*.tar.gz' | sort)
"${BOTH[@]}" restart backup-remote >/dev/null 2>&1 || fail "could not restart the sidecar"
sleep 75   # more than one BACKUP_SYNC_INTERVAL
[ "$(find "$DEST" -name 'headscale-easy-*.tar.gz' | sort)" = "$first" ] || fail "the sidecar changed the remote after a restart"
docker logs headscale-easy-backup-remote 2>&1 | awk '/uploaded/ {n++} END {exit !(n == 1)}' || fail "the sidecar logged more than one upload"
echo "one upload, none after the restart"

echo "OK"
