#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy: installer for the all-in-one image
#  https://github.com/insanerask77/headscale-easy
#
#    ./install.sh                       ask a few questions, install, start
#    curl -fsSL <url>/install.sh | bash          the same, no clone needed
#    HSE_PUBLIC_URL=https://vpn.example.com HSE_TLS=auto ACME_EMAIL=me@example.com \
#        ./install.sh --yes             no questions
#
#  Options: --yes (use the defaults and the environment), --dir DIR (default ./headscale-easy),
#           --install-docker (with --yes: install Docker if missing), --no-pull (use the image already here), --help
#  Environment: HSE_PUBLIC_URL, HSE_TLS (auto|internal|off), ACME_EMAIL, HSE_ADMIN_EMAIL,
#               HSE_ADMIN_PASSWORD, HSE_VERSION, TZ, HSE_DIR
#
#  It writes a compose file and a small .env in the install directory and runs
#  `docker compose up -d`. Everything else (DERP, DNS, identity provider, backups) is the setup
#  wizard's job, or the console's afterwards. Running it again on the same directory only updates
#  the images: the .env and the data are never touched.
# =============================================================================
set -euo pipefail

YES=false; DIR="${HSE_DIR:-./headscale-easy}"; INSTALL_DOCKER=false; NO_PULL=false
CONTAINER=headscale-easy
# Where answers are read from: the terminal even when the script itself arrives on stdin (curl | bash)
INPUT="${HSE_INSTALL_INPUT:-/dev/tty}"

say()  { printf '%s\n' "$*"; }
warn() { printf '! %s\n' "$*" >&2; }
die()  { printf 'x %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,2\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y) YES=true ;;
        --dir) [[ $# -ge 2 ]] || die "--dir needs a directory"; DIR="$2"; shift ;;
        --install-docker) INSTALL_DOCKER=true ;;
        --no-pull) NO_PULL=true ;;
        --help|-h) usage ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

# The input is opened once (fd 3): reopening a file for every question would read its first line again
HAVE_INPUT=false
if ! $YES && { exec 3< "$INPUT"; } 2>/dev/null; then HAVE_INPUT=true; fi

# ask <prompt> <default> -> the answer (the default when empty, or when running with --yes)
ask() {
    local answer=""
    if ! $YES; then
        if $HAVE_INPUT; then
            printf '? %s [%s]: ' "$1" "${2:-none}" >&2
            read -r answer <&3 || true
        else warn "no terminal to ask '$1': using the default (or pass --yes and variables)"; fi
    fi
    printf '%s' "${answer:-$2}"
}

