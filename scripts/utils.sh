#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — day-to-day helpers
#  https://github.com/insanerask77/headscale-easy
#
#  Usage: ./scripts/utils.sh <command> [args]   (or: make <target>)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

print_info()    { echo -e "${BLUE}ℹ${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error()   { echo -e "${RED}✗${NC} $1" >&2; }

hs() { docker exec headscale headscale "$@"; }

# Headscale 0.29 wants numeric user ids; accept a name too
user_id() {
    local user="$1" id
    if [[ "$user" =~ ^[0-9]+$ ]]; then echo "$user"; return 0; fi
    id=$(hs users list --output json 2>/dev/null | tr -d ' \t\n' \
         | grep -oE "\"id\":[0-9]+,\"name\":\"${user}\"" | grep -oE '[0-9]+' | head -1)
    if [[ -z "$id" ]]; then print_error "No such user: $user"; return 1; fi
    echo "$id"
}

# -----------------------------------------------------------------------------
# Services
# -----------------------------------------------------------------------------

cmd_status() { cd "$PROJECT_DIR"; docker compose ps; }

cmd_logs() {
    cd "$PROJECT_DIR"
    if [[ -n "${1:-}" ]]; then docker compose logs -f "$1"; else docker compose logs -f; fi
}

cmd_restart() {
    cd "$PROJECT_DIR"
    if [[ -n "${1:-}" ]]; then docker compose restart "$1"; else docker compose restart; fi
    print_success "Restarted ${1:-all services}"
}

cmd_health() {
    local c state
    for c in headscale headscale-easy caddy authentik-server authentik-worker authentik-postgresql; do
        state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || echo "absent")
        case "$state" in
            healthy|running) echo -e "${GREEN}✓${NC} ${c}: ${state}" ;;
            starting)        echo -e "${YELLOW}…${NC} ${c}: ${state}" ;;
            absent)          [[ "$c" == authentik-* ]] || echo -e "${RED}✗${NC} ${c}: not running" ;;
            *)               echo -e "${RED}✗${NC} ${c}: ${state}" ;;
        esac
    done
}

cmd_shell() { docker exec -it "${1:-headscale-easy}" /bin/sh; }

cmd_update() {
    cd "$PROJECT_DIR"
    print_info "Pulling new images..."
    docker compose pull --ignore-pull-failures
    docker compose up -d
    print_success "Updated"
}

# -----------------------------------------------------------------------------
# Headscale
# -----------------------------------------------------------------------------

cmd_users_list()   { hs users list; }
cmd_users_create() {
    local name="${1:-}"
    [[ -z "$name" ]] && read -r -p "$(echo -e "${CYAN}?${NC} User name: ")" name
    hs users create "$name"
}
cmd_nodes_list() {
    if [[ -n "${1:-}" ]]; then hs nodes list --user "$(user_id "$1")"; else hs nodes list; fi
}
cmd_routes_list() { hs nodes list-routes; }

cmd_preauthkey_create() {
    local user="${1:-}" reusable="${2:-true}" expiration="${3:-24h}" id
    [[ -z "$user" ]] && read -r -p "$(echo -e "${CYAN}?${NC} User: ")" user
    id=$(user_id "$user")
    local args=(preauthkeys create --user "$id" --expiration "$expiration")
    [[ "$reusable" == "true" ]] && args+=(--reusable)
    hs "${args[@]}"
}

cmd_apikey_create() {
    local key
    key=$(hs apikeys create --expiration "${1:-90d}" | tr -d '\r\n')
    [[ "$key" =~ ^hskey- ]] || { print_error "Could not create the API key"; return 1; }
    echo -e "\n  ${BOLD}${key}${NC}\n"
    print_warning "Save it now: Headscale only shows it once."
}
cmd_apikey_list() { hs apikeys list; }

# -----------------------------------------------------------------------------
# Backups and configuration
# -----------------------------------------------------------------------------

cmd_backup() {
    # Same job as the scheduled one (backup container): consistent copy of
    # Headscale's database, keys, Authentik's database and the configuration.
    cd "$PROJECT_DIR"
    docker compose run --rm --no-deps --entrypoint /usr/local/bin/backup.sh backup
}

cmd_restore() {
    "$SCRIPT_DIR/restore.sh" "$@"
}

cmd_config_show() {
    [[ -f "$ENV_FILE" ]] || { print_error ".env not found: run ./install.sh"; exit 1; }
    sed -E 's/^([A-Z_]*(SECRET|PASSWORD|PASS|KEY|TOKEN)[A-Z_]*=).+/\1***hidden***/' "$ENV_FILE"
}

cmd_help() {
    cat <<EOF
Headscale Easy — helpers

Usage: ./scripts/utils.sh <command> [args]

Services
  status                         Container status
  logs [service]                 Follow logs (headscale, web, caddy, authentik-server...)
  restart [service]              Restart one or all services
  health                         Health of every container
  shell [container]              Shell in a container (default: headscale-easy)
  update                         Pull new images and recreate containers

Headscale
  users:list                     List users
  users:create [name]            Create a user
  nodes:list [user]              List machines (optionally of one user)
  routes:list                    List subnet routes and exit nodes
  preauth:create <user> [reusable] [expiration]
                                 Create an auth key (default: reusable, 24h)
  apikey:create [expiration]     Create an API key (default: 90d)
  apikey:list                    List API keys

Maintenance
  backup                         Back up now (also runs every day, see BACKUP_* in .env)
  restore <file> [--yes]         Restore a backup (stops and starts the stack)
  config:show                    Show .env with secrets hidden
EOF
}

main() {
    local command="${1:-help}"
    shift || true
    case "$command" in
        status)          cmd_status "$@" ;;
        logs)            cmd_logs "$@" ;;
        restart)         cmd_restart "$@" ;;
        health)          cmd_health "$@" ;;
        shell)           cmd_shell "$@" ;;
        update)          cmd_update "$@" ;;
        users:list)      cmd_users_list "$@" ;;
        users:create)    cmd_users_create "$@" ;;
        nodes:list)      cmd_nodes_list "$@" ;;
        routes:list)     cmd_routes_list "$@" ;;
        preauth:create)  cmd_preauthkey_create "$@" ;;
        apikey:create)   cmd_apikey_create "$@" ;;
        apikey:list)     cmd_apikey_list "$@" ;;
        backup)          cmd_backup "$@" ;;
        restore)         cmd_restore "$@" ;;
        config:show)     cmd_config_show "$@" ;;
        help|--help|-h)  cmd_help ;;
        *) print_error "Unknown command: $command"; echo; cmd_help; exit 1 ;;
    esac
}

main "$@"
