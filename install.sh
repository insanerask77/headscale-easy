#!/usr/bin/env bash

# =============================================================================
# HEADSCALE + MI VPN - INSTALADOR INTERACTIVO
# =============================================================================
# Instalador todo-en-uno para Headscale (control plane) + Mi VPN (panel web)
# Uso: ./install.sh
#
# Características:
# - Detección y validación de dependencias
# - Configuración interactiva con valores por defecto sensatos
# - Generación automática de secretos
# - Soporte para SSL (Let's Encrypt o certificado autofirmado)
# - Integración OIDC opcional
# - Idempotente: puede ejecutarse múltiples veces para reconfigurar
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# VARIABLES GLOBALES
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
DATA_DIR="${SCRIPT_DIR}/data"
TEMPLATES_DIR="${SCRIPT_DIR}/templates"

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

# Imprimir mensajes con color
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

# Preguntar sí/no con valor por defecto
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

# Preguntar input con valor por defecto y validación opcional
ask_input() {
    local prompt="$1"
    local default="$2"
    local validate_func="${3:-}"
    local value

    if [[ -n "$default" ]]; then
        prompt="$prompt [${default}]"
    fi

    while true; do
        read -p "$(echo -e "${CYAN}?${NC} $prompt: ")" value
        value="${value:-$default}"

        # Si hay función de validación, usarla
        if [[ -n "$validate_func" ]] && type "$validate_func" &>/dev/null; then
            if $validate_func "$value"; then
                echo "$value"
                return 0
            else
                print_warning "Valor inválido, intenta de nuevo"
                continue
            fi
        fi

        # Si no está vacío, aceptar
        if [[ -n "$value" ]]; then
            echo "$value"
            return 0
        else
            print_warning "Este campo no puede estar vacío"
        fi
    done
}

# Menú numerado. Imprime en stderr para no contaminar la captura por $(...)
# y devuelve por stdout la clave elegida. Uso:
#   ask_choice "Pregunta" "actual" "clave1|Título|Descripción" "clave2|..."
ask_choice() {
    local prompt="$1"
    local current="$2"
    shift 2
    local options=("$@")
    local n=${#options[@]}
    local default_idx=1
    local i key title desc choice

    for i in "${!options[@]}"; do
        IFS='|' read -r key title desc <<< "${options[$i]}"
        [[ "$key" == "$current" ]] && default_idx=$((i + 1))
    done

    {
        echo ""
        echo -e "${CYAN}${BOLD}${prompt}${NC}"
        echo ""
        for i in "${!options[@]}"; do
            IFS='|' read -r key title desc <<< "${options[$i]}"
            echo -e "  ${BOLD}$((i + 1))${NC}) ${BOLD}${title}${NC}"
            echo -e "     ${desc}"
        done
        echo ""
    } >&2

    while true; do
        read -r -p "$(echo -e "${CYAN}?${NC} Elige una opción [1-${n}] [${default_idx}]: ")" choice >&2
        choice="${choice:-$default_idx}"

        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= n )); then
            IFS='|' read -r key title desc <<< "${options[$((choice - 1))]}"
            echo "$key"
            return 0
        fi
        print_warning "Opción inválida, elige un número entre 1 y ${n}" >&2
    done
}

