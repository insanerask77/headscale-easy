#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — end-to-end tests
#  https://github.com/insanerask77/headscale-easy
#
#  Usage: tests/e2e/run.sh   (or: make e2e; E2E_AUTH=authentik make e2e)
#
#  Deploys a throwaway stack in THIS checkout with
#  './install.sh --non-interactive' (Headscale, web UI, hs-helper, backups
#  and, with E2E_AUTH=authentik, the built-in Authentik), runs
#  tests/e2e/e2e_*.py against it and removes it again.
#
#  The stack uses fixed container and volume names (headscale, caddy,
#  headscale-data...), so this refuses to run on a machine that already has
#  them: use a VM or CI (the 'e2e' job in .github/workflows/ci.yml).
#
#  Environment:
#    E2E_AUTH        none (default) or authentik
#    E2E_DOMAIN      public name of the stack (default hse.e2e.test; the
#                    tests reach it on 127.0.0.1, no DNS needed)
#    E2E_HTTP_PORT   Caddy's HTTP port on this machine (default 8088)
#    E2E_KEEP        true = leave the stack running afterwards
#    E2E_TAILSCALE   false = skip the real Tailscale client
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

AUTH="${E2E_AUTH:-none}"
case "$AUTH" in none|authentik) ;; *) echo "E2E_AUTH must be none or authentik" >&2; exit 2 ;; esac

# Never touch an existing installation
existing=$(docker ps -a --format '{{.Names}}' | grep -xE 'headscale|caddy|headscale-easy|headscale-easy-helper|authentik-server' || true)
if [[ -n "$existing" || -f .env ]] || docker volume inspect headscale-data >/dev/null 2>&1; then
    echo "Refusing to run: this machine already has a Headscale Easy stack (containers, the headscale-data volume or .env)." >&2
    echo "The end-to-end tests deploy and then DELETE a stack with the same names: run them in a VM or CI." >&2
    exit 2
fi

compose() { docker compose --profile authentik --profile backup --profile postgres "$@"; }

teardown() {
    local status=$?
    if [[ $status -ne 0 ]]; then
        echo "::group::docker compose ps / logs (failure)"
        compose ps -a || true
        compose logs --no-color --tail 200 || true
        docker logs --tail 100 hse-e2e-tailscale 2>&1 || true
        echo "::endgroup::"
    fi
    if [[ "${E2E_KEEP:-false}" != "true" ]]; then
        docker rm -f hse-e2e-tailscale >/dev/null 2>&1 || true
        compose down -v --remove-orphans >/dev/null 2>&1 || true
        # Generated files; data/ and backups/ may hold files owned by the containers' users
        docker run --rm -v "$ROOT:/p" alpine:3 sh -c 'rm -rf /p/data /p/backups' >/dev/null 2>&1 || true
        rm -f .env Caddyfile headscale-config.yaml headscale-derp.yaml docker-compose.override.yml
    fi
    exit $status
}
trap teardown EXIT

echo "==> Installing (AUTH_PROVIDER=${AUTH})"
HSE_NONINTERACTIVE=true \
UI_LANG=en \
DOMAIN="${E2E_DOMAIN:-hse.e2e.test}" \
SSL_MODE=none \
HTTP_PORT="${E2E_HTTP_PORT:-8088}" \
AUTH_PROVIDER="$AUTH" \
TAILNET_NAME=e2e \
ADMIN_USER=admin \
DERP_USE_PUBLIC=false \
NETWORK_ISOLATION=true \
PORTAL_API_KEY_LOGIN=true \
MFA_REQUIRED=optional \
BACKUP_ENABLED=true \
BACKUP_DIR=./backups \
BACKUP_KEEP_DAYS=14 \
    ./install.sh --non-interactive

grep -q '^HEADSCALE_API_KEY=hskey-api-' .env || { echo "The installer did not create an API key" >&2; exit 1; }

echo "==> Running the end-to-end tests"
python3 -m unittest discover -s tests/e2e -p 'e2e_*.py' -v