valid_host()  { [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]]; }
valid_email() { [[ "$1" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; }
is_local()    { [[ "$1" == localhost* || "$1" =~ ^[0-9.]+(:[0-9]+)?$ ]]; }

# --- Docker --------------------------------------------------------------------------------------
ensure_docker() {
    if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
        local go=false
        if $YES; then
            if $INSTALL_DOCKER; then go=true; fi
        elif [[ "$(ask "Docker (with Compose) is not installed. Install it with get.docker.com? (y/N)" n)" =~ ^[yY] ]]; then
            go=true
        fi
        $go || die "Docker with the Compose plugin is required: https://docs.docker.com/engine/install/ (or --install-docker)"
        say "Installing Docker with the official script (get.docker.com)..."
        curl -fsSL https://get.docker.com | sh || die "Docker could not be installed automatically"
    fi
    docker info >/dev/null 2>&1 || die "cannot talk to Docker: run as root, or add your user to the docker group"
}

# --- an existing directory -----------------------------------------------------------------------
# A 1.x install keeps its settings in the repository root. 2.0 does not convert it: never write
# into that directory.
refuse_1x() {
    if [[ -f "$DIR/headscale-config.yaml" ]] || grep -qE '^(SSL_MODE|AUTH_PROVIDER)=' "$DIR/.env" 2>/dev/null; then
        die "$DIR holds a 1.x installation (split compose file). 2.0 is a fresh install: use another directory (--dir)."
    fi
}

compose_in() { (cd "$DIR" && docker compose "$@"); }

wait_healthy() {
    local status
    for _ in $(seq 1 60); do
        status=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || true)
        [[ "$status" == healthy ]] && return 0
        sleep "${HSE_INSTALL_POLL:-2}"
    done
    warn "the container is not healthy yet: docker compose -f $DIR/docker-compose.yml logs"
    return 1
}

# The compose file below is deploy/compose/docker-compose.yml, copied by scripts/embed-compose.sh
# (tests/test_install.py fails when they differ).
write_compose() {
    cat > "$1" <<'HSE_COMPOSE'
# Headscale Easy — all-in-one image, reference compose file (advanced edition)
# https://github.com/insanerask77/headscale-easy
#
#   cp .env.example .env     # set HSE_PUBLIC_URL (and HSE_TLS / ACME_EMAIL)
#   docker compose up -d     # then open the setup wizard (token: docker compose logs)
#   docker compose --profile backup-remote up -d    # also upload every backup elsewhere
#
# One container does everything: Headscale, Caddy (HTTPS) and the web console. What
# you add around it (an identity provider, PostgreSQL, a proxy in front) is in
# ../examples/. Reference for every variable: docs/all-in-one.md.
name: headscale-easy

services:
  headscale-easy:
    image: ghcr.io/insanerask77/headscale-easy:${HSE_VERSION:-latest}
    container_name: headscale-easy
    restart: unless-stopped
    env_file: .env
    ports:
      - "${HSE_HTTP_PORT:-80}:80"
      - "${HSE_HTTPS_PORT:-443}:443"
      - "${HSE_HTTPS_PORT:-443}:443/udp"
      - "3478:3478/udp"   # DERP/STUN: the clients' fallback relay. Not HTTP: a proxy cannot carry it.
    volumes:
      - hse-data:/data
      # Backups: a named volume by default. To keep them in a folder (a NAS mount), use a bind mount
      # instead and make it writable by uid 1000:  mkdir -p backups && chown 1000:1000 backups
      - hse-backups:/data/backups
    # The image runs as an unprivileged user and needs no capability: ports 80/443 are bound through
    # this sysctl (Docker >= 20.10 already has it; Podman and older engines need it set).
    sysctls:
      net.ipv4.ip_unprivileged_port_start: 0
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"

  # Optional: uploads each new backup to S3, B2, SFTP (rclone) or a server (rsync over SSH).
  # Set BACKUP_REMOTE in .env; the rclone config or the SSH key goes in ./remote-config.
  backup-remote:
    image: ghcr.io/insanerask77/headscale-easy-backup:${HSE_VERSION:-latest}
    container_name: headscale-easy-backup-remote
    profiles: ["backup-remote"]
    restart: unless-stopped
    depends_on:
      - headscale-easy
    environment:
      BACKUP_MODE: sync
      BACKUP_SYNC_INTERVAL: ${BACKUP_SYNC_INTERVAL:-15}
      BACKUP_REMOTE: ${BACKUP_REMOTE:-}
      BACKUP_REMOTE_KEEP_DAYS: ${BACKUP_REMOTE_KEEP_DAYS:-${BACKUP_KEEP_DAYS:-14}}
      BACKUP_REMOTE_SSH_PORT: ${BACKUP_REMOTE_SSH_PORT:-22}
      AWS_ACCESS_KEY_ID: ${BACKUP_AWS_ACCESS_KEY_ID:-}
      AWS_SECRET_ACCESS_KEY: ${BACKUP_AWS_SECRET_ACCESS_KEY:-}
      TZ: ${TZ:-UTC}
    volumes:
      # Only the backups, read-only: the archives are owned by uid 1000 with mode 600, hence DAC_OVERRIDE.
      - hse-backups:/backups:ro
      - ${HSE_REMOTE_CONFIG:-./remote-config}:/remote-config:ro
    cap_drop:
      - ALL
    cap_add:
      - DAC_OVERRIDE
    security_opt:
      - no-new-privileges:true
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  hse-data:
  hse-backups:
HSE_COMPOSE
}

# --- main ----------------------------------------------------------------------------------------
ensure_docker
refuse_1x
mkdir -p "$DIR"

if [[ -f "$DIR/.env" && -f "$DIR/docker-compose.yml" ]]; then
    say "Existing installation in $DIR: updating the images (the .env and the data are not touched)."
    tmp=$(mktemp); write_compose "$tmp"
    if ! cmp -s "$tmp" "$DIR/docker-compose.yml"; then
        cp "$DIR/docker-compose.yml" "$DIR/docker-compose.yml.bak"; cp "$tmp" "$DIR/docker-compose.yml"
        say "The compose file changed: the old one is docker-compose.yml.bak"
    fi
    rm -f "$tmp"
    if $NO_PULL; then compose_in up -d; else compose_in pull && compose_in up -d; fi
    wait_healthy || true
    say "Done."; exit 0
fi

say "Headscale Easy: all-in-one install in $DIR"
addr=$(ask "Domain, IP or address of this server (vpn.example.com, 192.168.1.10, https://...)" "${HSE_PUBLIC_URL:-}")
[[ -n "$addr" ]] || die "an address is needed (HSE_PUBLIC_URL, or answer the question)"
scheme=""; host="$addr"
if [[ "$addr" =~ ^(https?)://([^/]+)/?$ ]]; then scheme="${BASH_REMATCH[1]}"; host="${BASH_REMATCH[2]}"; fi
valid_host "$host" || die "not a valid address: $addr"

if [[ -n "${HSE_TLS:-}" ]]; then tls_default="$HSE_TLS"
elif [[ "$scheme" == http ]] || is_local "$host"; then tls_default=off
else tls_default=auto; fi
tls=$(ask "HTTPS: auto (Let's Encrypt), internal (own CA, for a LAN) or off (plain HTTP, or a proxy you run)" "$tls_default")
case "$tls" in auto|internal|off) ;; *) die "HTTPS must be auto, internal or off" ;; esac
if [[ "$tls" == auto ]] && is_local "$host"; then die "Let's Encrypt does not issue certificates for IPs or localhost: use internal or off"; fi
if [[ "$tls" == off ]]; then url_scheme="${scheme:-http}"; else url_scheme=https; fi
acme=""
if [[ "$tls" == auto ]]; then
    acme=$(ask "Email for Let's Encrypt" "${ACME_EMAIL:-admin@${host%%:*}}")
    valid_email "$acme" || die "not a valid email: $acme"
fi
admin=$(ask "Administrator email (empty: the setup wizard creates the account)" "${HSE_ADMIN_EMAIL:-}")
if [[ -n "$admin" ]]; then valid_email "$admin" || die "not a valid email: $admin"; fi
pass=""
if [[ -n "$admin" ]]; then
    pass="${HSE_ADMIN_PASSWORD:-$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)}"
    [[ ${#pass} -ge 8 && "$pass" =~ ^[A-Za-z0-9._@%+=:-]+$ ]] || die "HSE_ADMIN_PASSWORD: 8 or more letters, digits or . _ @ % + = : -"
fi
public="${url_scheme}://${host}"

write_compose "$DIR/docker-compose.yml"
tz="${TZ:-$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)}"
[[ "$tz" =~ ^[A-Za-z0-9_+/-]+$ ]] || tz=UTC
(
    umask 077
    {
        echo "# Headscale Easy: written by install.sh. More options: https://github.com/insanerask77/headscale-easy/tree/next/deploy/compose"
        echo "HSE_PUBLIC_URL=$public"
        echo "HSE_TLS=$tls"
        if [[ -n "$acme" ]]; then echo "ACME_EMAIL=$acme"; fi
        if [[ -n "$admin" ]]; then echo "HSE_ADMIN_EMAIL=$admin"; echo "HSE_ADMIN_PASSWORD=$pass"; fi
        echo "TZ=$tz"
        if [[ -n "${HSE_VERSION:-}" ]]; then echo "HSE_VERSION=$HSE_VERSION"; fi
    } > "$DIR/.env"
)
chmod 600 "$DIR/.env"

$NO_PULL || compose_in pull
compose_in up -d
healthy=true; wait_healthy || healthy=false

say ""
if $healthy; then say "Headscale Easy is up at $public"; else say "Headscale Easy is starting at $public"; fi
if [[ -z "$admin" ]]; then
    token=$(docker exec "$CONTAINER" cat /data/config/setup-token 2>/dev/null || true)
    say "Open the setup wizard: $public/admin/setup"
    if [[ -n "$token" ]]; then say "One-time token: $token"; fi
else
    say "Sign in at $public/admin as $admin with the password in $DIR/.env (HSE_ADMIN_PASSWORD)."
    say "Change it in the console, then delete that line from the .env."
fi
if [[ "$tls" == off && "$url_scheme" == https ]]; then
    say "Proxy in front: set HSE_TRUSTED_PROXIES in $DIR/.env and see deploy/examples/front-proxy/. UDP 3478 must reach this host directly."
fi
if [[ "$url_scheme" == http ]]; then warn "No HTTPS: the control plane travels unencrypted. Do not expose it to the Internet."; fi
say "Backups, identity provider and PostgreSQL: https://github.com/insanerask77/headscale-easy/tree/next/deploy"
