#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — end-to-end smoke test of the Docker tab snippets
#
#  Usage: ./scripts/docker-tab-smoke.sh [image]        (default: hse-aio:ci)
#  Needs Docker, python3, /dev/net/tun on the host and permission to give
#  containers NET_ADMIN. The unit tests only compare the text of the snippets;
#  this runs them: the all-in-one image on a Docker network (HSE_TLS=off, the
#  network alias "hse" for the server), the snippets generated through the
#  console's own "Add device > Docker" form with a local admin, run as printed,
#  and a client container that uses what they advertise. It fails when:
#    - an exit node deployed from the snippet is not advertised, cannot be
#      approved, or a client that selects it does not get traffic through it
#      (target reached, tailscale0 rx > 0 on the client, HTTPS over the Internet);
#    - an exit node added later (same state volume, new snippet, spent key) is
#      not advertised after the container is recreated;
#    - a subnet route is not advertised, or a client with --accept-routes does
#      not route to it;
#    - a userspace exit node (no /dev/net/tun) does not carry traffic;
#    - a single-use key that is already spent logs the container out when it is
#      recreated or restarted (TS_AUTH_ONCE);
#    - taking NET_ADMIN or the forwarding sysctls out of the exit node snippet
#      no longer breaks it (the lines are there for a reason), or the sysctls
#      are not applied in the container when present.
#  Env: HOST_PORT (18090), TS_IMAGE (image written in the snippets, default as
#  generated: tailscale/tailscale:latest), SMOKE_HTTPS_URL (https://example.com: the
#  Internet site the client fetches through the exit node, so the runner needs Internet access), HSE_SMOKE_KEEP=1 (leave the containers).
#  In GitHub Actions each case also goes to $GITHUB_STEP_SUMMARY.
# =============================================================================
set -uo pipefail

IMAGE="${1:-hse-aio:ci}"
PORT="${HOST_PORT:-18090}"
HTTPS_URL="${SMOKE_HTTPS_URL:-https://example.com}"
P="hse-dtsm"                 # prefix of everything this script creates
SRV="$P-server"; CLIENT="$P-client"; TARGET="$P-target"
NET="$P-net"; LAN="$P-lan"; LAN_SUBNET="11.99.0.0/24"; TARGET_IP="11.99.0.10"
TOKEN="dtab-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
ADMIN_PW="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
HSC=(headscale -c /data/config/config.yaml)
WORK="$(mktemp -d)"
CASES=()

dump() {
    local c
    for c in "$SRV" "$CLIENT" $(docker ps -a --format '{{.Names}}' | grep "^tailscale-dts-" || true); do
        echo "--- docker logs $c (tail)"; docker logs "$c" 2>&1 | tail -25
    done
}
fail() { echo "FAIL: $1"; dump; exit 1; }
cleanup() {
    local c
    for c in $(docker ps -a --format '{{.Names}}' | grep -E "^(tailscale-dts-|$P-)" || true); do docker rm -f "$c" >/dev/null 2>&1; done
    for c in $(docker volume ls -q | grep -E "^(tailscale-dts-|$P-)" || true); do docker volume rm -f "$c" >/dev/null 2>&1; done
    docker network rm "$NET" "$LAN" >/dev/null 2>&1
    rm -rf "$WORK"
}
trap '[ "${HSE_SMOKE_KEEP:-0}" = 1 ] || cleanup' EXIT
cleanup
WORK="$(mktemp -d)"

summary() { if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then printf '%s\n' "$@" >> "$GITHUB_STEP_SUMMARY"; fi; return 0; }
ok() { echo "ok: $1"; CASES+=("$1"); summary "- $1"; }