# Validar dominio o IP
validate_domain_or_ip() {
    local input="$1"

    # Validar IP (simplificado)
    if [[ "$input" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        return 0
    fi

    # Validar dominio (simplificado)
    if [[ "$input" =~ ^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
        return 0
    fi

    # Validar localhost
    if [[ "$input" == "localhost" ]]; then
        return 0
    fi

    return 1
}

# Validar puerto
validate_port() {
    local port="$1"

    if [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; then
        return 0
    fi

    return 1
}

# Validar email
validate_email() {
    local email="$1"

    if [[ "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        return 0
    fi

    return 1
}

# Validar nombre alfanumérico
validate_alphanumeric() {
    local name="$1"

    if [[ "$name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        return 0
    fi

    return 1
}

# Validar URL
validate_url() {
    local url="$1"

    if [[ "$url" =~ ^https?:// ]]; then
        return 0
    fi

    return 1
}

# Validar IP opcional: vacío o una IPv4
validate_optional_ip() {
    local input="$1"
    [[ -z "$input" ]] && return 0
    [[ "$input" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]
}

# Generar secreto aleatorio
generate_secret() {
    local length="${1:-64}"
    openssl rand -hex "$length" 2>/dev/null || \
        head -c "$length" /dev/urandom | xxd -p | tr -d '\n'
}

# -----------------------------------------------------------------------------
# FUNCIONES DE VERIFICACIÓN DE DEPENDENCIAS
# -----------------------------------------------------------------------------

check_command() {
    command -v "$1" &>/dev/null
}

install_docker() {
    print_info "Intentando instalar Docker..."

    # Detectar distribución
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
    else
        print_error "No se pudo detectar la distribución del sistema"
        return 1
    fi

    case "$OS" in
        ubuntu|debian)
            print_info "Instalando Docker en Debian/Ubuntu..."
            sudo apt-get update
            sudo apt-get install -y ca-certificates curl gnupg
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            echo \
              "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS \
              $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
              sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        fedora|rhel|centos)
            print_info "Instalando Docker en Fedora/RHEL/CentOS..."
            sudo dnf -y install dnf-plugins-core
            sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            sudo systemctl start docker
            sudo systemctl enable docker
            ;;
        arch)
            print_info "Instalando Docker en Arch Linux..."
            sudo pacman -Sy --noconfirm docker docker-compose
            sudo systemctl start docker
            sudo systemctl enable docker
            ;;
        *)
            print_error "Distribución no soportada para instalación automática: $OS"
            print_info "Por favor instala Docker manualmente: https://docs.docker.com/engine/install/"
            return 1
            ;;
    esac

    # Agregar usuario actual al grupo docker
    if ! groups | grep -q docker; then
        print_info "Agregando usuario actual al grupo docker..."
        sudo usermod -aG docker "$USER"
        print_warning "Debes cerrar sesión y volver a entrar para que el cambio de grupo surta efecto"
        print_warning "O ejecuta: newgrp docker"
    fi

    print_success "Docker instalado correctamente"
    return 0
}

check_dependencies() {
    print_header "VERIFICACIÓN DE DEPENDENCIAS"

    local missing_deps=()

    # Verificar Docker
    if ! check_command docker; then
        print_warning "Docker no está instalado"
        if ask_yes_no "¿Deseas instalar Docker automáticamente?" "y"; then
            if ! install_docker; then
                missing_deps+=("docker")
            fi
        else
            missing_deps+=("docker")
        fi
    else
        print_success "Docker está instalado"

        # Verificar que Docker daemon esté corriendo
        if ! docker info &>/dev/null; then
            print_warning "Docker daemon no está corriendo"
            print_info "Intentando iniciar Docker..."
            sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || {
                print_error "No se pudo iniciar Docker daemon. Inícialo manualmente."
                missing_deps+=("docker-daemon")
            }
        fi
    fi

    # Verificar Docker Compose (plugin)
    if ! docker compose version &>/dev/null; then
        print_warning "Docker Compose plugin no está instalado"
        missing_deps+=("docker-compose")
    else
        print_success "Docker Compose plugin está instalado"
    fi

    # Verificar openssl (para generar secretos)
    if ! check_command openssl; then
        print_warning "OpenSSL no está instalado (necesario para generar secretos)"
        missing_deps+=("openssl")
    fi

    # Si hay dependencias faltantes, salir
    if [ ${#missing_deps[@]} -gt 0 ]; then
        print_error "Faltan las siguientes dependencias: ${missing_deps[*]}"
        print_info "Por favor instálalas manualmente y vuelve a ejecutar el instalador"
        exit 1
    fi

    print_success "Todas las dependencias están instaladas"
}

# -----------------------------------------------------------------------------
# FUNCIONES DE CONFIGURACIÓN
# -----------------------------------------------------------------------------

load_existing_config() {
    if [ -f "$ENV_FILE" ]; then
        print_info "Encontrado archivo de configuración existente"
        if ask_yes_no "¿Deseas cargar la configuración existente?" "y"; then
            # Cargar variables existentes
            set -a
            source "$ENV_FILE"
            set +a
            return 0
        fi
    fi
    return 1
}

# Este instalador tiene UN solo modo de despliegue: Caddy delante de Headscale
# y el panel Mi VPN, enrutando un único dominio (/ -> control plane, /mi-vpn ->
# panel).
# La única bifurcación es quién pone el HTTPS, y eso es lo que decide SSL_MODE:
#
#   letsencrypt  Caddy pide el certificado a Let's Encrypt.
#   selfsigned   Caddy firma con su CA interna.
#   front        Lo pone otro proxy por delante (NPM, nginx, Traefik u otro
#                Caddy). Este Caddy sirve HTTP y el instalador escupe el
#                snippet de configuración para ese proxy.
#   none         No hay HTTPS. Para localhost, LAN de confianza o un acceso
#                que ya viaja por otra VPN.
configure_network() {
    print_header "CONFIGURACIÓN DE RED Y ACCESO"

    print_info "Dominio o IP con el que se accede al stack. Caddy enruta ese"
    print_info "único nombre: / -> Headscale, /mi-vpn -> panel."
    print_info "Ejemplos: vpn.midominio.com, 192.168.1.100, localhost"
    DOMAIN=$(ask_input "Dominio o IP" "${DOMAIN:-vpn.example.com}" "validate_domain_or_ip")

    # Let's Encrypt no emite para IPs ni para localhost: en esos casos ni se
    # ofrece, en vez de dejar que el reto ACME falle a mitad de instalación.
    local tls_options=()
    if [[ "$DOMAIN" =~ ^[0-9.]+$ ]] || [[ "$DOMAIN" == "localhost" ]]; then
        print_warning "Has indicado una IP o localhost: Let's Encrypt no emite certificados para eso"
    else
        tls_options+=("letsencrypt|Caddy, con Let's Encrypt|Certificado público y de confianza para ${DOMAIN}. Requiere que su DNS ya apunte aquí y que los puertos 80 y 443 lleguen desde internet.")
    fi
    tls_options+=("selfsigned|Caddy, con certificado autofirmado|HTTPS sin dependencias externas. Hay que instalar la CA de Caddy en cada cliente Tailscale o no conectarán.")
    tls_options+=("front|Un proxy que ya tienes por delante|NPM, nginx, Traefik u otro Caddy terminan el TLS y reenvían aquí. Se genera el snippet listo para ese proxy.")
    tls_options+=("none|Nadie: sólo HTTP|Caddy enruta igual, pero sin cifrar: http://${DOMAIN}. Para localhost, LAN de confianza o un acceso que ya va por VPN.")

    SSL_MODE=$(ask_choice "¿Quién pone el HTTPS?" "${SSL_MODE:-letsencrypt}" "${tls_options[@]}")

    case "$SSL_MODE" in
        letsencrypt)
            URL_SCHEME="https"
            FRONT_PROXY=""
            print_info "Let's Encrypt necesita un email para los avisos de renovación"
            ACME_EMAIL=$(ask_input "Email para Let's Encrypt" "${ACME_EMAIL:-admin@${DOMAIN}}" "validate_email")
            ;;
        selfsigned)
            URL_SCHEME="https"
            FRONT_PROXY=""
            print_info "Se usará un certificado autofirmado"
            print_warning "Tendrás que instalar la CA en cada cliente Tailscale (se exporta al final)"
            ;;
        front)
            # El proxy de delante habla HTTPS con el mundo aunque aquí el salto
            # sea HTTP: SERVER_URL y la cookie de sesión se deciden por lo que
            # ve el cliente, no por lo que escucha Caddy.
            URL_SCHEME="https"
            FRONT_PROXY=$(ask_choice "¿Qué proxy tienes delante?" "${FRONT_PROXY:-npm}" \
                "npm|Nginx Proxy Manager|Guía de los campos del Proxy Host más el bloque para la caja Advanced." \
                "nginx|Nginx que gestionas tú|Un server{} completo listo para /etc/nginx/conf.d/." \
                "traefik|Traefik|Configuración dinámica en YAML para el file provider." \
                "caddy|Otro Caddy|El Caddyfile del proxy de borde.")

            echo ""
            print_info "Dirección de ESTA máquina tal y como la ve el proxy: es a donde reenvía."
            local guess
            guess=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)
            BACKEND_HOST=$(ask_input "IP o hostname de esta máquina" \
                                     "${BACKEND_HOST:-${guess:-127.0.0.1}}")
            ;;
        none)
            URL_SCHEME="http"
            FRONT_PROXY=""
            print_info "Caddy escuchará en HTTP y enrutará / y /admin sin certificado"
            ;;
    esac

    if [[ "$URL_SCHEME" == "http" ]]; then
        print_warning "ADVERTENCIA: el plano de control viajará sin cifrar"
        print_warning "No expongas esto a internet"
    fi

    print_success "Configuración de red completada"
}

configure_ports() {
    print_header "CONFIGURACIÓN DE PUERTOS"

    print_info "Enter para aceptar los valores por defecto."

    if [[ "$SSL_MODE" == "letsencrypt" || "$SSL_MODE" == "selfsigned" ]]; then
        HTTP_PORT=$(ask_input "Puerto HTTP de Caddy (reto ACME y redirección a HTTPS)" \
                              "${HTTP_PORT:-80}" "validate_port")
        HTTPS_PORT=$(ask_input "Puerto HTTPS de Caddy" "${HTTPS_PORT:-443}" "validate_port")
    else
        # Sin certificado Caddy no escucha en 443: preguntar por ese puerto
        # sería ofrecer uno que nadie va a abrir.
        [[ "$SSL_MODE" == "front" ]] && \
            print_info "Es el puerto donde el proxy de delante encontrará a Caddy"
        HTTP_PORT=$(ask_input "Puerto HTTP de Caddy" "${HTTP_PORT:-80}" "validate_port")
        HTTPS_PORT="443"
    fi

    # El panel y la API de Headscale NO se publican en el host: sólo los
    # alcanza Caddy por la red interna de Docker.

    HEADSCALE_GRPC_PORT=$(ask_input "Puerto gRPC de Headscale (interno)" "${HEADSCALE_GRPC_PORT:-50443}" "validate_port")
    HEADSCALE_HTTP_PORT=$(ask_input "Puerto HTTP API de Headscale (interno)" "${HEADSCALE_HTTP_PORT:-8080}" "validate_port")
    HEADSCALE_DERP_PORT=$(ask_input "Puerto DERP/STUN de Headscale (UDP, debe ser accesible)" "${HEADSCALE_DERP_PORT:-3478}" "validate_port")
    HEADSCALE_METRICS_PORT=$(ask_input "Puerto Metrics de Headscale (interno)" "${HEADSCALE_METRICS_PORT:-9090}" "validate_port")

    print_success "Configuración de puertos completada"
}

# Las URLs públicas dependen del dominio Y de los puertos, así que sólo pueden
# calcularse después de configure_ports.
#
# Importante: son las URLs que ve el MUNDO, no las internas. Headscale y
# el panel siguen hablando HTTP por la red de Docker, pero anuncian https://
# cuando es eso lo que resuelve el cliente.
compute_public_urls() {
    local suffix=""

    case "$SSL_MODE" in
        letsencrypt|selfsigned)
            [[ "$HTTPS_PORT" != "443" ]] && suffix=":${HTTPS_PORT}"
            HEADSCALE_PUBLIC_URL="https://${DOMAIN}${suffix}"
            ;;
        front)
            # El proxy de delante escucha en el 443 estándar. Su puerto no tiene
            # por qué coincidir con HTTP_PORT, que es sólo el de esta máquina.
            HEADSCALE_PUBLIC_URL="https://${DOMAIN}"
            ;;
        none)
            [[ "$HTTP_PORT" != "80" ]] && suffix=":${HTTP_PORT}"
            HEADSCALE_PUBLIC_URL="http://${DOMAIN}${suffix}"
            ;;
    esac


    # SERVER_URL = URL del control plane; es la que usan los clientes Tailscale
    SERVER_URL="$HEADSCALE_PUBLIC_URL"

    echo ""
    print_info "URL del control plane (Headscale): ${HEADSCALE_PUBLIC_URL}"
    print_info "URL del panel (Mi VPN): ${HEADSCALE_PUBLIC_URL}/mi-vpn/"
}

configure_tailnet() {
    print_header "CONFIGURACIÓN DE LA RED TAILNET"

    print_info "Configura los parámetros de tu red privada virtual (Tailnet)"

    TAILNET_NAME=$(ask_input "Nombre de la organización/tailnet (alfanumérico, sin espacios)" \
                             "${TAILNET_NAME:-myorg}" "validate_alphanumeric")

    ADMIN_USER=$(ask_input "Nombre del usuario administrador inicial" \
                           "${ADMIN_USER:-admin}" "validate_alphanumeric")

    IP_PREFIXES_V4=$(ask_input "Rango IPv4 para clientes (CIDR)" "${IP_PREFIXES_V4:-100.64.0.0/10}")
    IP_PREFIXES_V6=$(ask_input "Rango IPv6 para clientes (CIDR)" "${IP_PREFIXES_V6:-fd7a:115c:a1e0::/48}")

    DATA_DIR=$(ask_input "Ruta de persistencia de datos" "${DATA_DIR:-./data}")

    LOG_LEVEL=$(ask_input "Nivel de log (trace/debug/info/warn/error)" "${LOG_LEVEL:-info}")

    print_success "Configuración de Tailnet completada"
}

# Headscale no tiene usuarios con contraseña propios: sin OIDC el panel sólo
# acepta una API key y los dispositivos se registran con pre-auth keys. Para
# login con usuario/contraseña o con Google hace falta un proveedor OIDC:
#
#   none       Sin OIDC. Mi VPN pide la API key de Headscale (sólo admins).
#   authentik  Authentik dentro de este stack, bajo /authentik/ del mismo
#              dominio. Usuarios locales con contraseña y, opcionalmente,
#              "Login con Google". Se configura solo mediante un blueprint.
#   external   Un proveedor que ya tienes (Keycloak, Authelia, Google...).
configure_auth() {
    print_header "AUTENTICACIÓN DE USUARIOS"

    # Compatibilidad con .env anteriores, que sólo tenían ENABLE_OIDC
    local current="${AUTH_PROVIDER:-}"
    if [[ -z "$current" ]]; then
        current=$([[ "${ENABLE_OIDC:-false}" == "true" ]] && echo "external" || echo "none")
    fi

    PREV_AUTH_PROVIDER="$current"

    AUTH_PROVIDER=$(ask_choice "¿Cómo inician sesión los usuarios?" "$current" \
        "none|Sólo API key|El panel pide la API key de Headscale (sólo administradores); los dispositivos se registran con pre-auth keys. Sin cuentas de usuario." \
        "authentik|Authentik integrado|Usuarios con contraseña y login con Google opcional, en ${HEADSCALE_PUBLIC_URL}/authentik/. Añade 3 contenedores (~1 GB de RAM)." \
        "external|Proveedor OIDC propio|Keycloak, Authelia, Google directo u otro que ya tengas funcionando.")

    case "$AUTH_PROVIDER" in
        none)      configure_auth_none ;;
        authentik) configure_auth_authentik ;;
        external)  configure_auth_external ;;
    esac

    ENABLE_OIDC=$([[ "$AUTH_PROVIDER" == "none" ]] && echo "false" || echo "true")

    # Con OIDC, el panel puede aceptar además la API key de Headscale como
    # acceso de administrador de emergencia (si el proveedor se cae, sin ella
    # no se entra hasta cambiar PORTAL_API_KEY_LOGIN). Por defecto, sólo SSO.
    # .env anteriores la llamaban HEADPLANE_API_KEY_LOGIN.
    PORTAL_API_KEY_LOGIN="${PORTAL_API_KEY_LOGIN:-${HEADPLANE_API_KEY_LOGIN:-false}}"
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        echo ""
        print_info "El panel puede aceptar también la API key de Headscale para entrar como"
        print_info "administrador. Sirve de acceso de emergencia si el proveedor OIDC falla."
        if ask_yes_no "¿Permitir también el login con API key?" \
                      "$([[ "$PORTAL_API_KEY_LOGIN" == "true" ]] && echo y || echo n)"; then
            PORTAL_API_KEY_LOGIN="true"
        else
            PORTAL_API_KEY_LOGIN="false"
        fi
    else
        # Sin OIDC la API key es la ÚNICA forma de entrar
        PORTAL_API_KEY_LOGIN="true"
    fi

    echo ""
    print_info "Aislamiento de red: cada usuario sólo alcanza SUS dispositivos"
    print_info "(admins incluidos). Sin él, cualquier dispositivo llega a cualquier otro."
    if ask_yes_no "¿Aislar la red por usuario?" \
                  "$([[ "${NETWORK_ISOLATION:-true}" == "true" ]] && echo y || echo n)"; then
        NETWORK_ISOLATION="true"
    else
        NETWORK_ISOLATION="false"
    fi

    print_success "Configuración de autenticación completada"
}

configure_auth_none() {
    OIDC_ISSUER_URL=""
    OIDC_CLIENT_ID=""
    OIDC_CLIENT_SECRET=""
    OIDC_SCOPE="openid profile email"
    OIDC_EMAIL_CLAIM="email"
    GOOGLE_CLIENT_ID=""
    GOOGLE_CLIENT_SECRET=""
    PORTAL_ADMIN_EMAILS=""
}

