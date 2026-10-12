#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — project checks
#  https://github.com/insanerask77/headscale-easy
#
#  Usage: ./scripts/validate.sh   (or: make validate)
#  Checks required files, script syntax, Python syntax, UI translations and,
#  when Docker is available, the Compose file.
# =============================================================================

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
BOLD='\033[1m'

errors=0
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; errors=$((errors + 1)); }

required_files=(
    compose.yaml .env.example
    advanced/README.md advanced/backup-remote.yaml advanced/postgres.yaml advanced/postgres-bundled.yaml
    advanced/proxy.yaml advanced/oidc/authentik.yaml advanced/oidc/pocket-id.yaml
    .gitignore README.md LICENSE
    templates/headscale-config.yaml.tmpl templates/Caddyfile.tmpl templates/headscale-pg-readonly.sql
    advanced/oidc/authentik/blueprints/headscale.yaml advanced/oidc/authentik/branding/custom.css
    web/app.py web/headscale.py web/pgwire.py web/machines_pages.py web/admin_pages.py
    VERSION web/ui.py web/i18n.py web/version.py web/locales/es.json
    web/static/style.css web/static/app.js web/static/theme.js
    scripts/check_i18n.py scripts/gen_env_reference.py scripts/release_info.py
    aio/Dockerfile aio/control.py aio/supervisor.py aio/render.py
    backup/Dockerfile backup/entrypoint.sh backup/remote.sh
    mkdocs.yml docs/requirements.txt docs/index.md docs/index.es.md
)
for f in "${required_files[@]}"; do
    [[ -f "$f" ]] || fail "Missing file: $f"
done
[[ $errors -eq 0 ]] && ok "All ${#required_files[@]} required files present"

for f in scripts/validate.sh scripts/aio-smoke.sh scripts/compose-smoke.sh; do
    [[ -x "$f" ]] || fail "Not executable: $f"
    bash -n "$f" 2>/dev/null && ok "Shell syntax: $f" || fail "Shell syntax error: $f"
done

if command -v python3 &>/dev/null; then
    if python3 -m py_compile web/*.py web/*/*.py aio/*.py scripts/*.py 2>/dev/null; then ok "Python syntax: web/*.py aio/*.py scripts/*.py"; else fail "Python syntax error in web/ or aio/"; fi
    if python3 scripts/check_i18n.py >/dev/null; then ok "Translations complete"; else fail "Missing translations: python3 scripts/check_i18n.py"; fi
    if python3 scripts/gen_env_reference.py; then ok "Environment-variable reference documents every variable"; else fail "Undocumented variables: python3 scripts/gen_env_reference.py"; fi
    if python3 scripts/release_info.py check >/dev/null; then ok "VERSION, compose.yaml, aio/Dockerfile and CHANGELOG.md agree"; else fail "The version differs between files: python3 scripts/release_info.py check"; fi
else
    warn "python3 not found: Python checks skipped"
fi

# The Compose app and its advanced configurations: compose.yaml alone, each overlay on top of it, and the
# combinations people use together. The overlays require their secrets (:?) and the .env example ships them
# empty: give them a value for the check only.
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    export HSE_PUBLIC_URL=https://vpn.example.test HSE_DOMAIN=vpn.example.test ID_DOMAIN=id.example.test \
        HSE_TRUSTED_PROXIES=192.0.2.10/32 HEADSCALE_PG_HOST=db.example.test HEADSCALE_PG_PASS=validate-only \
        HEADSCALE_PG_RO_PASS=validate-only AUTHENTIK_SECRET_KEY=validate-only AUTHENTIK_PG_PASS=validate-only \
        OIDC_CLIENT_SECRET=validate-only POCKET_ID_ENCRYPTION_KEY=validate-only
    if docker compose -f compose.yaml config -q 2>/dev/null; then ok "compose.yaml is valid"; else fail "compose.yaml is invalid"; fi
    while IFS= read -r f; do
        if docker compose -f compose.yaml -f "$f" config -q 2>/dev/null; then ok "compose.yaml + $f is valid"; else fail "compose.yaml + $f is invalid"; fi
    done < <(find advanced -maxdepth 2 -name '*.yaml' | sort)   # not advanced/oidc/authentik/blueprints/
    for combo in \
        "advanced/proxy.yaml advanced/postgres.yaml" \
        "advanced/proxy.yaml advanced/postgres-bundled.yaml advanced/backup-remote.yaml" \
        "advanced/proxy.yaml advanced/oidc/authentik.yaml advanced/postgres.yaml advanced/backup-remote.yaml"; do
        args=(-f compose.yaml); for f in $combo; do args+=(-f "$f"); done
        if docker compose "${args[@]}" config -q 2>/dev/null; then ok "compose.yaml + $combo is valid"; else fail "compose.yaml + $combo is invalid"; fi
    done
fi
# ...and nothing may mount the Docker socket: no compose file anywhere
sock=$(grep -rn --include='*.yml' --include='*.yaml' --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.claude 'docker\.sock' . 2>/dev/null | grep -v -E ':[0-9]*:[[:space:]]*#' || true)
if [[ -n "$sock" ]]; then
    fail "no compose file may mount the Docker socket: $sock"
else
    ok "Nothing mounts the Docker socket"
fi

echo ""
if [[ $errors -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All checks passed${NC}"
else
    echo -e "${RED}${BOLD}${errors} check(s) failed${NC}"
    exit 1
fi