wait_for() {  # wait_for <seconds> <description> <command...>
    local limit="$1" what="$2"; shift 2
    for _ in $(seq 1 "$limit"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "$what"
}

# --- the console's form, driven over HTTP ---------------------------------------
# ui <hostname> <exit 0|1> <routes> <userspace 0|1> <generate 0|1>  -> the `docker run` snippet on stdout
ui() {
    HSE_URL="http://127.0.0.1:$PORT/admin" HSE_PW="$ADMIN_PW" python3 - "$@" <<'PY'
import html, http.cookiejar, re, sys, urllib.error, urllib.parse, urllib.request
host, exit_, routes, userspace, generate = sys.argv[1:6]
base = __import__("os").environ["HSE_URL"]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect)
def send(path, data=None):
    try:
        r = op.open(base + path, urllib.parse.urlencode(data).encode() if data is not None else None, timeout=20)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
code, _ = send("/login/local", {"username": "admin", "password": __import__("os").environ["HSE_PW"]})
assert code == 303, f"sign-in answered {code}"
code, page = send("/add")
assert code == 200, f"/add answered {code}"
csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
form = {"csrf": csrf, "hostname": host, "routes": routes, "days": "1"}
owner = re.search(r'<select name="user_id">\s*<option value="(\d+)"', page)
if owner: form["user_id"] = owner.group(1)
for k, v in (("exit", exit_), ("userspace", userspace), ("generate", generate)):
    if v == "1": form[k] = "1"
code, page = send("/add/docker", form)
assert code == 200, f"/add/docker answered {code}: {html.unescape(' '.join(re.findall(r'class="notice[^>]*>([^<]*)<', page)))}"
blocks = re.findall(r'<code class="pre">(.*?)</code>', page, re.S)
assert len(blocks) >= 2, "the page has no snippets"
run, compose = html.unescape(blocks[0]), html.unescape(blocks[1])
assert run.startswith("docker run"), run[:80]
assert compose.startswith("services:"), compose[:80]
print(run)
PY
}

# snippet <file> <args of ui>: generate and keep the snippet; `-- [key]` handled by the caller
snippet() { local f="$1"; shift; ui "$@" > "$f" || fail "could not generate the snippet ($*)"; }

# run_snippet <file>: run the snippet as printed. Only `docker run -d` becomes create + connect + start, so the
# container is on the test network and on the LAN before tailscaled starts (attaching a second network to a
# running container moves its default route and breaks the sign-in in progress).
run_snippet() {
    local f="$1" text name
    text=$(cat "$f")
    [ -z "${TS_IMAGE:-}" ] || text="${text//tailscale\/tailscale:latest/$TS_IMAGE}"
    text="${text/docker run -d/docker create --network $NET}"
    name=$(grep -o -m1 -- '--name [^ ]*' "$f" | cut -d' ' -f2 | tr -d "'\"")
    bash -c "$text" >/dev/null 2>"$WORK/run.err" || { cat "$WORK/run.err"; fail "the snippet did not start: $(cat "$f")"; }
    docker network connect "$LAN" "$name" || fail "could not connect $name to the LAN"
    docker start "$name" >/dev/null || fail "could not start $name"
}
# without_line <file> <pattern>: the snippet minus the lines that match
without_line() { grep -v -E -- "$2" "$1" > "$1.cut"; mv "$1.cut" "$1"; }
key_of() { grep -o 'TS_AUTHKEY=[^ ]*' "$1" | head -1 | cut -d= -f2- | tr -d "'\""; }

