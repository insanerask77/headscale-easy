#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy: smoke test of two advanced configurations, run as Compose overlays
#
#  Usage: ./scripts/advanced-smoke.sh postgres [16|17|18] [image]
#         ./scripts/advanced-smoke.sh proxy [image]
#         (the image defaults to hse-aio:ci; it is tagged locally as the name compose.yaml uses)
#  Needs Docker with the Compose plugin. Publishes 127.0.0.1:HOST_PORT (18097); SMOKE_DERP_PORT=<port> moves
#  UDP 3478 when something else holds it.
#
#  postgres: advanced/postgres-bundled.yaml with a real PostgreSQL of that version. Fails unless Headscale is
#            healthy on it, the console runs with the read-only role (and that role can read three columns of
#            one table and nothing else), a backup holds the dump and a restore loads it.
#  proxy:    advanced/proxy.yaml behind a real nginx using advanced/proxy/nginx.conf. Fails unless the console
#            answers through the proxy, its cookie is Secure, the activity log shows the real client's address
#            and a forged X-Forwarded-For is ignored.
# =============================================================================
set -uo pipefail

MODE="${1:-}"
case "$MODE" in
    postgres) PGV="${2:-17}"; AIO="${3:-hse-aio:ci}" ;;
    proxy)    AIO="${2:-hse-aio:ci}" ;;
    *) echo "usage: $0 postgres [16|17|18] [image] | $0 proxy [image]"; exit 2 ;;
esac
PORT="${HOST_PORT:-18097}"
TLS_PORT=$((PORT + 1))
DERP="${SMOKE_DERP_PORT:-3478}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
PROJECT="hse-advanced-smoke"
TAG="smoke"
PASSWORD="Advanced-Smoke-Pw-123"
OWNER_PW="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
RO_PW="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
PROXY_NAME="hse-advanced-smoke-nginx"

fail() { echo "FAIL: $1"; docker logs headscale-easy 2>&1 | tail -30; exit 1; }
compose() { docker compose -p "$PROJECT" --project-directory "$WORK" "${FILES[@]}" --env-file "$WORK/.env" "$@"; }
cleanup() {
    docker rm -f "$PROXY_NAME" >/dev/null 2>&1
    compose down -v >/dev/null 2>&1
    rm -rf "$WORK"
}
trap cleanup EXIT

wait_for() {  # wait_for <seconds> <description> <command...>
    local limit="$1" what="$2"; shift 2
    for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "$what"
}
is_healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' headscale-easy 2>/dev/null)" = healthy ]; }
http_code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

docker tag "$AIO" "ghcr.io/insanerask77/headscale-easy:$TAG" || exit 1
mkdir -p "$WORK/advanced/proxy"
cp "$ROOT/compose.yaml" "$WORK/compose.yaml"
cp "$ROOT/advanced/"*.yaml "$WORK/advanced/"

# ------------------------------------------------------------------------------------------------ postgres
if [ "$MODE" = postgres ]; then
    FILES=(-f "$WORK/compose.yaml" -f "$WORK/advanced/postgres-bundled.yaml")
    cat > "$WORK/.env" <<ENV
