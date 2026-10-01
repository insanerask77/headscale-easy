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
    install.sh uninstall.sh docker-compose.yml .env.example .gitignore
    README.md LICENSE
    templates/headscale-config.yaml.tmpl templates/Caddyfile.tmpl
    templates/front-caddy.tmpl templates/front-nginx.conf.tmpl
    templates/front-npm.md.tmpl templates/front-traefik.yml.tmpl
    authentik/blueprints/headscale.yaml authentik/branding/custom.css
    web/Dockerfile web/app.py web/headscale.py web/pages.py web/admin_pages.py
    web/ui.py web/i18n.py web/version.py web/mfa.py web/locales/es.json
    web/static/style.css web/static/app.js web/static/theme.js
    scripts/utils.sh scripts/check_i18n.py scripts/restore.sh
    helper/Dockerfile helper/helper.py
    backup/Dockerfile backup/backup.sh backup/entrypoint.sh backup/pg-client.sh
    mkdocs.yml docs/requirements.txt docs/index.md docs/index.es.md
)
for f in "${required_files[@]}"; do
    [[ -f "$f" ]] || fail "Missing file: $f"
done
[[ $errors -eq 0 ]] && ok "All ${#required_files[@]} required files present"

for f in install.sh uninstall.sh scripts/utils.sh scripts/validate.sh scripts/restore.sh; do
    [[ -x "$f" ]] || fail "Not executable: $f"
    bash -n "$f" 2>/dev/null && ok "Shell syntax: $f" || fail "Shell syntax error: $f"
done

if command -v python3 &>/dev/null; then
    if python3 -m py_compile web/*.py helper/*.py 2>/dev/null; then ok "Python syntax: web/*.py helper/*.py"; else fail "Python syntax error in web/ or helper/"; fi
    if python3 scripts/check_i18n.py >/dev/null; then ok "Translations complete"; else fail "Missing translations: python3 scripts/check_i18n.py"; fi
else
    warn "python3 not found: Python checks skipped"
fi

if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    if docker compose -f docker-compose.yml config -q 2>/dev/null; then ok "docker-compose.yml is valid"; else fail "docker-compose.yml is invalid"; fi
else
    warn "Docker Compose not found: Compose check skipped"
fi

# Only hs-helper may mount the Docker socket (see SECURITY.md)
if awk '/^  [a-z]/ { svc = $1 } !/^[[:space:]]*#/ && /docker\.sock/ && svc != "hs-helper:" { found = 1 } END { exit !found }' docker-compose.yml; then
    fail "docker-compose.yml: only the hs-helper service may mount the Docker socket"
else
    ok "Docker socket mounted by hs-helper only"
fi

for var in DOMAIN SSL_MODE SERVER_URL TAILNET_NAME AUTH_PROVIDER PORTAL_SESSION_SECRET UI_LANG; do
    grep -q "^${var}=" .env.example || fail ".env.example lacks ${var}"
done

echo ""
if [[ $errors -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All checks passed${NC}"
else
    echo -e "${RED}${BOLD}${errors} check(s) failed${NC}"
    exit 1
fi
