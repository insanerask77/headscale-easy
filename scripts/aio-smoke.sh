#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — smoke test of the all-in-one image
#
#  Usage: ./scripts/aio-smoke.sh [image]        (default: hse-aio:ci)
#  Needs Docker and python3. Starts the image twice and fails when:
#    - headless mode (HSE_PUBLIC_URL + admin env) is not healthy, /healthz or
#      /admin/healthz do not answer, or a pre-auth key cannot be created;
#    - the rendered config does not use the embedded DERP by default, the
#      container does not answer STUN on 3478/udp, sign-up is not off
#      (/admin/signup must be 404) or the event stream does not require a session;
#    - setup mode (no env) does not print a token or serve /admin/setup;
#    - the image is above MAX_IMAGE_MB (250) or idle RAM above MAX_RAM_MB (100).
#  Env: MAX_IMAGE_MB, MAX_RAM_MB, IDLE_SECONDS (60), HOST_PORT (18080)
# =============================================================================
set -uo pipefail

IMAGE="${1:-hse-aio:ci}"
MAX_IMAGE_MB="${MAX_IMAGE_MB:-250}"
MAX_RAM_MB="${MAX_RAM_MB:-100}"
IDLE_SECONDS="${IDLE_SECONDS:-60}"
PORT="${HOST_PORT:-18080}"
NAME="hse-aio-smoke"

fail() { echo "FAIL: $1"; docker logs "$NAME" 2>&1 | tail -40; exit 1; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1; docker volume rm -f "$NAME-data" >/dev/null 2>&1; }
trap cleanup EXIT
cleanup

# http_code <path> -> status code, redirects not followed
http_code() {
    python3 - "$1" <<PY
import sys, urllib.request, urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
try:
    print(urllib.request.build_opener(NoRedirect).open("http://127.0.0.1:$PORT" + sys.argv[1], timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
except OSError:
    print(0)
PY
}

wait_for() {  # wait_for <seconds> <description> <command...>
    local limit="$1" what="$2"; shift 2
    for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "$what"
}

# --- image size -----------------------------------------------------------------
size_mb=$(( $(docker image inspect "$IMAGE" --format '{{.Size}}') / 1000000 ))
echo "image size: ${size_mb} MB (limit ${MAX_IMAGE_MB})"
[ "$size_mb" -le "$MAX_IMAGE_MB" ] || fail "image is ${size_mb} MB, above ${MAX_IMAGE_MB} MB"

# --- headless mode ----------------------------------------------------------------
echo "== headless mode"
admin_pw="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
docker run -d --name "$NAME" -p "$PORT:80" -v "$NAME-data:/data" \
    -e HSE_PUBLIC_URL="http://localhost:$PORT" -e HSE_TLS=off \
    -e HSE_ADMIN_EMAIL=admin@example.com -e HSE_ADMIN_PASSWORD="$admin_pw" "$IMAGE" >/dev/null \
    || fail "could not start the container"
wait_for 90 "container did not become healthy" \
    bash -c "[ \"\$(docker inspect -f '{{.State.Health.Status}}' $NAME)\" = healthy ]"
[ "$(http_code /healthz)" = 200 ] || fail "/healthz did not answer 200"
[ "$(http_code /admin/healthz)" = 200 ] || fail "/admin/healthz did not answer 200"
docker exec "$NAME" hse health || fail "hse health"
# the admin's Headscale user is user 1: create a pre-auth key for it
key=$(docker exec "$NAME" headscale preauthkeys create --user 1 --expiration 1h -c /data/config/config.yaml 2>&1 | tail -1)
[ -n "$key" ] && ! grep -qiE 'error|fail' <<<"$key" || fail "could not create a pre-auth key: $key"
echo "pre-auth key created"

# --- defaults of phase 2.5 -------------------------------------------------------------
cfg=/data/config/config.yaml
docker exec "$NAME" grep -q '^  urls: \[\]' "$cfg" || fail "DERP is not embedded-only by default (derp.urls is not empty)"
docker exec "$NAME" grep -q 'enabled: true' "$cfg" || fail "the embedded DERP server is not enabled"
docker exec "$NAME" grep -q 'base_domain: hse.net' "$cfg" || fail "the default MagicDNS base domain is not hse.net"
# STUN: a binding request with the FINGERPRINT attribute Tailscale's server insists on
docker exec -i "$NAME" python3 - <<'PY' || fail "the container does not answer STUN on 3478/udp"
import os, socket, struct, zlib
tx, sw = os.urandom(12), b"tailnode"
attrs = struct.pack("!HH", 0x8022, len(sw)) + sw
head = lambda n: struct.pack("!HHI", 1, n, 0x2112A442) + tx
crc = (zlib.crc32(head(len(attrs) + 8) + attrs) & 0xFFFFFFFF) ^ 0x5354554E
msg = head(len(attrs) + 8) + attrs + struct.pack("!HHI", 0x8028, 4, crc)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.sendto(msg, ("127.0.0.1", 3478))
assert s.recv(512)[:2] == b"\x01\x01"
PY
echo "embedded DERP, STUN and base domain: ok"
[ "$(http_code /admin/signup)" = 404 ] || fail "sign-up is not off by default (/admin/signup should answer 404)"
[ "$(http_code /admin/events)" = 401 ] || fail "/admin/events answered without a session"
echo "sign-up off, event stream needs a session: ok"

echo "idle for ${IDLE_SECONDS}s, then RAM"
sleep "$IDLE_SECONDS"
mem=$(docker stats --no-stream --format '{{.MemUsage}}' "$NAME" | awk '{print $1}')
ram_mb=$(python3 -c "
import re, sys
n, u = re.match(r'([0-9.]+)([A-Za-z]+)', '$mem').groups()
print(int(float(n) * {'B': 1/1048576, 'KiB': 1/1024, 'MiB': 1, 'GiB': 1024}[u]))")
echo "idle RAM: ${ram_mb} MB (limit ${MAX_RAM_MB})"
[ "$ram_mb" -le "$MAX_RAM_MB" ] || fail "idle RAM is ${ram_mb} MB, above ${MAX_RAM_MB} MB"
cleanup

# --- setup mode ---------------------------------------------------------------------
echo "== setup mode"
docker run -d --name "$NAME" -p "$PORT:80" -v "$NAME-data:/data" "$IMAGE" >/dev/null || fail "could not start the container"
wait_for 60 "setup mode did not become healthy" \
    bash -c "[ \"\$(docker inspect -f '{{.State.Health.Status}}' $NAME)\" = healthy ]"
token=$(docker exec "$NAME" cat /data/config/setup-token)
[ -n "$token" ] || fail "no setup token"
docker logs "$NAME" 2>&1 | grep -qF -e "$token" || fail "the setup token is not in the logs"
[ "$(http_code /admin/setup)" = 200 ] || fail "/admin/setup did not answer 200"
[ "$(http_code /admin/setup/language)" = 403 ] || fail "a wizard step answered without the token"
[ "$(http_code /admin/machines)" = 302 ] || fail "the console is reachable in setup mode"
echo "OK"