configure_auth_external() {
    print_info "Ejemplos de Issuer URL:"
    print_info "  - Keycloak: https://auth.example.com/realms/master"
    print_info "  - Google:   https://accounts.google.com"
    print_info "Registra en el proveedor estas dos Redirect URIs:"
    print_info "  ${HEADSCALE_PUBLIC_URL}/oidc/callback     (Headscale)"
    print_info "  ${HEADSCALE_PUBLIC_URL}/mi-vpn/callback   (panel Mi VPN)"

    # Si se viene de Authentik, sus valores no sirven para otro proveedor
    if [[ "${OIDC_ISSUER_URL:-}" == */authentik/application/o/* ]]; then
        OIDC_ISSUER_URL=""
        OIDC_CLIENT_SECRET=""
    fi

    OIDC_ISSUER_URL=$(ask_input "Issuer URL del proveedor OIDC" "${OIDC_ISSUER_URL:-}" "validate_url")
    # El panel usa el MISMO cliente que Headscale: así el 'sub' coincide y
    # puede localizar a cada usuario en Headscale sin depender del nombre.
    OIDC_CLIENT_ID=$(ask_input "Client ID (el mismo para Headscale y el panel)" "${OIDC_CLIENT_ID:-headscale}")
    OIDC_CLIENT_SECRET=$(ask_input "Client Secret" "${OIDC_CLIENT_SECRET:-}")
    OIDC_SCOPE=$(ask_input "Scopes OIDC (separados por espacios)" "${OIDC_SCOPE:-openid profile email}")
    OIDC_EMAIL_CLAIM=$(ask_input "Claim del email" "${OIDC_EMAIL_CLAIM:-email}")
    GOOGLE_CLIENT_ID=""
    GOOGLE_CLIENT_SECRET=""

    # Quién administra: el proveedor externo no tiene los grupos de este stack
    echo ""
    print_info "Administradores del panel: emails separados por comas. El resto de"
    print_info "usuarios sólo verán sus propios dispositivos."
    PORTAL_ADMIN_EMAILS=$(ask_input "Emails de administradores" "${PORTAL_ADMIN_EMAILS:-${ACME_EMAIL:-}}")
}

configure_auth_authentik() {
    # Headscale y el panel validan el issuer contra la URL pública, así que
    # tienen que poder alcanzarla desde dentro de sus contenedores. "localhost"
    # dentro de un contenedor es el propio contenedor: no hay forma de que
    # llegue a Caddy.
    if [[ "$DOMAIN" == "localhost" ]]; then
        print_error "Authentik no funciona con DOMAIN=localhost"
        print_info "Headscale tiene que alcanzar ${HEADSCALE_PUBLIC_URL}/authentik/ desde su"
        print_info "contenedor, y ahí 'localhost' es el propio contenedor."
        print_info "Reejecuta el instalador con la IP de esta máquina en la LAN o un dominio."
        exit 1
    fi

    # Todo lo de Authentik se deriva de la URL pública: un único dominio.
    OIDC_ISSUER_URL="${HEADSCALE_PUBLIC_URL}/authentik/application/o/headscale/"
    OIDC_CLIENT_ID="headscale"
    # Se conserva el de .env al reconfigurar; si venimos de otro proveedor, su
    # secreto no vale aquí. El blueprint lo copia a Authentik en cada arranque.
    if [[ -z "${OIDC_CLIENT_SECRET:-}" || "$PREV_AUTH_PROVIDER" != "authentik" ]]; then
        OIDC_CLIENT_SECRET=$(generate_secret 32)
    fi
    OIDC_SCOPE="openid profile email"
    OIDC_EMAIL_CLAIM="email"

    echo ""
    print_info "Authentik crea el usuario administrador 'akadmin' en su primer arranque."
    print_info "Usa un email real y tuyo: con login con Google, esa cuenta de Google"
    print_info "entraría como akadmin."
    AUTHENTIK_ADMIN_EMAIL=$(ask_input "Email del administrador de Authentik" \
                                      "${AUTHENTIK_ADMIN_EMAIL:-${ACME_EMAIL:-admin@${DOMAIN}}}" "validate_email")

    # Con SSL_MODE=front, Headscale llega a la URL pública a través del proxy
    # de delante. Por defecto resuelve el dominio por DNS; si ese DNS apunta a
    # una IP pública y el router no hace NAT loopback, no llegará.
    if [[ "$SSL_MODE" == "front" ]]; then
        echo ""
        print_info "Headscale y el panel tienen que alcanzar ${HEADSCALE_PUBLIC_URL}"
        print_info "desde sus contenedores, pasando por el proxy de delante."
        print_info "Si ${DOMAIN} resuelve a una IP pública y tu router no hace NAT"
        print_info "loopback, indica la IP del proxy en la LAN. Vacío = usar el DNS."
        FRONT_PROXY_IP=$(ask_input "IP del proxy de delante (opcional)" "${FRONT_PROXY_IP:-}" "validate_optional_ip")
    else
        FRONT_PROXY_IP=""
    fi

    # --- Login con Google ---
    echo ""
    if [[ "$URL_SCHEME" != "https" ]]; then
        print_warning "Login con Google no disponible: Google exige HTTPS en la Redirect URI"
        GOOGLE_CLIENT_ID=""
        GOOGLE_CLIENT_SECRET=""
    elif ask_yes_no "¿Añadir \"Login con Google\"?" "$([[ -n "${GOOGLE_CLIENT_ID:-}" ]] && echo y || echo n)"; then
        echo ""
        print_info "En https://console.cloud.google.com/apis/credentials crea un"
        print_info "\"OAuth client ID\" de tipo \"Web application\" con:"
        print_info "  Authorized JavaScript origins: ${HEADSCALE_PUBLIC_URL}"
        print_info "  Authorized redirect URIs:      ${HEADSCALE_PUBLIC_URL}/authentik/source/oauth/callback/google/"
        echo ""
        GOOGLE_CLIENT_ID=$(ask_input "Google Client ID" "${GOOGLE_CLIENT_ID:-}")
        GOOGLE_CLIENT_SECRET=$(ask_input "Google Client Secret" "${GOOGLE_CLIENT_SECRET:-}")
    else
        GOOGLE_CLIENT_ID=""
        GOOGLE_CLIENT_SECRET=""
    fi
}

generate_secrets() {
    print_header "GENERACIÓN DE SECRETOS"

    # Firma las cookies de sesión del panel. Se conserva al reconfigurar para
    # no cerrar las sesiones abiertas.
    if [[ -z "${PORTAL_SESSION_SECRET:-}" ]]; then
        PORTAL_SESSION_SECRET=$(generate_secret 32)
        print_success "Secreto de sesión del panel generado"
    fi

    [[ "$AUTH_PROVIDER" == "authentik" ]] || return 0

    # Nunca se regeneran si ya existen: la contraseña de Postgres queda fijada
    # al inicializar la base de datos y la SECRET_KEY firma sesiones y tokens.
    # La del admin sólo se lee en el primer arranque de Authentik.
    if [[ -z "${AUTHENTIK_SECRET_KEY:-}" ]]; then
        AUTHENTIK_SECRET_KEY=$(generate_secret 32)
        print_success "AUTHENTIK_SECRET_KEY generado"
    fi
    if [[ -z "${AUTHENTIK_PG_PASS:-}" ]]; then
        AUTHENTIK_PG_PASS=$(generate_secret 24)
        print_success "Contraseña de la base de datos de Authentik generada"
    fi
    if [[ -z "${AUTHENTIK_BOOTSTRAP_PASSWORD:-}" ]]; then
        AUTHENTIK_BOOTSTRAP_PASSWORD=$(generate_secret 12)
        print_success "Contraseña inicial de akadmin generada"
    fi

    # Cliente OIDC propio del panel Mi VPN. El blueprint lo copia a Authentik
    # en cada arranque. Si venimos de otro proveedor, su secreto no vale aquí.
    if [[ -z "${PORTAL_OIDC_CLIENT_SECRET:-}" || "${PORTAL_OIDC_CLIENT_ID:-}" != "mi-vpn" ]]; then
        PORTAL_OIDC_CLIENT_SECRET=$(generate_secret 32)
    fi
    PORTAL_OIDC_CLIENT_ID="mi-vpn"
    return 0
}

# Con qué se autentica el panel y con qué permisos corre, según AUTH_PROVIDER.
portal_settings() {
    case "$AUTH_PROVIDER" in
        authentik)
            PORTAL_OIDC_ISSUER="${HEADSCALE_PUBLIC_URL}/authentik/application/o/mi-vpn/"
            PORTAL_ADMIN_EMAILS=""
            ;;
        external)
            # Mismo cliente que Headscale (ver configure_auth_external)
            PORTAL_OIDC_ISSUER="$OIDC_ISSUER_URL"
            PORTAL_OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
            PORTAL_OIDC_CLIENT_SECRET="$OIDC_CLIENT_SECRET"
            ;;
        *)
            PORTAL_OIDC_ISSUER=""
            PORTAL_OIDC_CLIENT_ID=""
            PORTAL_OIDC_CLIENT_SECRET=""
            PORTAL_ADMIN_EMAILS=""
            ;;
    esac

    # El panel escribe headscale-config.yaml (DNS) y habla con el socket de
    # Docker (validar y reiniciar Headscale): corre con el uid/gid dueño de los
    # ficheros del proyecto y el grupo del socket, nunca como root.
    PORTAL_UID=$(stat -c %u "$SCRIPT_DIR")
    PORTAL_GID=$(stat -c %g "$SCRIPT_DIR")
    DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 999)
}

# -----------------------------------------------------------------------------
# FUNCIONES DE GENERACIÓN DE ARCHIVOS
# -----------------------------------------------------------------------------

generate_env_file() {
    print_header "GENERANDO ARCHIVO .env"

    # Cómo se autentica el panel y con qué uid/gid corre
    portal_settings
    # Compose lee COMPOSE_PROFILES de .env: así 'docker compose up -d' a secas
    # levanta también Authentik cuando está elegido, sin recordar --profile.
    COMPOSE_PROFILES=$([[ "$AUTH_PROVIDER" == "authentik" ]] && echo "authentik" || echo "")

    cat > "$ENV_FILE" <<EOF
# =============================================================================
# CONFIGURACIÓN HEADSCALE + MI VPN
# =============================================================================
# Generado por install.sh el $(date)
# Para reconfigurar: ejecuta ./install.sh

# -----------------------------------------------------------------------------
# RED Y ACCESO
# -----------------------------------------------------------------------------
# Un solo dominio. Caddy lo enruta: / -> Headscale, /mi-vpn -> panel.
DOMAIN=${DOMAIN}
URL_SCHEME=${URL_SCHEME}

# URL del control plane: la que usan los clientes con --login-server
SERVER_URL=${SERVER_URL}
HEADSCALE_PUBLIC_URL=${HEADSCALE_PUBLIC_URL}


# Quién pone el HTTPS. Caddy arranca siempre y enruta en los cuatro casos.
#   letsencrypt -> Caddy pide el certificado a Let's Encrypt
#   selfsigned  -> Caddy firma con su CA interna
#   front       -> lo pone un proxy por delante; aquí Caddy sirve HTTP
#   none        -> no hay HTTPS en ninguna capa
SSL_MODE=${SSL_MODE}
ACME_EMAIL=${ACME_EMAIL:-}

# Proxy de delante (sólo con SSL_MODE=front): npm, nginx, traefik o caddy.
# Determina qué snippet se genera en reverse-proxy/.
FRONT_PROXY=${FRONT_PROXY:-}

# Dirección de esta máquina vista desde ese proxy (destino del reverse proxy)
BACKEND_HOST=${BACKEND_HOST:-}

# -----------------------------------------------------------------------------
# PUERTOS
# -----------------------------------------------------------------------------
HTTP_PORT=${HTTP_PORT}
HTTPS_PORT=${HTTPS_PORT}
HEADSCALE_GRPC_PORT=${HEADSCALE_GRPC_PORT}
HEADSCALE_HTTP_PORT=${HEADSCALE_HTTP_PORT}
HEADSCALE_DERP_PORT=${HEADSCALE_DERP_PORT}
HEADSCALE_METRICS_PORT=${HEADSCALE_METRICS_PORT}

# -----------------------------------------------------------------------------
# TAILNET
# -----------------------------------------------------------------------------
TAILNET_NAME=${TAILNET_NAME}

# Usuario administrador creado automáticamente al desplegar
ADMIN_USER=${ADMIN_USER}

# Caducidad de la API key generada automáticamente (ej: 90d, 365d)
APIKEY_EXPIRATION=${APIKEY_EXPIRATION:-90d}

# API key de Headscale: la usa el panel Mi VPN para hablar con Headscale (y
# sirve para entrar en él como admin si PORTAL_API_KEY_LOGIN=true). La rellena
# el instalador tras arrancar Headscale. NO compartir ni versionar.
HEADSCALE_API_KEY=${HEADSCALE_API_KEY:-}

IP_PREFIXES_V4=${IP_PREFIXES_V4}
IP_PREFIXES_V6=${IP_PREFIXES_V6}
DATA_DIR=${DATA_DIR}
LOG_LEVEL=${LOG_LEVEL}

# -----------------------------------------------------------------------------
# PANEL MI VPN (/mi-vpn)
# -----------------------------------------------------------------------------
# Firma las cookies de sesión. NO compartir.
PORTAL_SESSION_SECRET=${PORTAL_SESSION_SECRET:-}

# OIDC del panel (los deriva install.sh de AUTH_PROVIDER; vacío = sin SSO)
PORTAL_OIDC_ISSUER="${PORTAL_OIDC_ISSUER:-}"
PORTAL_OIDC_CLIENT_ID="${PORTAL_OIDC_CLIENT_ID:-}"
PORTAL_OIDC_CLIENT_SECRET="${PORTAL_OIDC_CLIENT_SECRET:-}"

# ¿Acepta también la API key de Headscale para entrar como admin? (acceso de
# emergencia). Sin OIDC es la única forma de entrar y se fuerza a true.
PORTAL_API_KEY_LOGIN=${PORTAL_API_KEY_LOGIN:-false}

# Quién es administrador: miembros de estos grupos (Authentik) o dueños de
# estos emails (proveedor OIDC propio). Separados por comas.
PORTAL_ADMIN_GROUPS="${PORTAL_ADMIN_GROUPS:-vpn-admins,authentik Admins}"
PORTAL_ADMIN_EMAILS="${PORTAL_ADMIN_EMAILS:-}"

# Con qué uid/gid corre el panel (dueño de los ficheros del proyecto, para
# escribir headscale-config.yaml) y el grupo del socket de Docker.
PORTAL_UID=${PORTAL_UID}
PORTAL_GID=${PORTAL_GID}
DOCKER_GID=${DOCKER_GID}

# -----------------------------------------------------------------------------
# AUTENTICACIÓN
# -----------------------------------------------------------------------------
# none      -> sin OIDC: el panel sólo acepta la API key (admins)
# authentik -> Authentik integrado en /authentik/ (usuario/contraseña, Google)
# external  -> proveedor OIDC propio
AUTH_PROVIDER=${AUTH_PROVIDER}

# Profiles de Compose activos (lo deriva install.sh de AUTH_PROVIDER)
COMPOSE_PROFILES=${COMPOSE_PROFILES}

# OIDC (con authentik los rellena el instalador)
ENABLE_OIDC=${ENABLE_OIDC}
OIDC_ISSUER_URL="${OIDC_ISSUER_URL}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID}"
OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET}"
OIDC_SCOPE="${OIDC_SCOPE}"
OIDC_EMAIL_CLAIM="${OIDC_EMAIL_CLAIM}"

# Aislamiento de red por usuario: los dispositivos de cada usuario sólo
# alcanzan los suyos (ACL autogroup:self). Sólo se aplica si Headscale aún no
# tiene política; una política existente nunca se sobrescribe.
NETWORK_ISOLATION=${NETWORK_ISOLATION:-true}

# -----------------------------------------------------------------------------
# AUTHENTIK (sólo con AUTH_PROVIDER=authentik). NO compartir ni versionar.
# -----------------------------------------------------------------------------
# No cambies AUTHENTIK_PG_PASS ni AUTHENTIK_SECRET_KEY tras el primer arranque:
# la base de datos ya está inicializada con ellas.
AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY:-}
AUTHENTIK_PG_PASS=${AUTHENTIK_PG_PASS:-}

# Usuario akadmin. Sólo se leen en el PRIMER arranque de Authentik: cambiar
# la contraseña aquí después no tiene efecto (se cambia desde su UI).
AUTHENTIK_ADMIN_EMAIL=${AUTHENTIK_ADMIN_EMAIL:-}
AUTHENTIK_BOOTSTRAP_PASSWORD=${AUTHENTIK_BOOTSTRAP_PASSWORD:-}

# Login con Google (vacío = deshabilitado). Redirect URI a registrar en Google:
#   ${HEADSCALE_PUBLIC_URL}/authentik/source/oauth/callback/google/
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-}"

# IP del proxy de delante en la LAN (SSL_MODE=front). Si tiene valor, los
# contenedores resuelven DOMAIN a ella para llegar a Authentik.
FRONT_PROXY_IP=${FRONT_PROXY_IP:-}

# -----------------------------------------------------------------------------
# AVANZADO
# -----------------------------------------------------------------------------
TZ=${TZ:-UTC}
NETWORK_NAME=headscale-net
HEADSCALE_IMAGE_TAG=latest
CADDY_IMAGE_TAG=2-alpine
AUTHENTIK_IMAGE_TAG=${AUTHENTIK_IMAGE_TAG:-2026.8.3}
EOF

    print_success "Archivo .env generado: $ENV_FILE"
}

generate_headscale_config() {
    print_header "GENERANDO CONFIGURACIÓN DE HEADSCALE"

    # Configurar OIDC si está habilitado
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        # OIDC_SCOPE es una lista separada por espacios ("openid profile email");
        # Headscale la espera como lista YAML, un elemento por scope.
        local scope_list=""
        local s
        for s in $OIDC_SCOPE; do
            scope_list+="    - ${s}"$'\n'
        done

        # Authentik no marca los emails como verificados (sus usuarios los da
        # de alta un admin, sin paso de verificación) y Headscale, por
        # defecto, no sincroniza los que no lo están.
        local email_verified="true"
        [[ "$AUTH_PROVIDER" == "authentik" ]] && email_verified="false"

        # Heredoc SIN comillas: las variables deben expandirse aquí. envsubst
        # hace una sola pasada y no volvería a sustituir el texto insertado.
        #
        # No se filtra con allowed_groups/allowed_domains: con Authentik ya lo
        # hacen las bindings de la aplicación (grupo headscale-users), y con un
        # proveedor externo se añaden a mano si hacen falta.
        OIDC_CONFIG=$(cat <<EOFC
oidc:
  # Headscale no arranca si no puede descargar la configuración del issuer
  only_start_if_oidc_is_available: true
  issuer: "${OIDC_ISSUER_URL}"
  client_id: "${OIDC_CLIENT_ID}"
  client_secret: "${OIDC_CLIENT_SECRET}"
  scope:
${scope_list%$'\n'}
  email_verified_required: ${email_verified}
  pkce:
    enabled: true
    method: S256
EOFC
        )
    else
        OIDC_CONFIG="# OIDC deshabilitado"
    fi

    # Headscale siempre está detrás de Caddy, así que sin trusted_proxies vería
    # la IP de Caddy en todas las peticiones y todos los nodos aparecerían con
    # la misma en los logs. Se confía en el rango privado de las redes bridge de
    # Docker (172.17-172.31, que cae dentro de 172.16.0.0/12): el puerto de
    # Headscale no se publica en el host, así que nadie más puede llegar ahí.
    # Headscale rechaza el prefijo /0, por eso no vale poner 0.0.0.0/0.
    TRUSTED_PROXIES_CONFIG=$(cat <<'EOFP'
trusted_proxies:
  - 172.16.0.0/12
EOFP
    )

    # Con un proxy por delante la cadena X-Forwarded-For llega con dos saltos
    # (cliente, proxy) y Headscale sólo salta los que tiene en la lista, así que
    # se añade también el proxy para que la IP real sea la del cliente.
    if [[ "$SSL_MODE" == "front" && -n "${BACKEND_HOST:-}" ]]; then
        TRUSTED_PROXIES_CONFIG+=$'\n'"  # Descomenta y pon el CIDR de tu proxy para ver la IP real del cliente:"
        TRUSTED_PROXIES_CONFIG+=$'\n'"  # - 192.168.1.50/32"
    fi

    # Cargar plantilla y sustituir variables
    DNS_CONFIG=$(dns_block)

    export SERVER_URL HEADSCALE_HTTP_PORT HEADSCALE_METRICS_PORT HEADSCALE_GRPC_PORT \
           IP_PREFIXES_V4 IP_PREFIXES_V6 TAILNET_NAME HEADSCALE_DERP_PORT LOG_LEVEL \
           OIDC_CONFIG OIDC_ISSUER_URL OIDC_CLIENT_ID OIDC_CLIENT_SECRET OIDC_SCOPE \
           TRUSTED_PROXIES_CONFIG DNS_CONFIG

    envsubst < "$TEMPLATES_DIR/headscale-config.yaml.tmpl" > "$SCRIPT_DIR/headscale-config.yaml"

    print_success "Configuración de Headscale generada: headscale-config.yaml"
}

# Bloque dns: de headscale-config.yaml, entre marcadores. Lo edita el panel
# Mi VPN (Administración → DNS); al regenerar la configuración se conserva el
# bloque que ya hubiera, para no perder esos cambios. Si no existe (primera
# instalación, o una anterior a este cambio), se escribe el de por defecto.
dns_block() {
    local begin="# >>> dns: gestionado por Mi VPN (no edites entre estos marcadores a mano)"
    local end="# <<< dns"
    local current="$SCRIPT_DIR/headscale-config.yaml"

    if [[ -f "$current" ]] && grep -qF "$begin" "$current"; then
        sed -n "\|^${begin}\$|,\|^${end}\$|p" "$current"
        return 0
    fi

    # Por defecto. Si había una sección dns: sin marcadores (instalación
    # anterior), se respetan su base_domain y MagicDNS.
    local base="${TAILNET_NAME}.headscale.net" magic="true"
    if [[ -f "$current" ]]; then
        local b m
        b=$(sed -n '/^dns:/,/^[a-z]/{s/^  base_domain:[[:space:]]*//p}' "$current" | head -1)
        m=$(sed -n '/^dns:/,/^[a-z]/{s/^  magic_dns:[[:space:]]*//p}' "$current" | head -1)
        [[ -n "$b" ]] && base="$b"
        [[ -n "$m" ]] && magic="$m"
    fi
    cat <<EOFD
${begin}
dns:
  magic_dns: ${magic}
  base_domain: ${base}
  override_local_dns: true
  nameservers:
    global:
      - 1.1.1.1
      - 1.0.0.1
    split: {}
  search_domains: []
  extra_records: []
${end}
EOFD
}

generate_caddyfile() {
    print_header "GENERANDO CADDYFILE"

    case "$SSL_MODE" in
        letsencrypt)
            CADDY_DOMAIN="$DOMAIN"
            CADDY_TLS="tls ${ACME_EMAIL}"
            CADDY_HSTS="Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\""
            ;;
        selfsigned)
            CADDY_DOMAIN="$DOMAIN"
            CADDY_TLS="tls internal"
            CADDY_HSTS="# HSTS deshabilitado (certificado autofirmado)"
            ;;
        *)
            # 'front' y 'none': Caddy sólo enruta, por HTTP.
            #
            # ":80" en vez de "http://${DOMAIN}" a propósito: sin certificado el
            # sitio se alcanza por varios nombres (localhost, la IP de la LAN, el
            # hostname con el que lo llame el proxy de delante) y un site address
            # con dominio devolvería 404 a todos los demás.
            #
            # Caddy no intenta emitir certificados para una dirección sin esquema
            # ni host, así que no hace falta "auto_https off".
            CADDY_DOMAIN=":80"
            CADDY_TLS="# Sin TLS aquí: Caddy sólo hace de reverse proxy por HTTP"
            if [[ "$SSL_MODE" == "front" ]]; then
                CADDY_HSTS="# HSTS: lo emite el proxy de delante, que es quien habla HTTPS"
            else
                CADDY_HSTS="# HSTS deshabilitado (sin TLS)"
            fi
            ;;
    esac

    # Redirección HTTP -> HTTPS sólo cuando Caddy es quien tiene el certificado.
    # Sin él el sitio YA es el de HTTP y el bloque sería un bucle; con un proxy
    # delante, redirigir aquí mandaría al cliente de vuelta al proxy.
    if [[ "$SSL_MODE" == "letsencrypt" || "$SSL_MODE" == "selfsigned" ]]; then
        HTTP_REDIRECT=$(cat <<EOF
http://${DOMAIN} {
    redir https://{host}{uri} permanent
}
EOF
        )
    else
        HTTP_REDIRECT=""
    fi

    # Authentik bajo /authentik/. No se recorta el prefijo: Authentik lo espera
    # (AUTHENTIK_WEB__PATH) y lo incluye en todas las URLs que genera.
    if [[ "$AUTH_PROVIDER" == "authentik" ]]; then
        # Authentik construye el issuer OIDC y sus redirecciones a partir del
        # esquema de la petición. Con SSL_MODE=front a Caddy le llega HTTP y lo
        # reenviaría como tal, así que se fuerza el esquema que ve el navegador.
        local proto_line="# X-Forwarded-Proto: el de la petición entrante"
        [[ "$SSL_MODE" == "front" ]] && proto_line="header_up X-Forwarded-Proto https"

        AUTHENTIK_ROUTE=$(cat <<EOF
    # Authentik (proveedor de identidad). Va antes que el catch-all de
    # Headscale; /authentik a secas redirige para no caer en él.
    redir /authentik /authentik/ 308
    # Atajo al formulario de alta de usuarios (blueprint: headscale-alta-usuario)
    redir /alta-usuario /authentik/if/flow/headscale-alta-usuario/ 302

    handle /authentik/* {
        reverse_proxy authentik-server:9000 {
            header_up X-Real-IP {remote_host}
            ${proto_line}
        }
    }
EOF
        )
    else
        AUTHENTIK_ROUTE="    # Authentik deshabilitado (AUTH_PROVIDER=${AUTH_PROVIDER})"
    fi

    export CADDY_DOMAIN CADDY_TLS CADDY_HSTS HEADSCALE_HTTP_PORT HTTP_REDIRECT AUTHENTIK_ROUTE

    envsubst < "$TEMPLATES_DIR/Caddyfile.tmpl" > "$SCRIPT_DIR/Caddyfile"

    print_success "Caddyfile generado: Caddyfile"
}

generate_compose_override() {
    print_header "GENERANDO DOCKER COMPOSE OVERRIDE"

    local ports_block
    if [[ "$SSL_MODE" == "letsencrypt" || "$SSL_MODE" == "selfsigned" ]]; then
        print_info "Publicando los puertos HTTP y HTTPS de Caddy..."
        ports_block=$(cat <<'EOFP'
      # HTTP: reto ACME de Let's Encrypt y redirección a HTTPS
      - "${HTTP_PORT:-80}:80"
      - "${HTTPS_PORT:-443}:443"
      # HTTP/3 (QUIC)
      - "${HTTPS_PORT:-443}:443/udp"
EOFP
        )
    else
        print_info "Publicando sólo el puerto HTTP de Caddy (aquí no hay TLS)..."
        ports_block=$(cat <<'EOFP'
      # Sin TLS en esta máquina: sólo HTTP. No se publica 443 porque nada
      # escucharía ahí.
      - "${HTTP_PORT:-80}:80"
EOFP
        )
    fi

    local oidc_block=""
    [[ "$AUTH_PROVIDER" == "authentik" ]] && oidc_block=$(authentik_reachability_block)

    # El heredoc no va entrecomillado, pero ports_block ya viene expandido y
    # bash no vuelve a escanear el resultado de una expansión: los ${HTTP_PORT}
    # de dentro llegan literales al fichero, que es lo que queremos (los
    # resuelve Compose leyendo .env).
    cat > "$SCRIPT_DIR/docker-compose.override.yml" <<EOF
# Docker Compose Override - Generado automáticamente por install.sh
# Publica los puertos de Caddy, que dependen de quién ponga el TLS.
#
# Compose FUSIONA las listas de 'ports' añadiendo, nunca quitando, así que los
# puertos variables no pueden estar en docker-compose.yml: si estuvieran, este
# fichero no podría retirarlos.
#
# NO editar a mano: install.sh lo regenera en cada ejecución.

services:
  caddy:
    ports:
${ports_block}
${oidc_block}
EOF

    print_success "docker-compose.override.yml generado"
}

# Headscale y el panel validan el issuer OIDC contra la URL PÚBLICA de
# Authentik (${HEADSCALE_PUBLIC_URL}/authentik/...): el issuer que publica
# Authentik se construye con el host y esquema de la petición, y los clientes
# OIDC exigen que coincida exactamente. Por eso no vale la URL interna
# http://authentik-server:9000; hay que llegar a la pública desde dentro de
# los contenedores:
#
#   - DOMAIN se resuelve a la puerta de enlace del host (host-gateway), donde
#     Caddy publica sus puertos: mismo puerto y mismo certificado que ve un
#     navegador, sin depender de que el router haga NAT loopback.
#   - Con SSL_MODE=front el TLS lo pone otra máquina: se resuelve a
#     FRONT_PROXY_IP si se indicó, o por DNS si no.
#   - Con SSL_MODE=selfsigned hay que confiar además en la CA de Caddy.
authentik_reachability_block() {
    local target=""
    if [[ "$DOMAIN" =~ ^[0-9.]+$ ]]; then
        target=""                              # una IP no necesita resolución
    elif [[ "$SSL_MODE" == "front" ]]; then
        target="${FRONT_PROXY_IP:-}"
    else
        target="host-gateway"
    fi

    local svc hosts="" ca_env="" ca_vol=""
    [[ -n "$target" ]] && hosts=$'\n'"    extra_hosts:"$'\n'"      - \"${DOMAIN}:${target}\""

    local out=""
    for svc in headscale portal; do
        if [[ "$SSL_MODE" == "selfsigned" ]]; then
            # Cada runtime añade la CA de Caddy a su manera: Go (Headscale)
            # con SSL_CERT_DIR y el panel (Python) con EXTRA_CA_FILE, que
            # carga él mismo.
            case "$svc" in
                headscale) ca_env="      - SSL_CERT_DIR=/etc/ssl/certs:/caddy-ca" ;;
                portal)    ca_env="      - EXTRA_CA_FILE=/caddy-ca/root.crt" ;;
            esac
            ca_vol=$'\n'"    volumes:"$'\n'"      - ./data/caddy-ca:/caddy-ca:ro"$'\n'"    environment:"$'\n'"${ca_env}"
        fi
        [[ -z "$hosts" && -z "$ca_vol" ]] && continue
        out+=$'\n'"  ${svc}:${hosts}${ca_vol}"
    done

    [[ -z "$out" ]] && return 0
    printf '\n  # Acceso a la URL pública de Authentik desde los contenedores (OIDC)%s\n' "$out"
}

# Con Caddy delante de todo, el proxy externo tiene un ÚNICO destino y no
# necesita saber nada de /admin ni de CORS: le basta con reenviar el dominio
# entero a Caddy, que ya enruta por ruta. Por eso el snippet es tan corto.
generate_front_proxy_snippet() {
    [[ "$SSL_MODE" == "front" ]] || return 0

    print_header "GENERANDO CONFIGURACIÓN DEL PROXY DE DELANTE"

    local tmpl out
    case "$FRONT_PROXY" in
        npm)     tmpl="front-npm.md.tmpl";      out="NGINX-PROXY-MANAGER.md" ;;
        nginx)   tmpl="front-nginx.conf.tmpl";  out="nginx-${DOMAIN}.conf"   ;;
        traefik) tmpl="front-traefik.yml.tmpl"; out="traefik-${DOMAIN}.yml"  ;;
        caddy)   tmpl="front-caddy.tmpl";       out="Caddyfile"              ;;
        *)
            print_warning "Proxy '${FRONT_PROXY}' desconocido, no se genera snippet"
            return 0
            ;;
    esac

    mkdir -p "$SCRIPT_DIR/reverse-proxy"

    export DOMAIN BACKEND_HOST HTTP_PORT HEADSCALE_DERP_PORT HEADSCALE_PUBLIC_URL

    # Lista explícita de variables: las plantillas de nginx y Traefik están
    # llenas de $host, $http_upgrade, $remote_addr... y un envsubst sin lista se
    # los comería todos dejando la configuración rota.
    envsubst '${DOMAIN} ${BACKEND_HOST} ${HTTP_PORT} ${HEADSCALE_DERP_PORT} ${HEADSCALE_PUBLIC_URL}' \
        < "$TEMPLATES_DIR/$tmpl" > "$SCRIPT_DIR/reverse-proxy/$out"

    print_success "Generado: reverse-proxy/${out}"
    print_info "Cópialo a la máquina del proxy y aplícalo allí"
}

create_data_dirs() {
    print_header "CREANDO DIRECTORIOS DE DATOS"

    mkdir -p "$DATA_DIR"
    mkdir -p "$DATA_DIR/caddy-logs"

    print_success "Directorios de datos creados en: $DATA_DIR"
}

# -----------------------------------------------------------------------------
# FUNCIONES DE DESPLIEGUE
# -----------------------------------------------------------------------------

# Con Authentik, Headscale no arranca (only_start_if_oidc_is_available) hasta
# que el issuer responde en la URL pública. Eso exige, por este orden:
# Authentik con el blueprint aplicado, y Caddy enrutando /authentik/. Caddy se
# levanta con --no-deps para no arrastrar al panel, que todavía no
# tiene la API key.
start_authentik_first() {
    [[ "$AUTH_PROVIDER" == "authentik" ]] || return 0

    print_header "ARRANCANDO AUTHENTIK"

    print_info "Descargando imágenes de Docker..."
    docker compose pull

    print_info "Levantando Authentik y Caddy..."
    docker compose up -d authentik-postgresql authentik-server authentik-worker
    # --force-recreate: al reconfigurar, Caddy ya corre con el Caddyfile
    # anterior (sin /authentik/) y 'up' no relee un fichero montado.
    docker compose up -d --no-deps --force-recreate caddy

    # Headscale necesita la CA de Caddy para validar el issuer por HTTPS
    if [[ "$SSL_MODE" == "selfsigned" ]]; then
        export_root_ca
        mkdir -p "$SCRIPT_DIR/data/caddy-ca"
        if [[ -s "$SCRIPT_DIR/caddy-root-ca.crt" ]]; then
            cp "$SCRIPT_DIR/caddy-root-ca.crt" "$SCRIPT_DIR/data/caddy-ca/root.crt"
        fi
    fi

    # Con un proxy delante, Headscale llega al issuer A TRAVÉS de ese proxy:
    # si todavía no reenvía el dominio hacia aquí, Headscale no arrancará.
    if [[ "$SSL_MODE" == "front" ]]; then
        echo ""
        print_warning "Headscale validará el login contra ${HEADSCALE_PUBLIC_URL}/authentik/"
        print_warning "pasando por el proxy de delante, así que ese proxy tiene que estar listo."
        echo -e "   Configúralo ahora con: ${BOLD}$(ls -1 "$SCRIPT_DIR"/reverse-proxy/ 2>/dev/null | sed 's|^|reverse-proxy/|' | tr '\n' ' ')${NC}"
        echo -e "   (reenvía ${DOMAIN} a ${BACKEND_HOST}:${HTTP_PORT}; Caddy ya está escuchando)"
        read -r -p "$(echo -e "${CYAN}?${NC} Pulsa Enter cuando el proxy reenvíe ${DOMAIN} a esta máquina: ")" _
    fi

    # El primer arranque migra la base de datos y aplica los blueprints: puede
    # tardar un par de minutos. Se consulta desde Caddy (que trae wget) por la
    # red interna: aquí sólo se comprueba que Authentik ya sirve el proveedor.
    print_info "Esperando a que Authentik publique el proveedor OIDC (1-3 min la primera vez)..."
    local url="http://authentik-server:9000/authentik/application/o/headscale/.well-known/openid-configuration"
    local waited=0
    while [ $waited -lt 300 ]; do
        if docker exec caddy wget -q -O /dev/null "$url" 2>/dev/null; then
            echo ""
            # Authentik sólo reaplica un blueprint cuando cambia el FICHERO, no
            # sus variables de entorno. Al reconfigurar (otro dominio, otro
            # secreto, Google) el proveedor se quedaría con los valores viejos
            # y el login fallaría con redirect_uri_no_match. Se fuerza aquí.
            # Tarda ~1-2 min: reaplica también los blueprints por defecto de
            # los que depende (entradas metaapplyblueprint).
            print_info "Aplicando el blueprint con la configuración actual (1-2 min)..."
            if ! docker exec authentik-worker ak apply_blueprint custom/headscale.yaml >/dev/null 2>&1; then
                print_error "No se pudo aplicar el blueprint de Authentik"
                print_info "Ejecuta a mano: docker exec authentik-worker ak apply_blueprint custom/headscale.yaml"
                exit 1
            fi
            print_success "Authentik está listo"
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
        echo -n "."
    done

    echo ""
    print_error "Authentik no publicó el proveedor OIDC tras 300s"
    print_info "Revisa los logs con: docker compose logs authentik-server authentik-worker"
    print_info "Si el blueprint falló, aparece en Authentik > Customization > Blueprints"
    exit 1
}

# Si se deja de usar Authentik, sus contenedores quedarían corriendo: con el
# profile inactivo 'docker compose up' ya no los gestiona. Los datos siguen en
# los volúmenes authentik-db y authentik-data por si se vuelve a activar.
# Headplane se retiró: la administración está en el panel Mi VPN. Si sigue el
# contenedor de una instalación anterior, se quita. Su volumen (headplane-data)
# y su config se conservan: no se borra nada que no se pueda recuperar.
remove_headplane() {
    if docker ps -a --format '{{.Names}}' | grep -qx headplane; then
        print_info "Retirando Headplane (sustituido por Mi VPN; su volumen se conserva)..."
        docker rm -f headplane >/dev/null
    fi
}

stop_unused_authentik() {
    [[ "$AUTH_PROVIDER" == "authentik" ]] && return 0
    if docker ps -a --format '{{.Names}}' | grep -qE '^authentik-(server|worker|postgresql)$'; then
        print_info "Deteniendo Authentik (ya no se usa; sus datos se conservan)..."
        docker compose --profile authentik rm -sf authentik-server authentik-worker authentik-postgresql >/dev/null
    fi
}

start_headscale_first() {
    # Headscale debe estar arriba ANTES que el panel: la API key que el panel
    # necesita sólo puede emitirla un Headscale en marcha.
    print_header "ARRANCANDO HEADSCALE"

    # Con Authentik las imágenes ya se descargaron en start_authentik_first
    if [[ "$AUTH_PROVIDER" != "authentik" ]]; then
        print_info "Descargando imágenes de Docker..."
        docker compose pull
    fi

    print_info "Levantando Headscale..."
    # --force-recreate: al reconfigurar, headscale-config.yaml cambia pero el
    # contenedor no, y 'up' a secas no lo reiniciaría para leerlo.
    docker compose up -d --force-recreate headscale

    # Con OIDC, Headscale se reinicia hasta alcanzar el issuer: más margen
    local max_wait=90
    [[ "$ENABLE_OIDC" == "true" ]] && max_wait=180

    print_info "Esperando a que Headscale esté saludable..."
    local waited=0
    while [ $waited -lt $max_wait ]; do
        local state
        state=$(docker inspect -f '{{.State.Health.Status}}' headscale 2>/dev/null || echo "starting")
        if [[ "$state" == "healthy" ]]; then
            echo ""
            print_success "Headscale está listo"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
        echo -n "."
    done

    echo ""
    print_error "Headscale no llegó a estado saludable tras ${max_wait}s"
    print_info "Revisa los logs con: docker compose logs headscale"
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        print_info "Con OIDC, la causa habitual es que no alcanza el issuer desde su contenedor:"
        print_info "  ${OIDC_ISSUER_URL}.well-known/openid-configuration"
        print_info "Comprueba el DNS de ${DOMAIN}, el firewall hacia los puertos de Caddy"
        print_info "y, con certificado autofirmado, que exista data/caddy-ca/root.crt"
    fi
    exit 1
}

bootstrap_headscale() {
    print_header "CREANDO USUARIO ADMINISTRADOR Y API KEY"

    # --- Usuario administrador (idempotente) ---
    # 'users create' falla con UNIQUE constraint si ya existe, así que se
    # comprueba antes para que reejecutar el instalador no aborte.
    if docker exec headscale headscale users list --output json 2>/dev/null \
        | grep -q "\"name\": *\"${ADMIN_USER}\""; then
        print_info "El usuario '${ADMIN_USER}' ya existe, se conserva"
    else
        if docker exec headscale headscale users create "${ADMIN_USER}" >/dev/null 2>&1; then
            print_success "Usuario administrador creado: ${ADMIN_USER}"
        else
            print_error "No se pudo crear el usuario '${ADMIN_USER}'"
            docker exec headscale headscale users create "${ADMIN_USER}" || true
            exit 1
        fi
    fi

    # Headscale 0.29 exige el ID numérico en 'preauthkeys create --user',
    # no el nombre, así que hay que resolverlo para poder mostrar el comando.
    ADMIN_USER_ID=$(docker exec headscale headscale users list --output json 2>/dev/null \
                    | tr -d ' \t\n' \
                    | grep -oE "\"id\":[0-9]+,\"name\":\"${ADMIN_USER}\"" \
                    | grep -oE '[0-9]+' | head -1)
    [[ -z "$ADMIN_USER_ID" ]] && ADMIN_USER_ID="<id>"

    # --- API key ---
    # Headscale sólo devuelve el valor completo al crearla; después almacena
    # únicamente el prefijo. Si conservamos una en .env y sigue vigente, se
    # reutiliza para que reconfigurar no acumule claves huérfanas.
    if [[ -n "${HEADSCALE_API_KEY:-}" ]]; then
        local prefix expires now
        prefix=$(printf '%s' "$HEADSCALE_API_KEY" | cut -d- -f1-3)

        # Headscale lista el prefijo enmascarado ("hskey-api-XXXX-***"), por lo
        # que hay que buscar el prefijo como subcadena, no como valor exacto.
        expires=$(docker exec headscale headscale apikeys list --output json 2>/dev/null \
                  | tr -d ' \t\n' \
                  | grep -oE "\"prefix\":\"${prefix}[^\"]*\",\"expiration\":\{\"seconds\":[0-9]+" \
                  | grep -oE '[0-9]+$' || true)
        now=$(date +%s)

        if [[ -n "$expires" ]] && [[ "$expires" -gt "$now" ]]; then
            print_info "Reutilizando la API key existente de .env"
            return 0
        elif [[ -n "$expires" ]]; then
            print_warning "La API key guardada en .env ha caducado, se generará otra"
        else
            print_warning "La API key guardada en .env ya no existe, se generará otra"
        fi
    fi

    HEADSCALE_API_KEY=$(docker exec headscale headscale apikeys create \
                        --expiration "${APIKEY_EXPIRATION:-90d}" 2>/dev/null | tr -d '\r\n')

    if [[ ! "$HEADSCALE_API_KEY" =~ ^hskey- ]]; then
        print_error "La API key generada no tiene el formato esperado"
        print_info "Genérala manualmente con: docker exec headscale headscale apikeys create"
        HEADSCALE_API_KEY=""
        return 0
    fi

    # Persistir en .env (único fichero con secretos, ya excluido por .gitignore)
    if grep -q '^HEADSCALE_API_KEY=' "$ENV_FILE"; then
        sed -i "s|^HEADSCALE_API_KEY=.*|HEADSCALE_API_KEY=${HEADSCALE_API_KEY}|" "$ENV_FILE"
    else
        printf '\n# API key de Headscale (generada automáticamente, NO compartir)\nHEADSCALE_API_KEY=%s\n' \
            "$HEADSCALE_API_KEY" >> "$ENV_FILE"
    fi

    print_success "API key generada (válida ${APIKEY_EXPIRATION:-90d}) y guardada en .env"
}

# Política ACL de aislamiento: autogroup:member -> autogroup:self. Con ella,
# el dispositivo de un usuario sólo alcanza los de ese mismo usuario.
#
# Sólo se aplica si Headscale no tiene política todavía (mode: database, que
# es lo que edita el panel): una política existente, hecha a mano o desde
# Mi VPN > Control de acceso, nunca se pisa.
apply_network_policy() {
    [[ "${NETWORK_ISOLATION:-true}" == "true" ]] || return 0

    print_header "AISLAMIENTO DE RED POR USUARIO"

    if docker exec headscale headscale policy get >/dev/null 2>&1; then
        print_info "Headscale ya tiene una política ACL: se conserva tal cual"
        print_info "Para aislar por usuario, añade en Mi VPN > Control de acceso:"
        print_info '  {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}'
        return 0
    fi

    local policy
    policy=$(cat <<'EOFP'
{
  // Generada por install.sh (NETWORK_ISOLATION=true).
  // Cada usuario sólo alcanza sus propios dispositivos, admins incluidos.
  // Edítala desde Mi VPN > Control de acceso.
  "acls": [
    {
      "action": "accept",
      "src": ["autogroup:member"],
      "dst": ["autogroup:self:*"]
    }
  ]
}
EOFP
    )

    # La imagen de Headscale es distroless (sin sh): la política entra por
    # stdin, que 'policy set -f' acepta como /dev/stdin.
    if printf '%s\n' "$policy" | docker exec -i headscale headscale policy set -f /dev/stdin >/dev/null 2>&1; then
        print_success "Política aplicada: cada usuario sólo alcanza sus dispositivos"
    else
        print_warning "No se pudo aplicar la política de aislamiento"
        print_info "Aplícala desde Mi VPN > Control de acceso con:"
        printf '%s\n' "$policy"
    fi
}

deploy_stack() {
    print_header "DESPLEGANDO STACK CON DOCKER COMPOSE"

    # Caddy forma parte del stack siempre: es quien enruta / y /mi-vpn, con
    # certificado o sin él.
    stop_unused_authentik
    remove_headplane

    print_info "Levantando servicios..."
    docker compose up -d
    # El Caddyfile y el código del panel pueden haber cambiado: si ya
    # existían, 'up' no los reinicia para releerlos.
    docker compose restart caddy portal >/dev/null

    # Esperar a que los servicios estén saludables
    print_info "Esperando a que los servicios estén listos..."
    local max_wait=60
    local waited=0

    while [ $waited -lt $max_wait ]; do
        if docker compose ps | grep -q "healthy"; then
            break
        fi
        sleep 2
        waited=$((waited + 2))
        echo -n "."
    done
    echo ""

    if [ $waited -ge $max_wait ]; then
        print_warning "Los servicios están tardando más de lo esperado en iniciar"
        print_info "Verifica el estado con: docker compose ps"
        print_info "Verifica los logs con: docker compose logs -f"
    else
        print_success "Servicios desplegados correctamente"
    fi

    export_root_ca
}

# Con SSL_MODE=selfsigned, Caddy firma con su propia CA interna. Los clientes
# Tailscale rechazan ese certificado ("x509: certificate signed by unknown
# authority") y ni siquiera llegan a /key, así que la VPN no funciona hasta que
# la CA se instala en cada dispositivo. Se exporta aquí para poder distribuirla.
export_root_ca() {
    [[ "${SSL_MODE:-none}" == "selfsigned" ]] || return 0

    local ca_src="/data/caddy/pki/authorities/local/root.crt"
    local ca_dst="${SCRIPT_DIR}/caddy-root-ca.crt"
    local waited=0

    while [ $waited -lt 30 ]; do
        if docker exec caddy test -f "$ca_src" 2>/dev/null; then
            if docker exec caddy cat "$ca_src" > "$ca_dst" 2>/dev/null \
               && [[ -s "$ca_dst" ]]; then
                chmod 644 "$ca_dst"
                print_success "CA raíz exportada a: ${ca_dst}"
                return 0
            fi
        fi
        sleep 2
        waited=$((waited + 2))
    done

    rm -f "$ca_dst"
    print_warning "No se pudo exportar la CA raíz de Caddy"
    print_info "Extráela manualmente con:"
    print_info "  docker exec caddy cat ${ca_src} > caddy-root-ca.crt"
    return 0
}

show_authentik_info() {
    local ak="${HEADSCALE_PUBLIC_URL}/authentik"

    echo -e "${YELLOW}${BOLD}┌─────────────────────────────────────────────────────────────────────────┐${NC}"
        echo -e "${YELLOW}${BOLD}│  AUTHENTIK: CUENTAS DE USUARIO                                          │${NC}"
    echo -e "${YELLOW}${BOLD}└─────────────────────────────────────────────────────────────────────────┘${NC}"
    echo ""
    echo -e "  Panel de Authentik:  ${BOLD}${ak}/if/admin/${NC}"
    echo -e "  Usuario:         ${BOLD}akadmin${NC}"
    echo -e "  Contraseña:      ${BOLD}${AUTHENTIK_BOOTSTRAP_PASSWORD}${NC}"
    print_warning "Es la contraseña del PRIMER arranque. Cámbiala al entrar (y si ya la cambiaste, ésta ya no vale)."
    echo ""
    echo -e "  Para dar de alta a alguien (sin entrar en el panel de Authentik):"
    echo -e "   ${BOLD}${HEADSCALE_PUBLIC_URL}/alta-usuario${NC}"
    echo -e "   Formulario con nombre, usuario, email, contraseña y acceso:"
    echo -e "      • ${BOLD}VPN${NC}                        -> grupo headscale-users"
    echo -e "      • ${BOLD}VPN + administrador${NC}        -> grupo vpn-admins"
    echo -e "   Sólo pueden usarlo los miembros de vpn-admins y akadmin."
    echo -e "   Sin grupo, Authentik deniega el acceso aunque la cuenta exista."
    echo ""
    if [[ -n "${GOOGLE_CLIENT_ID:-}" ]]; then
        echo -e "  Login con Google activo. Quien entre con Google por primera vez obtiene"
        echo -e "  cuenta en Authentik pero ${BOLD}sin grupo${NC}: añádelo a uno para que entre."
        echo -e "  Redirect URI registrada en Google:"
        echo -e "   ${BOLD}${ak}/source/oauth/callback/google/${NC}"
        echo ""
    fi
    echo -e "  Administran el panel ${BOLD}akadmin${NC} y los miembros de ${BOLD}vpn-admins${NC}."
    echo -e "  Los dispositivos pueden registrarse con ${YELLOW}tailscale up --login-server=${HEADSCALE_PUBLIC_URL}${NC}"
    echo -e "  (sin --authkey): abrirá el login de Authentik en el navegador."
    if [[ "${NETWORK_ISOLATION:-true}" == "true" ]]; then
        echo -e "  Red aislada por usuario: cada uno sólo alcanza sus propios dispositivos."
    fi
    echo ""
}

show_access_info() {
    print_header "¡INSTALACIÓN COMPLETADA!"

    echo ""
    echo -e "${GREEN}${BOLD}✓ Headscale + Mi VPN están corriendo${NC}"
    echo -e "${CYAN}HTTPS:${NC} ${BOLD}${SSL_MODE}${NC}"
    echo ""

    # URLs de acceso
    echo -e "${CYAN}Panel (Mi VPN):${NC} ${BOLD}${HEADSCALE_PUBLIC_URL}/mi-vpn/${NC}"
    echo -e "${CYAN}Control plane (Headscale):${NC} ${BOLD}${HEADSCALE_PUBLIC_URL}${NC}"
    echo ""

    # Con un proxy delante el stack NO es alcanzable todavía: falta configurar
    # la otra máquina. Decirlo antes que nada evita el desconcierto.
    if [[ "$SSL_MODE" == "front" ]]; then
        echo -e "${YELLOW}${BOLD}┌─────────────────────────────────────────────────────────────────────────┐${NC}"
        echo -e "${YELLOW}${BOLD}│  FALTA UN PASO: CONFIGURAR EL PROXY DE DELANTE                          │${NC}"
        echo -e "${YELLOW}${BOLD}└─────────────────────────────────────────────────────────────────────────┘${NC}"
        echo ""
        echo -e "   La URL de arriba todavía no responde. Aquí, Caddy escucha en:"
        echo -e "     ${BOLD}${BACKEND_HOST}:${HTTP_PORT}${NC}  (HTTP, ya enruta / y /admin)"
        echo ""
        echo -e "   Snippet listo para copiar en la máquina del proxy:"
        echo -e "     ${BOLD}$(ls -1 "$SCRIPT_DIR"/reverse-proxy/ 2>/dev/null | sed 's|^|reverse-proxy/|' | tr '\n' ' ')${NC}"
        echo ""
        print_warning "Apunta ${DOMAIN} al proxy, no a esta máquina."
        print_warning "Abre UDP ${HEADSCALE_DERP_PORT} hacia ${BACKEND_HOST}: el relay DERP no pasa por el proxy."
        echo ""
    fi

    # El aviso depende de URL_SCHEME y no de SSL_MODE: con un proxy delante hay
    # HTTPS aunque aquí no corra ningún terminador TLS.
    if [[ "$URL_SCHEME" == "http" ]]; then
        print_warning "El plano de control viaja sin cifrar (HTTP)"
        echo -e "   No expongas esto fuera de una red de confianza."
        echo ""
    fi

    if [[ "$SSL_MODE" == "selfsigned" ]]; then
        print_warning "Estás usando un certificado autofirmado (CA interna de Caddy)"
        echo ""
        echo -e "   ${BOLD}Los clientes Tailscale NO se conectarán hasta que instales la CA.${NC}"
        echo -e "   Sin ella fallan con: ${YELLOW}x509: certificate signed by unknown authority${NC}"
        echo ""
        if [[ -s "${SCRIPT_DIR}/caddy-root-ca.crt" ]]; then
            echo -e "   CA raíz exportada en: ${BOLD}${SCRIPT_DIR}/caddy-root-ca.crt${NC}"
            echo -e "   Cópiala a cada dispositivo e instálala en su almacén de confianza:"
            echo ""
            echo -e "   ${YELLOW}# Linux (Debian/Ubuntu)${NC}"
            echo -e "   ${YELLOW}sudo cp caddy-root-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates${NC}"
            echo -e "   ${YELLOW}# macOS${NC}"
            echo -e "   ${YELLOW}sudo security add-trusted-cert -d -k /Library/Keychains/System.keychain caddy-root-ca.crt${NC}"
            echo -e "   ${YELLOW}# Windows (PowerShell como administrador)${NC}"
            echo -e "   ${YELLOW}Import-Certificate -FilePath caddy-root-ca.crt -CertStoreLocation Cert:\\LocalMachine\\Root${NC}"
            echo ""
            echo -e "   ${BOLD}Android/iOS no admiten CAs propias para Tailscale:${NC} en esos"
            echo -e "   dispositivos necesitas Let's Encrypt (reejecuta el instalador)."
        fi
        echo ""
    fi

    # --- API key: con ella se entra en el panel si no hay OIDC ---
    # Con sólo SSO no sirve para iniciar sesión (el panel la usa por dentro),
    # así que mostrarla como credencial de login confundiría.
    if [[ "${PORTAL_API_KEY_LOGIN:-true}" != "true" ]]; then
        print_info "El panel sólo acepta SSO: la API key de Headscale (en .env) no sirve para entrar."
        print_info "Para usarla como acceso de emergencia: PORTAL_API_KEY_LOGIN=true y ./install.sh"
        echo ""
    elif [[ -n "${HEADSCALE_API_KEY:-}" ]]; then
        echo -e "${YELLOW}${BOLD}┌─────────────────────────────────────────────────────────────────────────┐${NC}"
        echo -e "${YELLOW}${BOLD}│  API KEY PARA ENTRAR EN EL PANEL COMO ADMINISTRADOR                     │${NC}"
        echo -e "${YELLOW}${BOLD}└─────────────────────────────────────────────────────────────────────────┘${NC}"
        echo ""
        echo -e "  ${BOLD}${HEADSCALE_API_KEY}${NC}"
        echo ""
        print_warning "GUÁRDALA AHORA: Headscale sólo la muestra en el momento de crearla."
        print_warning "Da control total sobre tu tailnet. Trátala como una contraseña."
        echo ""
        echo -e "  Si la pierdes, genera otra con:"
        echo -e "  ${YELLOW}docker exec headscale headscale apikeys create --expiration 90d${NC}"
        echo ""
    else
        print_warning "No se generó ninguna API key en esta ejecución"
        echo -e "  Para entrar en el panel con API key necesitas una:"
        echo -e "  ${YELLOW}docker exec headscale headscale apikeys create --expiration 90d${NC}"
        echo ""
    fi

    [[ "$AUTH_PROVIDER" == "authentik" ]] && show_authentik_info

    echo -e "${CYAN}${BOLD}Próximos pasos:${NC}"
    echo ""
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        echo -e "1. Entra en ${BOLD}${HEADSCALE_PUBLIC_URL}/mi-vpn/${NC} e inicia sesión"
    else
        echo -e "1. Entra en ${BOLD}${HEADSCALE_PUBLIC_URL}/mi-vpn/${NC} con la API key de arriba"
    fi
    echo -e "   ${YELLOW}(el panel vive bajo /mi-vpn; /admin redirige ahí)${NC}"
    echo ""
    echo "2. Generar una clave de pre-autenticación para conectar dispositivos:"
    echo -e "   ${YELLOW}docker exec headscale headscale preauthkeys create --user ${ADMIN_USER_ID} --reusable --expiration 24h${NC}"
    echo -e "   ${YELLOW}(--user espera el ID numérico del usuario, no su nombre)${NC}"
    echo ""
    echo "3. Conectar un dispositivo con Tailscale:"
    echo -e "   ${YELLOW}tailscale up --login-server=${HEADSCALE_PUBLIC_URL} --authkey=<clave-del-paso-2>${NC}"
    echo ""
    print_warning "No confundas las tres claves de este stack:"
    echo -e "   • ${BOLD}API key${NC} (hskey-api-...)      -> acceso de administrador a la API y al panel"
    echo -e "   • ${BOLD}Pre-auth key${NC} (hskey-auth-...) -> registrar dispositivos con --authkey"
    echo -e "   • ${BOLD}Auth ID${NC} (hskey-authreq-...)   -> lo imprime 'tailscale up' sin --authkey,"
    echo -e "     y se registra en el panel: Dispositivos → Registrar con Auth ID"
    echo ""

    echo -e "${CYAN}${BOLD}Comandos útiles:${NC}"
    echo ""
    echo "• Ver estado de los servicios:"
    echo -e "  ${YELLOW}docker compose ps${NC}"
    echo ""
    echo "• Ver logs:"
    echo -e "  ${YELLOW}docker compose logs -f${NC}"
    echo ""
    echo "• Reiniciar servicios:"
    echo -e "  ${YELLOW}docker compose restart${NC}"
    echo ""
    echo "• Detener servicios:"
    echo -e "  ${YELLOW}docker compose down${NC}"
    echo ""
    echo "• Reconfigurar:"
    echo -e "  ${YELLOW}./install.sh${NC}"
    echo ""

    echo -e "${CYAN}${BOLD}Archivos generados:${NC}"
    echo ""
    echo "• Configuración: .env"
    echo "• Config Headscale: headscale-config.yaml"
    echo "• Caddyfile: Caddyfile"
    echo "• Override de puertos: docker-compose.override.yml"
    [[ "$SSL_MODE" == "front" ]] && echo "• Snippet del proxy de delante: reverse-proxy/"
    echo "• Datos: $DATA_DIR/"
    echo ""

    print_success "¡Disfruta de tu red privada virtual!"
}

# -----------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# -----------------------------------------------------------------------------

main() {
    clear

    echo -e "${CYAN}${BOLD}"
    cat << "EOF"
╔═══════════════════════════════════════════════════════════════════════════╗
║                                                                           ║
║   ██╗  ██╗███████╗ █████╗ ██████╗ ███████╗ ██████╗ █████╗ ██╗     ███████╗║
║   ██║  ██║██╔════╝██╔══██╗██╔══██╗██╔════╝██╔════╝██╔══██╗██║     ██╔════╝║
║   ███████║█████╗  ███████║██║  ██║███████╗██║     ███████║██║     █████╗  ║
║   ██╔══██║██╔══╝  ██╔══██║██║  ██║╚════██║██║     ██╔══██║██║     ██╔══╝  ║
║   ██║  ██║███████╗██║  ██║██████╔╝███████║╚██████╗██║  ██║███████╗███████╗║
║   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚══════╝║
║                                                                           ║
║                            + MI VPN (panel)                               ║
║                                                                           ║
║              Instalador Interactivo Todo-en-Uno v1.0                     ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    print_info "Este instalador configurará Headscale + el panel Mi VPN con un solo comando"
    print_info "Responde las preguntas a continuación (puedes usar valores por defecto)"
    echo ""

    # Verificar si es una reconfiguración
    local is_reconfigure=false
    if [ -f "$ENV_FILE" ]; then
        echo ""
        print_warning "Se detectó una instalación existente"
        if ask_yes_no "¿Deseas reconfigurar la instalación existente?" "n"; then
            is_reconfigure=true
            print_info "Modo reconfiguración activado"
            print_warning "Los datos existentes se mantendrán, solo se actualizará la configuración"
            echo ""
        else
            print_info "Continuando con la instalación existente..."
            exit 0
        fi
    fi

    # 1. Verificar dependencias
    check_dependencies

    # 2. Cargar configuración existente (si existe y no es reconfiguración)
    if ! $is_reconfigure; then
        load_existing_config || true
    else
        # En modo reconfiguración, cargar siempre la config existente como base
        set -a
        source "$ENV_FILE"
        set +a
    fi

    # 3. Configuración interactiva
    configure_network
    configure_ports
    compute_public_urls
    configure_tailnet
    configure_auth

    # 4. Generar secretos
    generate_secrets

    # 5. Generar archivos de configuración
    generate_env_file
    create_data_dirs
    generate_headscale_config
    generate_caddyfile
    generate_compose_override
    generate_front_proxy_snippet

    # 6. Desplegar
    echo ""
    if ask_yes_no "¿Deseas desplegar el stack ahora?" "y"; then
        # Fase 0: con Authentik, el issuer OIDC tiene que existir antes que
        # Headscale arranque
        start_authentik_first

        # Fase 1: sólo Headscale, para poder emitir la API key
        start_headscale_first
        bootstrap_headscale
        apply_network_policy

        # Fase 2: resto del stack (panel y Caddy). El panel lee la API key de
        # .env, que bootstrap_headscale acaba de actualizar.
        deploy_stack
        show_access_info
    else
        print_info "Configuración completada pero no desplegada"
        print_info "Para desplegar manualmente, ejecuta:"
        echo -e "  ${YELLOW}docker compose up -d${NC}"
    fi

    echo ""
}

# Ejecutar main
main "$@"
