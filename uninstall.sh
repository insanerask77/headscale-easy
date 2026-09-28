#!/usr/bin/env bash

# =============================================================================
# HEADSCALE + MI VPN - DESINSTALADOR
# =============================================================================
# Script para detener y limpiar la instalación de Headscale + Mi VPN
# Uso: ./uninstall.sh [--purge]
#
# Opciones:
#   --purge    Elimina también los volúmenes y datos persistentes
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# VARIABLES GLOBALES
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
PURGE=false

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# -----------------------------------------------------------------------------
# FUNCIONES DE UTILIDAD
# -----------------------------------------------------------------------------

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}${BOLD}  $1${NC}"
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

ask_yes_no() {
    local prompt="$1"
    local default="${2:-n}"
    local response

    if [[ "$default" == "y" ]]; then
        prompt="$prompt [S/n]"
    else
        prompt="$prompt [s/N]"
    fi

    while true; do
        read -p "$(echo -e "${CYAN}?${NC} $prompt: ")" response
        response="${response:-$default}"
        response=$(echo "$response" | tr '[:upper:]' '[:lower:]')

        case "$response" in
            y|s|yes|si|sí)
                return 0
                ;;
            n|no)
                return 1
                ;;
            *)
                print_warning "Por favor responde 's' (sí) o 'n' (no)"
                ;;
        esac
    done
}

# -----------------------------------------------------------------------------
# FUNCIONES DE DESINSTALACIÓN
# -----------------------------------------------------------------------------

show_warning() {
    clear

    echo -e "${RED}${BOLD}"
    cat << "EOF"
╔═══════════════════════════════════════════════════════════════════════════╗
║                                                                           ║
║                          ⚠  ADVERTENCIA  ⚠                               ║
║                                                                           ║
║                  DESINSTALADOR DE HEADSCALE + MI VPN                      ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    echo ""
    print_warning "Este script detendrá y eliminará los contenedores de Headscale + Mi VPN"
    echo ""

    if $PURGE; then
        print_error "MODO PURGE ACTIVADO: También se eliminarán:"
        echo "  • Todos los volúmenes de Docker (datos de Headscale, certificados, etc.)"
        echo "  • Archivos de configuración generados (.env, *.yaml, Caddyfile)"
        echo "  • Directorio de datos (./data/)"
        echo ""
        print_error "ESTA ACCIÓN ES IRREVERSIBLE Y PERDERÁS TODOS LOS DATOS"
    else
        print_info "Los datos persistentes se mantendrán (volúmenes, configuración)"
        print_info "Para eliminar también los datos, ejecuta: ./uninstall.sh --purge"
    fi

    echo ""
}

stop_services() {
    print_header "DETENIENDO SERVICIOS"

    # Cargar .env si existe (aporta NETWORK_NAME y los puertos que Compose
    # necesita interpolar para poder leer el fichero)
    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE"
        set +a
    fi

    # Detener y eliminar contenedores. Ya no hay profiles: Caddy forma parte
    # del stack siempre, así que 'down' a secas se lleva todo.
    print_info "Deteniendo contenedores..."
    if docker compose down; then
        print_success "Contenedores detenidos y eliminados"
    else
        print_warning "No se pudo detener algunos contenedores (puede que ya estén detenidos)"
    fi
}

remove_volumes() {
    if ! $PURGE; then
        return 0
    fi

    print_header "ELIMINANDO VOLÚMENES"

    print_warning "Eliminando volúmenes de Docker..."

    # Lista de volúmenes a eliminar
    local volumes=(
        "headscale-data"
        "headscale-socket"
        "caddy-data"
        "caddy-config"
        "authentik-db"
        "authentik-data"
        "authentik-media"
    )

    for volume in "${volumes[@]}"; do
        if docker volume ls | grep -q "$volume"; then
            if docker volume rm "$volume" 2>/dev/null; then
                print_success "Volumen eliminado: $volume"
            else
                print_warning "No se pudo eliminar el volumen: $volume (puede estar en uso)"
            fi
        fi
    done
}

remove_network() {
    if ! $PURGE; then
        return 0
    fi

    print_header "ELIMINANDO RED"

    local network_name="${NETWORK_NAME:-headscale-net}"

    if docker network ls | grep -q "$network_name"; then
        if docker network rm "$network_name" 2>/dev/null; then
            print_success "Red eliminada: $network_name"
        else
            print_warning "No se pudo eliminar la red: $network_name (puede estar en uso)"
        fi
    fi
}

remove_configs() {
    if ! $PURGE; then
        return 0
    fi

    print_header "ELIMINANDO ARCHIVOS DE CONFIGURACIÓN"

    local files=(
        "$ENV_FILE"
        "${SCRIPT_DIR}/headscale-config.yaml"
        # De instalaciones anteriores, cuando el panel era Headplane
        "${SCRIPT_DIR}/headplane-config.yaml"
        "${SCRIPT_DIR}/Caddyfile"
    )

    for file in "${files[@]}"; do
        if [ -f "$file" ]; then
            rm -f "$file"
            print_success "Eliminado: $(basename "$file")"
        fi
    done
}

remove_data_dir() {
    if ! $PURGE; then
        return 0
    fi

    print_header "ELIMINANDO DIRECTORIO DE DATOS"

    local data_dir="${SCRIPT_DIR}/data"

    if [ -d "$data_dir" ]; then
        print_warning "Eliminando: $data_dir"
        rm -rf "$data_dir"
        print_success "Directorio de datos eliminado"
    fi
}

show_completion() {
    print_header "DESINSTALACIÓN COMPLETADA"

    echo ""
    print_success "Headscale + Mi VPN han sido desinstalados"
    echo ""

    if $PURGE; then
        print_info "Todos los datos y configuraciones han sido eliminados"
        print_info "Para volver a instalar, ejecuta: ./install.sh"
    else
        print_info "Los datos persistentes se han mantenido"
        print_info "Para eliminar también los datos, ejecuta: ./uninstall.sh --purge"
        print_info "Para volver a instalar con la configuración existente, ejecuta: ./install.sh"
    fi

    echo ""
}

# -----------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# -----------------------------------------------------------------------------

main() {
    # Parsear argumentos
    while [[ $# -gt 0 ]]; do
        case $1 in
            --purge)
                PURGE=true
                shift
                ;;
            -h|--help)
                echo "Uso: $0 [--purge]"
                echo ""
                echo "Opciones:"
                echo "  --purge    Elimina también los volúmenes y datos persistentes"
                echo "  -h, --help Muestra esta ayuda"
                exit 0
                ;;
            *)
                print_error "Opción desconocida: $1"
                echo "Usa -h o --help para ver las opciones disponibles"
                exit 1
                ;;
        esac
    done

    # Mostrar advertencia
    show_warning

    # Pedir confirmación
    if ! ask_yes_no "¿Estás seguro de que deseas continuar?" "n"; then
        print_info "Desinstalación cancelada"
        exit 0
    fi

    # Confirmación adicional para purge
    if $PURGE; then
        echo ""
        print_error "ÚLTIMA ADVERTENCIA: Se eliminarán TODOS los datos de forma irreversible"
        if ! ask_yes_no "¿Estás COMPLETAMENTE seguro?" "n"; then
            print_info "Desinstalación cancelada"
            exit 0
        fi
    fi

    # Ejecutar desinstalación
    stop_services
    remove_volumes
    remove_network
    remove_configs
    remove_data_dir

    # Mostrar completado
    show_completion
}

# Ejecutar main
main "$@"
