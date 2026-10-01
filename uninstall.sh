#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — uninstaller
#  https://github.com/insanerask77/headscale-easy
#
#  Usage: ./uninstall.sh [--purge] [--lang en|es]
#    (default)  stop and remove the containers; data and configuration stay
#    --purge    also delete volumes, generated configuration and ./data
#               (users, machines, keys, certificates: IRREVERSIBLE)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
PURGE=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

UI_LANG=$(grep -E '^UI_LANG=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)
UI_LANG="${UI_LANG:-en}"
t() { if [[ "${UI_LANG:-en}" == "es" ]]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

print_info()    { echo -e "${BLUE}ℹ${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error()   { echo -e "${RED}✗${NC} $1" >&2; }

ask_yes_no() {
    local response
    read -r -p "$(echo -e "${CYAN}?${NC} $1 $(t "[y/N]" "[s/N]"): ")" response
    [[ "$(echo "${response:-n}" | tr '[:upper:]' '[:lower:]')" =~ ^(y|yes|s|si|sí)$ ]]
}

usage() {
    echo "Usage: $0 [--purge] [--lang en|es]"
    echo "  --purge    also delete volumes, generated configuration and ./data"
    echo "  --lang     interface language (default: the installed one, else en)"
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --purge) PURGE=true ;;
            --lang)
                [[ "${2:-}" =~ ^(en|es)$ ]] || { print_error "--lang needs en or es"; exit 1; }
                UI_LANG="$2"; shift ;;
            -h|--help) usage; exit 0 ;;
            *) print_error "Unknown option: $1"; usage; exit 1 ;;
        esac
        shift
    done

    echo -e "${CYAN}${BOLD}Headscale Easy — $(t "uninstall" "desinstalar")${NC}\n"
    if $PURGE; then
        print_error "$(t "PURGE: this deletes every user, machine, key, certificate and setting. It cannot be undone." \
                         "PURGE: se borran todos los usuarios, máquinas, claves, certificados y ajustes. No se puede deshacer.")"
    else
        print_info "$(t "Containers are removed; data and configuration are kept (use --purge to delete them)." \
                        "Se eliminan los contenedores; los datos y la configuración se conservan (usa --purge para borrarlos).")"
    fi
    ask_yes_no "$(t "Continue?" "¿Continuar?")" || { print_info "$(t "Cancelled" "Cancelado")"; exit 0; }
    if $PURGE; then
        ask_yes_no "$(t "Are you COMPLETELY sure?" "¿Estás COMPLETAMENTE seguro?")" || { print_info "$(t "Cancelled" "Cancelado")"; exit 0; }
    fi

    cd "$SCRIPT_DIR"
    # shellcheck source=/dev/null
    if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi

    # Every profile, so Authentik, PostgreSQL and backups go too when enabled
    docker compose --profile authentik --profile postgres --profile backup down --remove-orphans || print_warning "$(t "Some containers could not be removed" "Algunos contenedores no se pudieron eliminar")"
    print_success "$(t "Containers removed" "Contenedores eliminados")"

    if $PURGE; then
        local v
        for v in headscale-data headscale-db headscale-socket caddy-data caddy-config authentik-db authentik-data authentik-media; do
            docker volume rm "$v" >/dev/null 2>&1 && print_success "$(t "Volume deleted:" "Volumen borrado:") $v"
        done
        docker network rm "${NETWORK_NAME:-headscale-net}" >/dev/null 2>&1 || true
        rm -f .env headscale-config.yaml Caddyfile docker-compose.override.yml caddy-root-ca.crt
        rm -rf data reverse-proxy
        print_success "$(t "Configuration and data deleted" "Configuración y datos borrados")"
    fi

    echo ""
    print_info "$(t "Install again any time with ./install.sh" "Vuelve a instalar cuando quieras con ./install.sh")"
}

main "$@"
