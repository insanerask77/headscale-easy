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
#    - built-in backups (phase 3) break: `hse backup` does not write a private,
#      verifiable archive, an offline or an online `hse restore` does not bring
#      back what was deleted after the backup, a container started with
#      BACKUP_SCHEDULE="* * * * *" writes no archive within SCHED_WAIT seconds,
#      or RAM goes above MAX_RAM_MB while a backup runs;
#    - setup mode (no env) does not print a token or serve /admin/setup;
#    - the image is above MAX_IMAGE_MB (250) or idle RAM above MAX_RAM_MB (100).
#  Env: MAX_IMAGE_MB, MAX_RAM_MB, IDLE_SECONDS (60), SCHED_WAIT (150), HOST_PORT (18080)
# =============================================================================
set -uo pipefail

IMAGE="${1:-hse-aio:ci}"
MAX_IMAGE_MB="${MAX_IMAGE_MB:-250}"
MAX_RAM_MB="${MAX_RAM_MB:-100}"
IDLE_SECONDS="${IDLE_SECONDS:-60}"
SCHED_WAIT="${SCHED_WAIT:-150}"
PORT="${HOST_PORT:-18080}"
NAME="hse-aio-smoke"
SCHED_NAME="$NAME-sched"

fail() { echo "FAIL: $1"; docker logs "$NAME" 2>&1 | tail -40; exit 1; }
cleanup() {
    docker rm -f "$NAME" "$SCHED_NAME" >/dev/null 2>&1
    docker volume rm -f "$NAME-data" "$SCHED_NAME-data" >/dev/null 2>&1
}
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

is_healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ]; }

# mem_mb <container> -> current RAM in whole MiB, from docker stats
mem_mb() {
    local mem
    mem=$(docker stats --no-stream --format '{{.MemUsage}}' "$1" | awk '{print $1}')
    python3 -c "
import re, sys
n, u = re.match(r'([0-9.]+)([A-Za-z]+)', sys.argv[1]).groups()
print(int(float(n) * {'B': 1/1048576, 'KiB': 1/1024, 'MiB': 1, 'GiB': 1024}[u]))" "$mem"
}

# hs_user_id <name> -> id of that Headscale user, empty when it does not exist
hs_user_id() {
    docker exec "$NAME" headscale users list -o json -c /data/config/config.yaml 2>/dev/null \
        | python3 -c "
import json, sys
try:
    rows = json.load(sys.stdin) or []
except ValueError:
    rows = []
print(next((r['id'] for r in rows if r.get('name') == sys.argv[1]), ''))" "$1"
}

