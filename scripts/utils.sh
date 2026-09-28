#!/usr/bin/env bash

# =============================================================================
# HEADSCALE + MI VPN - UTILIDADES
# =============================================================================
# Script con comandos útiles para gestionar Headscale + Mi VPN
# Uso: ./scripts/utils.sh <comando>
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# -----------------------------------------------------------------------------
# FUNCIONES DE UTILIDAD
# -----------------------------------------------------------------------------

print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

# -----------------------------------------------------------------------------
# COMANDOS
# -----------------------------------------------------------------------------

cmd_status() {
    echo -e "${CYAN}${BOLD}Estado de servicios:${NC}\n"
    cd "$PROJECT_DIR"
    docker compose ps
}

cmd_logs() {
    local service="${1:-}"
    cd "$PROJECT_DIR"

    if [[ -z "$service" ]]; then
        echo -e "${CYAN}${BOLD}Logs de todos los servicios:${NC}\n"
        docker compose logs -f
    else
        echo -e "${CYAN}${BOLD}Logs de $service:${NC}\n"
        docker compose logs -f "$service"
    fi
}

cmd_restart() {
    local service="${1:-}"
    cd "$PROJECT_DIR"

    if [[ -z "$service" ]]; then
        print_info "Reiniciando todos los servicios..."
        docker compose restart
        print_success "Servicios reiniciados"
    else
        print_info "Reiniciando $service..."
        docker compose restart "$service"
        print_success "$service reiniciado"
    fi
}

cmd_users_list() {
    print_info "Listando usuarios de Headscale:"
    docker exec headscale headscale users list
}

cmd_users_create() {
    local username="${1:-}"

    if [[ -z "$username" ]]; then
        read -p "$(echo -e "${CYAN}?${NC} Nombre del usuario: ")" username
    fi

    print_info "Creando usuario: $username"
    docker exec headscale headscale users create "$username"
    print_success "Usuario creado: $username"
}

cmd_nodes_list() {
    local user="${1:-}"

    if [[ -z "$user" ]]; then
        print_info "Listando todos los nodos:"
        docker exec headscale headscale nodes list
    else
        print_info "Listando nodos del usuario $user:"
        docker exec headscale headscale nodes list --user "$user"
    fi
}

cmd_preauthkey_create() {
    local user="${1:-}"
    local reusable="${2:-true}"
    local expiration="${3:-24h}"

    if [[ -z "$user" ]]; then
        read -p "$(echo -e "${CYAN}?${NC} Usuario: ")" user
    fi

    # Headscale 0.29 exige el ID numérico en --user. Se acepta un nombre por
    # comodidad y se resuelve aquí; si ya es un número se usa tal cual.
    local user_id="$user"
    if [[ ! "$user" =~ ^[0-9]+$ ]]; then
        user_id=$(docker exec headscale headscale users list --output json 2>/dev/null \
                  | tr -d ' \t\n' \
                  | grep -oE "\"id\":[0-9]+,\"name\":\"${user}\"" \
                  | grep -oE '[0-9]+' | head -1)
        if [[ -z "$user_id" ]]; then
            print_error "No existe el usuario '$user'"
            return 1
        fi
    fi

    print_info "Generando clave de pre-autenticación para $user (ID $user_id)..."

    local cmd="docker exec headscale headscale preauthkeys create --user $user_id --expiration $expiration"

    if [[ "$reusable" == "true" ]]; then
        cmd="$cmd --reusable"
    fi

    echo ""
    eval "$cmd"
    echo ""
    print_success "Clave generada"
}

cmd_apikey_create() {
    local expiration="${1:-90d}"

    print_info "Generando API key de Headscale (válida $expiration)..."

    local key
    key=$(docker exec headscale headscale apikeys create --expiration "$expiration" | tr -d '\r\n')

    if [[ ! "$key" =~ ^hskey- ]]; then
        print_error "No se pudo generar la API key"
        return 1
    fi

    echo ""
    echo -e "  ${BOLD}${key}${NC}"
    echo ""
    print_warning "Guárdala ahora: Headscale sólo la muestra al crearla"
    print_info "Sirve para la API y para entrar en el panel (/mi-vpn) como admin si PORTAL_API_KEY_LOGIN=true"
}

cmd_apikey_list() {
    print_info "API keys de Headscale:"
    echo ""
    docker exec headscale headscale apikeys list
}

cmd_routes_list() {
    print_info "Listando rutas:"
    docker exec headscale headscale routes list
}

cmd_backup() {
    local backup_dir="${1:-./backups}"
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="${backup_dir}/headscale-backup-${timestamp}.tar.gz"

    print_info "Creando backup en: $backup_file"

    # Crear directorio de backups si no existe
    mkdir -p "$backup_dir"

    # Crear backup
    cd "$PROJECT_DIR"
    tar -czf "$backup_file" \
        .env \
        headscale-config.yaml \
        Caddyfile \
        data/ 2>/dev/null || true

    # Backup de volumen de Headscale
    docker run --rm \
        -v headscale-data:/data \
        -v "$(pwd)/${backup_dir}":/backup \
        alpine tar czf "/backup/headscale-data-${timestamp}.tar.gz" -C /data .

    print_success "Backup completado:"
    echo "  - Archivos: $backup_file"
    echo "  - Volumen: ${backup_dir}/headscale-data-${timestamp}.tar.gz"
}