# --- Headscale -------------------------------------------------------------------
hs() { docker exec "$SRV" "${HSC[@]}" "$@"; }
# node <hostname> <field>: a field of that node from `nodes list` (empty when it is not there)
node() {
    hs nodes list -o json 2>/dev/null | python3 -c "
import json, sys
try: rows = json.load(sys.stdin) or []
except ValueError: rows = []
for r in rows:
    if r.get('name') == sys.argv[1] or r.get('given_name') == sys.argv[1]:
        v = r.get(sys.argv[2], '')
        print(','.join(map(str, v)) if isinstance(v, list) else str(v).lower() if isinstance(v, bool) else v)
        break" "$1" "$2"
}
node_count() { hs nodes list -o json 2>/dev/null | python3 -c "
import json, sys
print(sum(1 for r in (json.load(sys.stdin) or []) if r.get('name') == sys.argv[1]))" "$1"; }
has_node() { [ -n "$(node "$1" id)" ]; }
is_online() { [ "$(node "$1" online)" = true ]; }
has_route() { [[ ",$(node "$1" available_routes)," == *",$2,"* ]]; }
has_exit() { has_route "$1" 0.0.0.0/0 && has_route "$1" ::/0; }
approved() { [[ ",$(node "$1" approved_routes)," == *",$2,"* ]]; }
approve() { local h="$1"; shift; hs nodes approve-routes -i "$(node "$h" id)" -r "$1" >/dev/null || fail "could not approve $1 for $h"; }
ts_ip() { docker exec "$1" tailscale ip -4 2>/dev/null | head -1; }

# --- the client ------------------------------------------------------------------
cli() { docker exec "$CLIENT" tailscale "$@"; }
rx() { docker exec "$CLIENT" cat /sys/class/net/tailscale0/statistics/rx_bytes 2>/dev/null || echo 0; }
# through_exit <tries>: an HTTPS fetch that succeeds AND comes back through tailscale0 (the client has Internet of its
# own, so only the rx delta shows the exit node carried it). Headscale's default policy does not let a client send
# an exit-node flow to a private or test range, so the exit node cases use a real Internet site, not the LAN target.
through_exit() {
    local limit="$1" before after _
    for _ in $(seq 1 "$limit"); do
        before=$(rx)
        if docker exec "$CLIENT" wget -q -O /dev/null -T 10 "$HTTPS_URL" 2>/dev/null; then
            after=$(rx)
            if [ $((after - before)) -gt 2000 ]; then RX_DELTA=$((after - before)); return 0; fi
        fi
        sleep 2
    done
    return 1
}
via_target() { docker exec "$CLIENT" wget -qO- -T 8 "http://$TARGET_IP/" 2>/dev/null | grep -qF "$TOKEN"; }
client_has_route() { docker exec "$CLIENT" ip route show table 52 | grep -q "$1"; }
client_reset() { cli set --exit-node= --accept-routes=false >/dev/null 2>&1; }

# select_exit <hostname>: the client picks that machine as its exit node, retrying while the netmap catches up
select_exit() {
    local ip; ip=$(ts_ip "tailscale-$1"); [ -n "$ip" ] || { docker exec "tailscale-$1" tailscale ip -4 >&2; ip=$(docker exec "tailscale-$1" tailscale ip 2>/dev/null | head -1); }
    local _
    for _ in $(seq 1 45); do cli set --exit-node="$ip" --exit-node-allow-lan-access >/dev/null 2>&1 && return 0; sleep 1; done
    fail "the client could not select $1 ($ip) as its exit node: $(cli set --exit-node="$ip" --exit-node-allow-lan-access 2>&1)"
}

# end_case <hostname>: forget the machine so the next case starts clean
end_case() {
    client_reset
    docker rm -f "tailscale-$1" >/dev/null 2>&1
    docker volume rm -f "tailscale-$1" >/dev/null 2>&1
    local id; id=$(node "$1" id)
    [ -z "$id" ] || hs nodes delete -i "$id" --force >/dev/null 2>&1
}

# --- environment -----------------------------------------------------------------
echo "== environment"
[ -c /dev/net/tun ] || fail "/dev/net/tun is not available on this host"
docker network create "$NET" >/dev/null || fail "could not create the network"
docker network create --subnet "$LAN_SUBNET" "$LAN" >/dev/null || fail "could not create the LAN network"
docker run -d --name "$SRV" --network "$NET" --network-alias hse -p "$PORT:80" -v "$SRV-data:/data" \
    -e HSE_PUBLIC_URL="http://hse" -e HSE_TLS=off \
    -e HSE_ADMIN_EMAIL=admin@example.com -e HSE_ADMIN_PASSWORD="$ADMIN_PW" "$IMAGE" >/dev/null \
    || fail "could not start the server"
healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ]; }
wait_for 120 "the server did not become healthy" healthy "$SRV"
# (a container joins the LAN only once it is online: a second network moves the default route during sign-in)
# something on the "LAN" only the router and exit node containers can reach (busybox: alpine has no httpd applet)
docker run -d --name "$TARGET" --network "$LAN" --ip "$TARGET_IP" busybox:1.37 \
    sh -c "mkdir /www && echo $TOKEN > /www/index.html && exec httpd -f -p 80 -h /www" >/dev/null \
    || fail "could not start the target"