# latest_archive <container> -> path of the newest backup archive inside it
latest_archive() {
    docker exec "$1" sh -c 'ls -1t /data/backups/headscale-easy-*.tar.gz 2>/dev/null | head -1'
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
ram_mb=$(mem_mb "$NAME")
echo "idle RAM: ${ram_mb} MB (limit ${MAX_RAM_MB})"
[ "$ram_mb" -le "$MAX_RAM_MB" ] || fail "idle RAM is ${ram_mb} MB, above ${MAX_RAM_MB} MB"

# --- built-in backups (phase 3) -----------------------------------------------------
echo "== backups"
# 1. `hse backup`, sampling RAM while it runs. The backup is a subprocess of the
#    supervisor, so the container's total is what must stay under the limit.
backup_log=$(mktemp)
docker exec "$NAME" hse backup >"$backup_log" 2>&1 &
backup_pid=$!
peak_mb=0
while kill -0 "$backup_pid" 2>/dev/null; do
    sample=$(mem_mb "$NAME"); [ "$sample" -gt "$peak_mb" ] && peak_mb=$sample
done
wait "$backup_pid" || { cat "$backup_log"; fail "hse backup failed"; }
rm -f "$backup_log"
sample=$(mem_mb "$NAME"); [ "$sample" -gt "$peak_mb" ] && peak_mb=$sample
echo "RAM during backup: ${peak_mb} MB (limit ${MAX_RAM_MB})"
[ "$peak_mb" -le "$MAX_RAM_MB" ] || fail "RAM during a backup is ${peak_mb} MB, above ${MAX_RAM_MB} MB"

# 2. the archive: private, readable, AIO format, with the console's accounts and
#    without sessions (a restored session would revive revoked logins)
archive=$(latest_archive "$NAME")
[ -n "$archive" ] || fail "hse backup wrote no archive in /data/backups"
mode=$(docker exec "$NAME" stat -c %a "$archive")
[ "$mode" = 600 ] || fail "the backup archive is mode $mode, not 600"
dir_mode=$(docker exec "$NAME" stat -c %a /data/backups)
[ "$dir_mode" = 700 ] || fail "/data/backups is mode $dir_mode, not 700"
docker exec -i "$NAME" python3 - "$archive" <<'PY' || fail "the backup archive is not a valid AIO archive"
import hashlib, json, sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as tar:
    members = {m.name: m for m in tar.getmembers()}
    metas = [n for n in members if n.endswith("/meta.json") and n.count("/") == 1]
    assert len(metas) == 1, "no meta.json"
    top = metas[0].split("/")[0]
    meta = json.load(tar.extractfile(members[metas[0]]))
    assert meta["edition"] == "aio" and meta["format"] == 2, meta
    assert "headscale/db.sqlite" in meta["files"], "no Headscale database"
    assert "config/settings.json" in meta["files"], "no settings.json"
    assert "console/accounts.db" in meta["files"], "no console accounts"
    assert not any("sessions.db" in n for n in members), "sessions.db must not be in the archive"
    for rel, digest in meta["files"].items():
        data = tar.extractfile(members[top + "/" + rel]).read()
        assert hashlib.sha256(data).hexdigest() == digest, "sha256 mismatch: " + rel
PY
docker exec "$NAME" test -f /data/backups/status.json || fail "no /data/backups/status.json after a backup"
echo "archive $(basename "$archive"): mode 600, meta.json and sha256 ok, no sessions"

# 3. offline restore: back up with a marker user, delete it, restore into the
#    stopped volume, start again and expect the user back
docker exec "$NAME" headscale users create smoke-restore -c /data/config/config.yaml >/dev/null 2>&1 \
    || fail "could not create the marker user"
docker exec "$NAME" hse backup >/dev/null 2>&1 || fail "hse backup (with the marker user) failed"
archive=$(latest_archive "$NAME")
uid=$(hs_user_id smoke-restore)
[ -n "$uid" ] || fail "the marker user is not listed"
docker exec "$NAME" headscale users destroy --identifier "$uid" --force -c /data/config/config.yaml >/dev/null 2>&1 \
    || docker exec "$NAME" headscale users destroy --name smoke-restore --force -c /data/config/config.yaml >/dev/null 2>&1 \
    || fail "could not delete the marker user"
[ -z "$(hs_user_id smoke-restore)" ] || fail "the marker user is still there after deleting it"
docker stop "$NAME" >/dev/null || fail "could not stop the container"
docker run --rm -v "$NAME-data:/data" --entrypoint hse "$IMAGE" restore "$archive" --yes \
    || fail "offline hse restore failed"
docker start "$NAME" >/dev/null || fail "could not start the container after the restore"
wait_for 90 "container did not become healthy after the offline restore" is_healthy "$NAME"
[ -n "$(hs_user_id smoke-restore)" ] || fail "offline restore did not bring the marker user back"
[ "$(http_code /admin/healthz)" = 200 ] || fail "/admin/healthz did not answer 200 after the offline restore"
echo "offline restore: ok"

# 4. online restore: the same, through the running container
docker exec "$NAME" headscale users destroy --identifier "$(hs_user_id smoke-restore)" --force -c /data/config/config.yaml >/dev/null 2>&1 \
    || docker exec "$NAME" headscale users destroy --name smoke-restore --force -c /data/config/config.yaml >/dev/null 2>&1 \
    || fail "could not delete the marker user (online round)"
[ -z "$(hs_user_id smoke-restore)" ] || fail "the marker user is still there (online round)"
docker exec "$NAME" hse restore "$archive" --yes || fail "online hse restore failed"
# docker's health status is still the pre-restore "healthy" for up to one interval: wait for the console itself
healthz_ok() { [ "$(http_code /admin/healthz)" = 200 ]; }
wait_for 90 "/admin/healthz did not answer 200 after the online restore" healthz_ok
wait_for 90 "container is not healthy after the online restore" is_healthy "$NAME"
[ -n "$(hs_user_id smoke-restore)" ] || fail "online restore did not bring the marker user back"
[ "$(http_code /admin/healthz)" = 200 ] || fail "/admin/healthz did not answer 200 after the online restore"
echo "online restore: ok"
cleanup

# 5. the schedule, in the real image with its real time zone: one archive per minute
echo "== scheduled backup (BACKUP_SCHEDULE='* * * * *', up to ${SCHED_WAIT}s)"
docker run -d --name "$SCHED_NAME" -v "$SCHED_NAME-data:/data" \
    -e HSE_PUBLIC_URL="http://localhost:$PORT" -e HSE_TLS=off \
    -e HSE_ADMIN_EMAIL=admin@example.com -e HSE_ADMIN_PASSWORD="$admin_pw" \
    -e BACKUP_SCHEDULE="* * * * *" "$IMAGE" >/dev/null || { NAME="$SCHED_NAME"; fail "could not start the scheduled-backup container"; }
NAME="$SCHED_NAME"
wait_for 90 "the scheduled-backup container did not become healthy" is_healthy "$NAME"
wait_for "$SCHED_WAIT" "no scheduled backup was written within ${SCHED_WAIT}s" \
    bash -c "[ -n \"\$(docker exec $NAME sh -c 'ls /data/backups/headscale-easy-*.tar.gz 2>/dev/null | head -1')\" ]"
echo "scheduled backup: ok ($(basename "$(latest_archive "$NAME")"))"
NAME="hse-aio-smoke"
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