cmd_update() {
    print_info "Actualizando imágenes de Docker..."

    cd "$PROJECT_DIR"

    # Cargar .env
    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE"
        set +a
    fi

    # Pull de nuevas imágenes
    docker compose pull

    # Recrear contenedores
    print_info "Recreando contenedores con nuevas imágenes..."
    docker compose up -d

    print_success "Actualización completada"
}

cmd_health() {
    echo -e "${CYAN}${BOLD}Estado de salud de los servicios:${NC}\n"

    cd "$PROJECT_DIR"

    # Verificar cada servicio
    for service in headscale mi-vpn caddy; do
        if docker compose ps | grep -q "$service"; then
            local health=$(docker inspect --format='{{.State.Health.Status}}' "$service" 2>/dev/null || echo "no healthcheck")

            case "$health" in
                healthy)
                    echo -e "${GREEN}✓${NC} $service: ${GREEN}healthy${NC}"
                    ;;
                unhealthy)
                    echo -e "${RED}✗${NC} $service: ${RED}unhealthy${NC}"
                    ;;
                starting)
                    echo -e "${YELLOW}⏳${NC} $service: ${YELLOW}starting${NC}"
                    ;;
                "no healthcheck")
                    echo -e "${BLUE}ℹ${NC} $service: ${BLUE}no healthcheck${NC}"
                    ;;
                *)
                    echo -e "${RED}✗${NC} $service: ${RED}not running${NC}"
                    ;;
            esac
        else
            echo -e "${YELLOW}⚠${NC} $service: ${YELLOW}not configured${NC}"
        fi
    done
}

cmd_shell() {
    local service="${1:-headscale}"

    print_info "Abriendo shell en $service..."
    docker exec -it "$service" /bin/sh
}

cmd_config_show() {
    if [ ! -f "$ENV_FILE" ]; then
        print_error "No se encontró archivo .env"
        exit 1
    fi

    echo -e "${CYAN}${BOLD}Configuración actual (.env):${NC}\n"

    # Mostrar .env con secretos ofuscados
    while IFS= read -r line; do
        if [[ "$line" =~ ^[A-Z_]+=.* ]]; then
            local key="${line%%=*}"
            local value="${line#*=}"

            # Ofuscar secretos
            if [[ "$key" =~ (SECRET|PASSWORD|KEY|TOKEN) ]]; then
                echo "$key=***HIDDEN***"
            else
                echo "$line"
            fi
        else
            echo "$line"
        fi
    done < "$ENV_FILE"
}

cmd_help() {
    cat << EOF
${CYAN}${BOLD}Utilidades para Headscale + Mi VPN${NC}

${BOLD}Uso:${NC}
  ./scripts/utils.sh <comando> [argumentos]

${BOLD}Comandos disponibles:${NC}

${CYAN}Gestión de servicios:${NC}
  status                    - Ver estado de servicios
  logs [servicio]          - Ver logs (all, headscale, portal, caddy)
  restart [servicio]       - Reiniciar servicios
  health                   - Verificar estado de salud
  shell [servicio]         - Abrir shell en contenedor (default: headscale)
  update                   - Actualizar imágenes y recrear contenedores

${CYAN}Gestión de Headscale:${NC}
  users:list               - Listar usuarios
  users:create [nombre]    - Crear usuario
  nodes:list [usuario]     - Listar nodos (opcionalmente filtrar por usuario)
  routes:list              - Listar rutas
  preauth:create <usuario> [reusable] [expiration]
                          - Crear clave de pre-autenticación
                            Ejemplos:
                              utils.sh preauth:create admin
                              utils.sh preauth:create admin true 7d   (acepta nombre o ID)

  apikey:create [expiration]
                          - Crear API key (API de Headscale y login de admin en el panel)
                            (default: 90d)
  apikey:list             - Listar API keys (sólo muestra prefijos)

${CYAN}Utilidades:${NC}
  backup [directorio]      - Crear backup (default: ./backups)
  config:show              - Mostrar configuración actual (.env)
  help                     - Mostrar esta ayuda

${BOLD}Ejemplos:${NC}
  ./scripts/utils.sh status
  ./scripts/utils.sh logs headscale
  ./scripts/utils.sh users:create admin
  ./scripts/utils.sh preauth:create admin true 24h
  ./scripts/utils.sh backup /ruta/a/backups
  ./scripts/utils.sh update

EOF
}

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        status)
            cmd_status "$@"
            ;;
        logs)
            cmd_logs "$@"
            ;;
        restart)
            cmd_restart "$@"
            ;;
        health)
            cmd_health "$@"
            ;;
        shell)
            cmd_shell "$@"
            ;;
        update)
            cmd_update "$@"
            ;;
        users:list)
            cmd_users_list "$@"
            ;;
        users:create)
            cmd_users_create "$@"
            ;;
        nodes:list)
            cmd_nodes_list "$@"
            ;;
        routes:list)
            cmd_routes_list "$@"
            ;;
        preauth:create)
            cmd_preauthkey_create "$@"
            ;;
        apikey:create)
            cmd_apikey_create "$@"
            ;;
        apikey:list)
            cmd_apikey_list "$@"
            ;;
        backup)
            cmd_backup "$@"
            ;;
        config:show)
            cmd_config_show "$@"
            ;;
        help|--help|-h)
            cmd_help
            ;;
        *)
            print_error "Comando desconocido: $command"
            echo ""
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