HSE_PUBLIC_URL=http://localhost:$PORT
HSE_TLS=off
HSE_ADMIN_EMAIL=admin@example.com
HSE_ADMIN_PASSWORD=$PASSWORD
HSE_VERSION=$TAG
HSE_HTTP_PORT=$PORT
HSE_HTTPS_PORT=$TLS_PORT
POSTGRES_VERSION=$PGV
HEADSCALE_PG_PASS=$OWNER_PW
HEADSCALE_PG_RO_PASS=$RO_PW
HSE_DERP_PORT=$DERP
ENV
    echo "== advanced/postgres-bundled.yaml on PostgreSQL $PGV"
    compose up -d >/dev/null 2>&1 || fail "docker compose up failed"
    wait_for 150 "the app did not become healthy on PostgreSQL $PGV" is_healthy
    [ "$(docker exec headscale-easy-postgres psql -U headscale -d headscale -Atc 'show server_version_num' | cut -c1-2)" = "$PGV" ] \
        || fail "the server is not PostgreSQL $PGV"
    logs=$(docker logs headscale-easy 2>&1)
    grep -q "read-only role headscale_ro" <<<"$logs" || fail "the read-only role was not created"
    env=$(docker exec headscale-easy sh -c 'tr "\0" "\n" < /proc/$(pgrep -f web/app.py | head -1)/environ' 2>/dev/null)
    grep -qx 'HEADSCALE_PG_USER=headscale_ro' <<<"$env" || fail "the console is not using the read-only role"
    echo "healthy; the console runs with the read-only role"

    ro() { docker exec -e PGPASSWORD="$RO_PW" headscale-easy-postgres psql -h localhost -U headscale_ro -d headscale -Atc "$1" 2>&1; }
    out=$(ro "select id, host_info, endpoints from nodes limit 1"); ! grep -qi 'error' <<<"$out" || fail "the role cannot read what the console needs: $out"
    out=$(ro "select node_key from nodes limit 1");   grep -qi 'permission denied' <<<"$out" || fail "the role can read node keys: $out"
    out=$(ro "select * from users limit 1");          grep -qi 'permission denied' <<<"$out" || fail "the role can read users: $out"
    out=$(ro "select * from api_keys limit 1");       grep -qi 'permission denied' <<<"$out" || fail "the role can read API keys: $out"
    out=$(ro "update nodes set endpoints = null");    grep -qi 'read-only' <<<"$out" || fail "the role can write: $out"
    echo "the role reads id, host_info and endpoints, and nothing else"

    docker exec headscale-easy headscale users create smoke-pg -c /data/config/config.yaml >/dev/null 2>&1 || fail "could not create the marker user"
    docker exec headscale-easy hse backup >/dev/null 2>&1 || fail "hse backup failed on PostgreSQL $PGV"
    arch=$(docker exec headscale-easy sh -c 'ls -1t /data/backups/headscale-easy-*.tar.gz | head -1')
    members=$(docker exec headscale-easy tar -tzf "$arch")
    grep -q 'headscale/headscale.sql' <<<"$members" || fail "the backup has no headscale.sql"
    docker exec headscale-easy headscale users destroy --name smoke-pg --force -c /data/config/config.yaml >/dev/null 2>&1 || fail "could not delete the marker user"
    docker exec headscale-easy hse restore "$arch" --yes --with-postgres >/dev/null 2>&1 || fail "hse restore --with-postgres failed on PostgreSQL $PGV"
    healthz() { [ "$(http_code "http://localhost:$PORT/console/healthz")" = 200 ]; }
    wait_for 90 "the console did not come back after the restore" healthz
    marker() { local out; out=$(docker exec headscale-easy headscale users list -o json -c /data/config/config.yaml); grep -q smoke-pg <<<"$out"; }
    wait_for 60 "the marker user did not come back after the restore" marker
    echo "backup holds the dump; restore brings the deleted user back"
    echo "PostgreSQL $PGV: OK"
    exit 0
fi

# ---------------------------------------------------------------------------------------------------- proxy
FILES=(-f "$WORK/compose.yaml" -f "$WORK/advanced/proxy.yaml")
# The proxy and the app: the app sees the proxy as the project network's gateway (the proxy runs on the host)
cat > "$WORK/.env" <<ENV
HSE_PUBLIC_URL=https://localhost:$TLS_PORT
HSE_ADMIN_EMAIL=admin@example.com
HSE_ADMIN_PASSWORD=$PASSWORD
HSE_VERSION=$TAG
HSE_HTTP_PORT=$PORT
HSE_TRUSTED_PROXIES=192.0.2.1/32
HSE_DERP_PORT=$DERP
ENV
echo "== advanced/proxy.yaml behind nginx (advanced/proxy/nginx.conf)"
compose up -d >/dev/null 2>&1 || fail "docker compose up failed"
gateway=$(docker network inspect "${PROJECT}_default" --format '{{(index .IPAM.Config 0).Gateway}}')
[ -n "$gateway" ] || fail "no gateway for the project network"
sed -i "s|^HSE_TRUSTED_PROXIES=.*|HSE_TRUSTED_PROXIES=$gateway/32|" "$WORK/.env"
compose up -d >/dev/null 2>&1 || fail "docker compose up (with the proxy's address) failed"
wait_for 120 "the app did not become healthy" is_healthy
ports=$(docker inspect headscale-easy --format '{{json .NetworkSettings.Ports}}')
grep -q '"80/tcp":\[{"HostIp":"127.0.0.1"' <<<"$ports" || fail "port 80 is not published on 127.0.0.1 only: $ports"
grep -q '"443/tcp":\[' <<<"$ports" && fail "port 443 is published: $ports"   # "443/tcp":null is only EXPOSE
echo "healthy; HTTP only on 127.0.0.1:$PORT, 443 not published"

# nginx: the conf.d file of the repo with the domain, the upstream and the certificate swapped for the test
mkdir -p "$WORK/nginx"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$WORK/nginx/key.pem" -out "$WORK/nginx/cert.pem" -days 1 \
    -subj "/CN=localhost" >/dev/null 2>&1 || fail "openssl could not make a certificate"
python3 - "$ROOT/advanced/proxy/nginx.conf" "$WORK/nginx/default.conf" "$PORT" "$TLS_PORT" <<'PY' || fail "could not adapt nginx.conf"
import re, sys
src, dst, port, tls = sys.argv[1:5]
t = open(src, encoding="utf-8").read()
t = t.replace("AIO_HOST:8080", "127.0.0.1:" + port).replace("vpn.example.com", "localhost")
t = re.sub(r"server \{\n    listen 80;.*?\n\}\n\n", "", t, count=1, flags=re.S)       # the http -> https server
t = t.replace("listen 443 ssl;", "listen %s ssl;" % tls).replace("listen [::]:443 ssl;", "")
t = re.sub(r"ssl_certificate\s+\S+;", "ssl_certificate /t/cert.pem;", t)
t = re.sub(r"ssl_certificate_key\s+\S+;", "ssl_certificate_key /t/key.pem;", t)
t = re.sub(r"(access|error)_log\s+\S+;", r"\1_log /dev/stderr;", t)
open(dst, "w", encoding="utf-8").write(t)
PY
docker run -d --name "$PROXY_NAME" --network host -v "$WORK/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$WORK/nginx:/t:ro" nginx:alpine >/dev/null || fail "could not start nginx"
reached() { [ "$(http_code -k "https://localhost:$TLS_PORT/key?v=142")" = 200 ]; }
wait_for 30 "nginx does not reach Headscale (GET /key)" reached
echo "GET /key through the proxy: 200"
location=$(curl -sk -o /dev/null -w '%{redirect_url}' "https://localhost:$TLS_PORT/console")
case "$location" in */console/login*) echo "GET /console: redirects to the sign-in page" ;; *) fail "GET /console did not redirect to the sign-in page ($location)" ;; esac
cookie=$(curl -sk -D - -o /dev/null --data-urlencode "username=admin" --data-urlencode "password=$PASSWORD" \
    -H "X-Forwarded-For: 6.6.6.6" -H "X-Real-IP: 7.7.7.7" "https://localhost:$TLS_PORT/console/login/local" | tr -d '\r' | grep -i '^set-cookie:')
grep -qi 'secure' <<<"$cookie" || fail "the session cookie is not Secure: $cookie"
echo "the session cookie is Secure"
ips=$(docker exec headscale-easy python3 -c "
import sqlite3
c = sqlite3.connect('file:/data/console/audit.db?mode=ro', uri=True)
print(' '.join(sorted({r[0] for r in c.execute('select ip from events')})))")
echo "addresses in the activity log: $ips"
case " $ips " in *" 127.0.0.1 "*) ;; *) fail "the real client's address (127.0.0.1) is not in the activity log: $ips" ;; esac
case "$ips" in *6.6.6.6*|*7.7.7.7*) fail "a forged header reached the activity log: $ips" ;; esac
case " $ips " in *" $gateway "*) fail "the log shows the proxy's address ($gateway), not the client's" ;; esac
echo "the log shows the real client; the forged X-Forwarded-For and X-Real-IP are ignored"
echo "proxy: OK"