keyfile="$WORK/client.key"
hs preauthkeys create --user 1 --reusable --expiration 1h 2>&1 | tail -1 > "$keyfile"
[ -s "$keyfile" ] && ! grep -qiE 'error|fail' "$keyfile" || fail "could not create the client's key: $(cat "$keyfile")"
CLIENT_IMAGE="${TS_IMAGE:-tailscale/tailscale:latest}"
docker run -d --name "$CLIENT" --network "$NET" --cap-add NET_ADMIN --cap-add NET_RAW --device /dev/net/tun \
    -e TS_AUTHKEY="$(cat "$keyfile")" -e TS_HOSTNAME=dts-client -e TS_STATE_DIR=/var/lib/tailscale \
    -e TS_USERSPACE=false -e TS_ACCEPT_DNS=false -e TS_EXTRA_ARGS="--login-server=http://hse" \
    -e TS_DEBUG_FIREWALL_MODE=auto \
    "$CLIENT_IMAGE" >/dev/null || fail "could not start the client"
wait_for 90 "the client did not register" is_online dts-client
wait_for 60 "the client has no Tailscale address" bash -c "[ -n \"\$(docker exec $CLIENT tailscale ip -4 2>/dev/null)\" ]"
echo "server, target and client are up"
summary "### Docker tab snippets, end to end" ""

# =============================================================================
# 1. exit node, first deploy
# =============================================================================
echo "== exit node, first deploy"
h=dts-exit
snippet "$WORK/exit.run" "$h" 1 "" 0 1
grep -q 'TS_ROUTES=0.0.0.0/0,::/0' "$WORK/exit.run" || fail "the exit node snippet has no TS_ROUTES=0.0.0.0/0,::/0"
run_snippet "$WORK/exit.run"
wait_for 90 "the exit node did not register" is_online "$h"
wait_for 30 "Headscale does not list the exit node routes" has_exit "$h"
approved "$h" 0.0.0.0/0 && fail "the exit node was approved without anybody approving it"
approve "$h" "0.0.0.0/0,::/0"
[ "$(docker exec "tailscale-$h" cat /proc/sys/net/ipv4/ip_forward)" = 1 ] || fail "net.ipv4.ip_forward is not 1 in the exit node container"
[ "$(docker exec "tailscale-$h" cat /proc/sys/net/ipv6/conf/all/forwarding)" = 1 ] || fail "net.ipv6.conf.all.forwarding is not 1 in the exit node container"
select_exit "$h"
through_exit 20 || fail "the client got no traffic through the exit node (HTTPS to $HTTPS_URL)"
ok "exit node, first deploy: advertised, approved, client fetches $HTTPS_URL through it (${RX_DELTA} bytes back over tailscale0)"
end_case "$h"

# =============================================================================
# 2. exit node added later, on the same state volume with a key that is already spent
# =============================================================================
echo "== exit node added later"
h=dts-late
snippet "$WORK/late1.run" "$h" 0 "" 0 1
key=$(key_of "$WORK/late1.run"); [ -n "$key" ] || fail "no auth key in the snippet"
run_snippet "$WORK/late1.run"
wait_for 90 "the plain container did not register" is_online "$h"
has_route "$h" 0.0.0.0/0 && fail "a container deployed without the exit node advertises it"
id1=$(node "$h" id)
# the form again, now ticking the exit node, without generating a key: the old (spent) one goes in its place
snippet "$WORK/late2.run" "$h" 1 "" 0 0
grep -q 'TS_AUTHKEY=<auth-key>' "$WORK/late2.run" || fail "the regenerated snippet has no <auth-key> placeholder"
sed -i "s/<auth-key>/$key/" "$WORK/late2.run"
docker rm -f "tailscale-$h" >/dev/null
run_snippet "$WORK/late2.run"
wait_for 90 "the recreated container is not online" is_online "$h"
wait_for 60 "the exit node added later is not advertised after recreating the container" \
    has_route "$h" 0.0.0.0/0
[ "$(node "$h" id)" = "$id1" ] || fail "recreating the container registered a new machine (id $id1 -> $(node "$h" id))"
[ "$(node_count "$h")" = 1 ] || fail "there is more than one machine called $h"
approve "$h" "0.0.0.0/0,::/0"
select_exit "$h"
through_exit 20 || fail "the client got no traffic through the exit node added later"
ok "exit node added later: advertised after recreating on the same volume with a spent key, same machine"
end_case "$h"

# =============================================================================
# 3. subnet route + --accept-routes
# =============================================================================
echo "== subnet route"
h=dts-router
snippet "$WORK/router.run" "$h" 0 "$LAN_SUBNET" 0 1
grep -q "TS_ROUTES=$LAN_SUBNET" "$WORK/router.run" || fail "the snippet has no TS_ROUTES=$LAN_SUBNET"
run_snippet "$WORK/router.run"
wait_for 90 "the subnet router did not register" is_online "$h"
wait_for 30 "Headscale does not list the subnet route" has_route "$h" "$LAN_SUBNET"
approve "$h" "$LAN_SUBNET"
# without --accept-routes the client does not use it...
cli set --accept-routes=false >/dev/null
sleep 3
! via_target || fail "the client reached the LAN without --accept-routes"
# ...and with it, it does
cli set --accept-routes=true >/dev/null || fail "tailscale set --accept-routes failed"
wait_for 45 "the client has no route to $LAN_SUBNET with --accept-routes" \
    client_has_route "$LAN_SUBNET"
wait_for 30 "the client cannot reach the target through the subnet router" via_target
ok "subnet route $LAN_SUBNET: advertised, approved, only used with --accept-routes, target reached"
end_case "$h"

# =============================================================================
# 4. userspace exit node
# =============================================================================
echo "== userspace exit node"
h=dts-user
snippet "$WORK/user.run" "$h" 1 "" 1 1
grep -q 'TS_USERSPACE=true' "$WORK/user.run" || fail "the userspace snippet has no TS_USERSPACE=true"
! grep -qE '/dev/net/tun|NET_ADMIN|--sysctl' "$WORK/user.run" || fail "the userspace snippet asks for /dev/net/tun, NET_ADMIN or sysctls"
run_snippet "$WORK/user.run"
wait_for 90 "the userspace exit node did not register" is_online "$h"
wait_for 30 "Headscale does not list the userspace exit node" has_route "$h" 0.0.0.0/0
approve "$h" "0.0.0.0/0,::/0"
select_exit "$h"
through_exit 20 || fail "the client got no traffic through the userspace exit node (HTTPS to $HTTPS_URL)"
ok "userspace exit node: no tun, no capabilities, client fetches $HTTPS_URL through it (${RX_DELTA} bytes back over tailscale0)"
end_case "$h"

# =============================================================================
# 5. a spent single-use key must not log the container out
# =============================================================================
echo "== spent key"
h=dts-spent
snippet "$WORK/spent.run" "$h" 0 "" 0 1
run_snippet "$WORK/spent.run"
wait_for 90 "the container did not register" is_online "$h"
id1=$(node "$h" id)
used=$(hs preauthkeys list -o json 2>/dev/null | python3 -c "
import json, sys
print(sum(1 for k in (json.load(sys.stdin) or []) if k.get('used')))")
[ "${used:-0}" -ge 1 ] || fail "Headscale does not show the single-use key as used"
docker restart "tailscale-$h" >/dev/null
sleep 8
wait_for 60 "the container is not online after a restart with a spent key" is_online "$h"
docker rm -f "tailscale-$h" >/dev/null
run_snippet "$WORK/spent.run"           # the same snippet, the same spent key, the same volume
sleep 8
wait_for 60 "the container is not online after being recreated with a spent key" is_online "$h"
[ "$(docker inspect -f '{{.State.Running}}' "tailscale-$h")" = true ] || fail "the container stopped with a spent key"
[ "$(node "$h" id)" = "$id1" ] && [ "$(node_count "$h")" = 1 ] || fail "a spent key created a second machine or replaced the first"
docker logs "tailscale-$h" 2>&1 | grep -qiE 'authkey already used|invalid auth|unauthorized|You are logged out' \
    && fail "the container complains about its spent key: $(docker logs "tailscale-$h" 2>&1 | grep -iE 'authkey already used|invalid auth|unauthorized|You are logged out' | head -3)"
ok "spent key: restart and recreate on the same volume stay online, same machine"
end_case "$h"

# =============================================================================
# 6. missing NET_ADMIN / 7. missing sysctls: the lines of the snippet are needed
# =============================================================================
echo "== missing NET_ADMIN"
h=dts-nocap
snippet "$WORK/nocap.run" "$h" 1 "" 0 1
grep -q -- '--cap-add NET_ADMIN' "$WORK/nocap.run" || fail "the exit node snippet has no --cap-add NET_ADMIN"
without_line "$WORK/nocap.run" '--cap-add NET_ADMIN'
run_snippet "$WORK/nocap.run"
sleep 20
if is_online "$h" && has_route "$h" 0.0.0.0/0; then fail "an exit node without NET_ADMIN registers and advertises itself: the cap-add line is not needed any more"; fi
docker logs "tailscale-$h" 2>&1 | grep -qiE 'permission|not permitted|operation not|EPERM|tun' \
    || fail "without NET_ADMIN the container logs no permission error"
ok "missing NET_ADMIN: the container does not come up as an exit node and logs why"
end_case "$h"

echo "== missing sysctls"
h=dts-nosysctl
snippet "$WORK/nosysctl.run" "$h" 1 "" 0 1
grep -q -- '--sysctl net.ipv4.ip_forward=1' "$WORK/nosysctl.run" || fail "the exit node snippet has no net.ipv4.ip_forward sysctl"
without_line "$WORK/nosysctl.run" '--sysctl'
run_snippet "$WORK/nosysctl.run"
# Without the sysctls the image's own boot refuses to run as a router when the
# host does not forward by default (it exits and restarts, so it never registers).
sleep 25
if ! is_online "$h"; then
    docker logs "tailscale-$h" 2>&1 | grep -q 'IP forwarding must be enabled' \
        || fail "the container without sysctls did not register and logs no IP forwarding error"
    ok "missing sysctls: without them the container refuses to start as a router and logs why, so the snippet's lines are needed"
    end_case "$h"
    echo "OK (${#CASES[@]} cases)"
    exit 0
fi
fwd4=$(docker exec "tailscale-$h" cat /proc/sys/net/ipv4/ip_forward)
fwd6=$(docker exec "tailscale-$h" cat /proc/sys/net/ipv6/conf/all/forwarding)
if [ "$fwd4" = 1 ] && [ "$fwd6" = 1 ]; then
    echo "note: this Docker host forwards by default (ip_forward=$fwd4, ipv6=$fwd6); the sysctl lines cannot be shown to matter here"
    ok "missing sysctls: this host forwards by default, so only the snippet's lines were checked"
else
    wait_for 30 "Headscale does not list the routes" has_route "$h" 0.0.0.0/0
    approve "$h" "0.0.0.0/0,::/0"
    select_exit "$h"
    sleep 5
    ! through_exit 3 || fail "an exit node without the forwarding sysctls routes traffic: the sysctl lines are not needed any more"
    ok "missing sysctls: without ip_forward (=$fwd4) the exit node routes nothing, so the snippet's lines are needed"
fi
end_case "$h"

echo "OK (${#CASES[@]} cases)"
