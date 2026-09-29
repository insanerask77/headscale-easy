#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — installer
#  The open source Tailscale alternative: Headscale + a built-in web UI.
#
#  https://github.com/insanerask77/headscale-easy
#  Made by Rafa Madolell (@insanerask77) · https://buymeacoffee.com/insanerask
#  MIT License
#
#  Usage: ./install.sh
#
#  Interactive, idempotent (run it again to reconfigure; data is kept) and
#  bilingual (English / Español).
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
DATA_DIR="${SCRIPT_DIR}/data"
TEMPLATES_DIR="${SCRIPT_DIR}/templates"

INSTALLER_VERSION="1.0.5"
PROJECT_URL="https://github.com/insanerask77/headscale-easy"
SPONSOR_URL="https://buymeacoffee.com/insanerask"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

UI_LANG="${UI_LANG:-en}"

# -----------------------------------------------------------------------------
# Output and prompts
# -----------------------------------------------------------------------------

# t "English" "Español": the text in the installer's language
t() { if [[ "$UI_LANG" == "es" ]]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

print_info()    { echo -e "${BLUE}ℹ${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error()   { echo -e "${RED}✗${NC} $1" >&2; }
print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}${BOLD}  $1${NC}"
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ask_yes_no "question" y|n  -> exit status 0 (yes) / 1 (no)
ask_yes_no() {
    local prompt="$1" default="${2:-n}" response hint
    hint=$([[ "$default" == "y" ]] && t "[Y/n]" "[S/n]" || t "[y/N]" "[s/N]")
    while true; do
        read -r -p "$(echo -e "${CYAN}?${NC} ${prompt} ${hint}: ")" response
        response=$(echo "${response:-$default}" | tr '[:upper:]' '[:lower:]')
        case "$response" in
            y|yes|s|si|sí) return 0 ;;
            n|no) return 1 ;;
            *) print_warning "$(t "Please answer y or n" "Responde s o n")" >&2 ;;
        esac
    done
}

# ask_input "question" "default" [validator]  -> prints the answer
ask_input() {
    local prompt="$1" default="$2" validate="${3:-}" value
    [[ -n "$default" ]] && prompt="$prompt [${default}]"
    while true; do
        read -r -p "$(echo -e "${CYAN}?${NC} ${prompt}: ")" value
        value="${value:-$default}"
        if [[ -n "$validate" ]]; then
            if "$validate" "$value"; then echo "$value"; return 0; fi
            print_warning "$(t "Invalid value, try again" "Valor no válido, inténtalo de nuevo")" >&2
            continue
        fi
        if [[ -n "$value" ]]; then echo "$value"; return 0; fi
        print_warning "$(t "This field cannot be empty" "Este campo no puede estar vacío")" >&2
    done
}

# ask_choice "question" "current" "key|Title|Description" ...  -> prints the key
# The menu goes to stderr so $(...) only captures the answer.
ask_choice() {
    local prompt="$1" current="$2"; shift 2
    local options=("$@") n=$# default_idx=1 i key title desc choice
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
        read -r -p "$(echo -e "${CYAN}?${NC} $(t "Choose an option" "Elige una opción") [1-${n}] [${default_idx}]: ")" choice >&2
        choice="${choice:-$default_idx}"
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= n )); then
            IFS='|' read -r key title desc <<< "${options[$((choice - 1))]}"
            echo "$key"
            return 0
        fi
        print_warning "$(t "Invalid option" "Opción no válida")" >&2
    done
}

validate_domain_or_ip() {
    [[ "$1" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] && return 0
    [[ "$1" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]] && return 0
    [[ "$1" == "localhost" ]]
}
validate_port()         { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); }
validate_email()        { [[ "$1" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; }
validate_alphanumeric() { [[ "$1" =~ ^[a-zA-Z0-9_-]+$ ]]; }
validate_url()          { [[ "$1" =~ ^https?:// ]]; }
validate_optional_ip()  { [[ -z "$1" || "$1" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; }

generate_secret() {
    local length="${1:-32}"
    openssl rand -hex "$length" 2>/dev/null || head -c "$length" /dev/urandom | xxd -p | tr -d '\n'
}

# -----------------------------------------------------------------------------
# Dependencies
# -----------------------------------------------------------------------------

check_command() { command -v "$1" &>/dev/null; }

install_docker() {
    print_info "$(t "Installing Docker with the official script (get.docker.com)..." "Instalando Docker con el script oficial (get.docker.com)...")"
    if ! curl -fsSL https://get.docker.com | sudo sh; then
        print_error "$(t "Docker could not be installed automatically" "No se pudo instalar Docker automáticamente")"
        print_info "https://docs.docker.com/engine/install/"
        return 1
    fi
    if ! groups | grep -qw docker; then
        sudo usermod -aG docker "$USER"
        print_warning "$(t "Log out and back in (or run 'newgrp docker') so the docker group applies" \
                           "Cierra la sesión y vuelve a entrar (o ejecuta 'newgrp docker') para que se aplique el grupo docker")"
    fi
    print_success "$(t "Docker installed" "Docker instalado")"
}

check_dependencies() {
    print_header "$(t "CHECKING DEPENDENCIES" "COMPROBANDO DEPENDENCIAS")"
    local missing=()

    if ! check_command docker; then
        print_warning "$(t "Docker is not installed" "Docker no está instalado")"
        if ask_yes_no "$(t "Install Docker automatically?" "¿Instalar Docker automáticamente?")" "y"; then
            install_docker || missing+=("docker")
        else
            missing+=("docker")
        fi
    else
        print_success "Docker"
        if ! docker info &>/dev/null; then
            print_warning "$(t "The Docker daemon is not running, trying to start it..." "El demonio de Docker no está en marcha, intentando arrancarlo...")"
            sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || missing+=("docker-daemon")
        fi
    fi

    if docker compose version &>/dev/null; then print_success "Docker Compose"; else missing+=("docker-compose-plugin"); fi
    check_command openssl && print_success "OpenSSL" || missing+=("openssl")
    check_command envsubst && print_success "envsubst" || missing+=("gettext (envsubst)")

    if [ ${#missing[@]} -gt 0 ]; then
        print_error "$(t "Missing dependencies:" "Faltan dependencias:") ${missing[*]}"
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

choose_language() {
    local current="$UI_LANG"
    [[ -f "$ENV_FILE" ]] && current=$(grep -E '^UI_LANG=' "$ENV_FILE" | cut -d= -f2 | tr -d '"' || true)
    current="${current:-en}"
    local default_idx=1
    [[ "$current" == "es" ]] && default_idx=2
    echo -e "${BOLD}Language / Idioma${NC}"
    echo -e "  ${BOLD}1${NC}) English"
    echo -e "  ${BOLD}2${NC}) Español"
    local choice
    read -r -p "$(echo -e "${CYAN}?${NC} [1-2] [${default_idx}]: ")" choice
    choice="${choice:-$default_idx}"
    UI_LANG=$([[ "$choice" == "2" ]] && echo "es" || echo "en")
}

# ONE deployment shape: Caddy in front of Headscale and the web UI, routing
# a single domain (/ -> control plane, /admin -> web UI). The only branch is
# who terminates HTTPS, stored in SSL_MODE:
#   letsencrypt  Caddy gets a certificate from Let's Encrypt
#   selfsigned   Caddy signs with its internal CA
#   front        another proxy in front (NPM, nginx, Traefik, Caddy) does TLS;
#                a ready-made snippet is generated for it
#   none         no HTTPS (localhost, trusted LAN, or already inside a VPN)
configure_network() {
    print_header "$(t "NETWORK AND ACCESS" "RED Y ACCESO")"
    print_info "$(t "Domain or IP used to reach the stack. Caddy routes that single name:" \
                    "Dominio o IP con el que se accede al stack. Caddy enruta ese único nombre:")"
    print_info "  / -> Headscale, /admin -> Headscale Easy"
    print_info "$(t "Examples:" "Ejemplos:") vpn.example.com, 192.168.1.100, localhost"
    DOMAIN=$(ask_input "$(t "Domain or IP" "Dominio o IP")" "${DOMAIN:-vpn.example.com}" validate_domain_or_ip)

    local options=()
    if [[ "$DOMAIN" =~ ^[0-9.]+$ ]] || [[ "$DOMAIN" == "localhost" ]]; then
        print_warning "$(t "Let's Encrypt does not issue certificates for IPs or localhost" \
                           "Let's Encrypt no emite certificados para IPs ni localhost")"
    else
        options+=("letsencrypt|$(t "Caddy, with Let's Encrypt" "Caddy, con Let's Encrypt")|$(t "Public, trusted certificate. Needs DNS pointing here and ports 80/443 reachable from the Internet." "Certificado público de confianza. Requiere DNS apuntando aquí y los puertos 80/443 accesibles desde internet.")")
    fi
    options+=("selfsigned|$(t "Caddy, self-signed" "Caddy, autofirmado")|$(t "HTTPS without external dependencies. Caddy's CA must be installed on every client." "HTTPS sin dependencias externas. Hay que instalar la CA de Caddy en cada cliente.")")
    options+=("front|$(t "A proxy you already run" "Un proxy que ya tienes")|$(t "NPM, nginx, Traefik or another Caddy terminates TLS and forwards here. A ready-made snippet is generated." "NPM, nginx, Traefik u otro Caddy termina el TLS y reenvía aquí. Se genera el snippet listo.")")
    options+=("none|$(t "No HTTPS" "Sin HTTPS")|$(t "Plain HTTP: http://${DOMAIN}. For localhost, a trusted LAN or access through another VPN." "HTTP sin cifrar: http://${DOMAIN}. Para localhost, una LAN de confianza o acceso por otra VPN.")")
    SSL_MODE=$(ask_choice "$(t "Who provides HTTPS?" "¿Quién pone el HTTPS?")" "${SSL_MODE:-letsencrypt}" "${options[@]}")

    case "$SSL_MODE" in
        letsencrypt)
            URL_SCHEME="https"; FRONT_PROXY=""
            ACME_EMAIL=$(ask_input "$(t "Email for Let's Encrypt" "Email para Let's Encrypt")" "${ACME_EMAIL:-admin@${DOMAIN}}" validate_email)
            ;;
        selfsigned)
            URL_SCHEME="https"; FRONT_PROXY=""
            print_warning "$(t "You will have to install the CA on every Tailscale client (it is exported at the end)" \
                               "Tendrás que instalar la CA en cada cliente Tailscale (se exporta al final)")"
            ;;
        front)
            # The front proxy speaks HTTPS to the world even if this hop is
            # HTTP: public URLs and cookies follow what the client sees.
            URL_SCHEME="https"
            FRONT_PROXY=$(ask_choice "$(t "Which proxy is in front?" "¿Qué proxy tienes delante?")" "${FRONT_PROXY:-npm}" \
                "npm|Nginx Proxy Manager|$(t "Proxy Host fields plus the Advanced block." "Campos del Proxy Host y el bloque Advanced.")" \
                "nginx|nginx|$(t "A complete server{} for /etc/nginx/conf.d/." "Un server{} completo para /etc/nginx/conf.d/.")" \
                "traefik|Traefik|$(t "Dynamic configuration for the file provider." "Configuración dinámica para el file provider.")" \
                "caddy|Caddy|$(t "The edge Caddyfile block." "El bloque del Caddyfile de borde.")")
            local guess
            guess=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)
            print_info "$(t "Address of THIS machine as seen from the proxy (where it forwards to)." \
                            "Dirección de ESTA máquina vista desde el proxy (adonde reenvía).")"
            BACKEND_HOST=$(ask_input "$(t "IP or hostname of this machine" "IP o hostname de esta máquina")" "${BACKEND_HOST:-${guess:-127.0.0.1}}")
            ;;
        none)
            URL_SCHEME="http"; FRONT_PROXY=""
            print_warning "$(t "The control plane will travel unencrypted: do not expose it to the Internet" \
                               "El plano de control viajará sin cifrar: no lo expongas a internet")"
            ;;
    esac
}

configure_ports() {
    print_header "$(t "PORTS" "PUERTOS")"
    print_info "$(t "Press Enter to accept the defaults." "Pulsa Enter para aceptar los valores por defecto.")"
    if [[ "$SSL_MODE" == "letsencrypt" || "$SSL_MODE" == "selfsigned" ]]; then
        HTTP_PORT=$(ask_input "$(t "Caddy HTTP port (ACME challenge and redirect)" "Puerto HTTP de Caddy (reto ACME y redirección)")" "${HTTP_PORT:-80}" validate_port)
        HTTPS_PORT=$(ask_input "$(t "Caddy HTTPS port" "Puerto HTTPS de Caddy")" "${HTTPS_PORT:-443}" validate_port)
    else
        [[ "$SSL_MODE" == "front" ]] && print_info "$(t "The port where the front proxy reaches Caddy" "El puerto donde el proxy de delante encuentra a Caddy")"
        HTTP_PORT=$(ask_input "$(t "Caddy HTTP port" "Puerto HTTP de Caddy")" "${HTTP_PORT:-80}" validate_port)
        HTTPS_PORT="443"
    fi
    HEADSCALE_GRPC_PORT=$(ask_input "$(t "Headscale gRPC port (internal)" "Puerto gRPC de Headscale (interno)")" "${HEADSCALE_GRPC_PORT:-50443}" validate_port)
    HEADSCALE_HTTP_PORT=$(ask_input "$(t "Headscale HTTP API port (internal)" "Puerto de la API HTTP de Headscale (interno)")" "${HEADSCALE_HTTP_PORT:-8080}" validate_port)
    HEADSCALE_DERP_PORT=$(ask_input "$(t "DERP/STUN port (UDP, must be reachable)" "Puerto DERP/STUN (UDP, debe ser accesible)")" "${HEADSCALE_DERP_PORT:-3478}" validate_port)
    HEADSCALE_METRICS_PORT=$(ask_input "$(t "Headscale metrics port (internal)" "Puerto de métricas de Headscale (interno)")" "${HEADSCALE_METRICS_PORT:-9090}" validate_port)
}

# Public URLs depend on the domain AND the ports. They are what the WORLD sees,
# not the internal ones: Headscale and the UI speak HTTP inside Docker but
# announce https:// when that is what clients use.
compute_public_urls() {
    local suffix=""
    case "$SSL_MODE" in
        letsencrypt|selfsigned)
            [[ "$HTTPS_PORT" != "443" ]] && suffix=":${HTTPS_PORT}"
            HEADSCALE_PUBLIC_URL="https://${DOMAIN}${suffix}" ;;
        front)
            HEADSCALE_PUBLIC_URL="https://${DOMAIN}" ;;
        none)
            [[ "$HTTP_PORT" != "80" ]] && suffix=":${HTTP_PORT}"
            HEADSCALE_PUBLIC_URL="http://${DOMAIN}${suffix}" ;;
    esac
    SERVER_URL="$HEADSCALE_PUBLIC_URL"
    echo ""
    print_info "$(t "Control plane:" "Plano de control:") ${HEADSCALE_PUBLIC_URL}"
    print_info "$(t "Web UI:" "Panel web:") ${HEADSCALE_PUBLIC_URL}/admin/"
}

configure_tailnet() {
    print_header "$(t "TAILNET" "TAILNET")"
    TAILNET_NAME=$(ask_input "$(t "Tailnet / organization name (letters, digits, - and _)" "Nombre de la tailnet / organización (letras, números, - y _)")" "${TAILNET_NAME:-myorg}" validate_alphanumeric)
    ADMIN_USER=$(ask_input "$(t "Initial Headscale user" "Usuario inicial de Headscale")" "${ADMIN_USER:-admin}" validate_alphanumeric)
    IP_PREFIXES_V4=$(ask_input "$(t "IPv4 range for clients (CIDR)" "Rango IPv4 para los clientes (CIDR)")" "${IP_PREFIXES_V4:-100.64.0.0/10}")
    IP_PREFIXES_V6=$(ask_input "$(t "IPv6 range for clients (CIDR)" "Rango IPv6 para los clientes (CIDR)")" "${IP_PREFIXES_V6:-fd7a:115c:a1e0::/48}")
    LOG_LEVEL=$(ask_input "$(t "Log level (trace/debug/info/warn/error)" "Nivel de log (trace/debug/info/warn/error)")" "${LOG_LEVEL:-info}")
}

# Headscale has no password users of its own: without OIDC the web UI only
# accepts an API key (admins) and devices join with pre-auth keys.
#   none       No OIDC: the UI asks for a Headscale API key (admins only).
#   authentik  Authentik inside this stack at /authentik/: accounts with
#              passwords and, optionally, "Sign in with Google".
#   external   An OIDC provider you already run (Keycloak, Authelia, Google...).
configure_auth() {
    print_header "$(t "USER SIGN-IN" "INICIO DE SESIÓN DE USUARIOS")"

    local current="${AUTH_PROVIDER:-}"
    [[ -z "$current" ]] && current=$([[ "${ENABLE_OIDC:-false}" == "true" ]] && echo "external" || echo "none")
    PREV_AUTH_PROVIDER="$current"

    AUTH_PROVIDER=$(ask_choice "$(t "How do users sign in?" "¿Cómo inician sesión los usuarios?")" "$current" \
        "authentik|$(t "Built-in Authentik (recommended)" "Authentik integrado (recomendado)")|$(t "Accounts with passwords and optional Google sign-in at ${HEADSCALE_PUBLIC_URL}/authentik/. Adds 3 containers (~1 GB RAM)." "Cuentas con contraseña y login con Google opcional en ${HEADSCALE_PUBLIC_URL}/authentik/. Añade 3 contenedores (~1 GB de RAM).")" \
        "external|$(t "Your own OIDC provider" "Tu propio proveedor OIDC")|$(t "Keycloak, Authelia, Google or any other provider you already run." "Keycloak, Authelia, Google u otro que ya tengas.")" \
        "none|$(t "API key only" "Sólo API key")|$(t "No user accounts: admins sign in with a Headscale API key and devices join with auth keys." "Sin cuentas: los admins entran con una API key de Headscale y los dispositivos con claves.")")

    case "$AUTH_PROVIDER" in
        none)      configure_auth_none ;;
        authentik) configure_auth_authentik ;;
        external)  configure_auth_external ;;
    esac
    ENABLE_OIDC=$([[ "$AUTH_PROVIDER" == "none" ]] && echo "false" || echo "true")

    # With OIDC, the UI can also accept the Headscale API key as emergency
    # admin access (if the provider is down).
    PORTAL_API_KEY_LOGIN="${PORTAL_API_KEY_LOGIN:-false}"
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        echo ""
        print_info "$(t "The web UI can also accept the Headscale API key to sign in as admin (emergency access)." \
                        "El panel puede aceptar también la API key de Headscale para entrar como admin (acceso de emergencia).")"
        if ask_yes_no "$(t "Allow API key sign-in too?" "¿Permitir también el login con API key?")" \
                      "$([[ "$PORTAL_API_KEY_LOGIN" == "true" ]] && echo y || echo n)"; then
            PORTAL_API_KEY_LOGIN="true"
        else
            PORTAL_API_KEY_LOGIN="false"
        fi
    else
        PORTAL_API_KEY_LOGIN="true"   # the only way in without OIDC
    fi

    echo ""
    print_info "$(t "Network isolation: each user only reaches THEIR OWN devices (admins included)." \
                    "Aislamiento de red: cada usuario sólo alcanza SUS dispositivos (admins incluidos).")"
    if ask_yes_no "$(t "Isolate the network per user?" "¿Aislar la red por usuario?")" \
                  "$([[ "${NETWORK_ISOLATION:-true}" == "true" ]] && echo y || echo n)"; then
        NETWORK_ISOLATION="true"
    else
        NETWORK_ISOLATION="false"
    fi
}

configure_auth_none() {
    OIDC_ISSUER_URL=""; OIDC_CLIENT_ID=""; OIDC_CLIENT_SECRET=""
    OIDC_SCOPE="openid profile email"; OIDC_EMAIL_CLAIM="email"
    GOOGLE_CLIENT_ID=""; GOOGLE_CLIENT_SECRET=""; PORTAL_ADMIN_EMAILS=""
}

configure_auth_external() {
    print_info "$(t "Issuer URL examples:" "Ejemplos de Issuer URL:")"
    print_info "  Keycloak: https://auth.example.com/realms/master"
    print_info "  Google:   https://accounts.google.com"
    print_info "$(t "Register these two redirect URIs in the provider:" "Registra estas dos Redirect URIs en el proveedor:")"
    print_info "  ${HEADSCALE_PUBLIC_URL}/oidc/callback    (Headscale)"
    print_info "  ${HEADSCALE_PUBLIC_URL}/admin/callback   (Headscale Easy)"

    # Values from the built-in Authentik are useless for another provider
    if [[ "${OIDC_ISSUER_URL:-}" == */authentik/application/o/* ]]; then
        OIDC_ISSUER_URL=""; OIDC_CLIENT_SECRET=""
    fi
    OIDC_ISSUER_URL=$(ask_input "$(t "OIDC issuer URL" "Issuer URL del proveedor OIDC")" "${OIDC_ISSUER_URL:-}" validate_url)
    # The UI uses the SAME client as Headscale: the 'sub' then matches and it
    # can find each user in Headscale without relying on names.
    OIDC_CLIENT_ID=$(ask_input "$(t "Client ID (shared by Headscale and the web UI)" "Client ID (el mismo para Headscale y el panel)")" "${OIDC_CLIENT_ID:-headscale}")
    OIDC_CLIENT_SECRET=$(ask_input "Client Secret" "${OIDC_CLIENT_SECRET:-}")
    OIDC_SCOPE=$(ask_input "$(t "OIDC scopes (space separated)" "Scopes OIDC (separados por espacios)")" "${OIDC_SCOPE:-openid profile email}")
    OIDC_EMAIL_CLAIM=$(ask_input "$(t "Email claim" "Claim del email")" "${OIDC_EMAIL_CLAIM:-email}")
    GOOGLE_CLIENT_ID=""; GOOGLE_CLIENT_SECRET=""
    echo ""
    print_info "$(t "Web UI admins: comma separated emails. Everyone else only sees their own devices." \
                    "Administradores del panel: emails separados por comas. El resto sólo ve sus dispositivos.")"
    PORTAL_ADMIN_EMAILS=$(ask_input "$(t "Admin emails" "Emails de administradores")" "${PORTAL_ADMIN_EMAILS:-${ACME_EMAIL:-}}")
}

configure_auth_authentik() {
    # Headscale and the UI validate the issuer against the PUBLIC URL, so
    # they must reach it from their containers; there 'localhost' is the
    # container itself.
    if [[ "$DOMAIN" == "localhost" ]]; then
        print_error "$(t "Authentik does not work with DOMAIN=localhost" "Authentik no funciona con DOMAIN=localhost")"
        print_info "$(t "Use this machine's LAN IP or a domain instead." "Usa la IP de esta máquina en la LAN o un dominio.")"
        exit 1
    fi

    OIDC_ISSUER_URL="${HEADSCALE_PUBLIC_URL}/authentik/application/o/headscale/"
    OIDC_CLIENT_ID="headscale"
    # Kept across re-installs; the blueprint syncs it into Authentik
    if [[ -z "${OIDC_CLIENT_SECRET:-}" || "$PREV_AUTH_PROVIDER" != "authentik" ]]; then
        OIDC_CLIENT_SECRET=$(generate_secret 32)
    fi
    OIDC_SCOPE="openid profile email"; OIDC_EMAIL_CLAIM="email"; PORTAL_ADMIN_EMAILS=""

    echo ""
    print_info "$(t "Authentik creates the admin user 'akadmin' on first start. Use a real email of yours:" \
                    "Authentik crea el usuario admin 'akadmin' en su primer arranque. Usa un email real tuyo:")"
    print_info "$(t "with Google sign-in, that Google account would sign in as akadmin." \
                    "con login con Google, esa cuenta de Google entraría como akadmin.")"
    AUTHENTIK_ADMIN_EMAIL=$(ask_input "$(t "Authentik admin email" "Email del admin de Authentik")" \
                                      "${AUTHENTIK_ADMIN_EMAIL:-${ACME_EMAIL:-admin@${DOMAIN}}}" validate_email)

    if [[ "$SSL_MODE" == "front" ]]; then
        echo ""
        print_info "$(t "Headscale reaches ${HEADSCALE_PUBLIC_URL} through the front proxy. If ${DOMAIN} resolves" \
                        "Headscale llega a ${HEADSCALE_PUBLIC_URL} a través del proxy de delante. Si ${DOMAIN} resuelve")"
        print_info "$(t "to a public IP and your router has no NAT loopback, give the proxy's LAN IP. Empty = DNS." \
                        "a una IP pública y tu router no hace NAT loopback, indica la IP del proxy en la LAN. Vacío = DNS.")"
        FRONT_PROXY_IP=$(ask_input "$(t "Front proxy IP (optional)" "IP del proxy de delante (opcional)")" "${FRONT_PROXY_IP:-}" validate_optional_ip)
    else
        FRONT_PROXY_IP=""
    fi

    # Admins may have changed it from the web UI since: it saves the last
    # mode in data/web/mfa-required, newer than the one in .env
    local mfa_saved=""
    [[ -s "$DATA_DIR/web/mfa-required" ]] && mfa_saved=$(tr -d '[:space:]' < "$DATA_DIR/web/mfa-required")
    [[ "$mfa_saved" =~ ^(admins|everyone|optional)$ ]] || mfa_saved=""
    MFA_REQUIRED=$(ask_choice "$(t "Two-factor authentication (authenticator app or passkey)" "Autenticación en dos pasos (app de códigos o passkey)")" "${mfa_saved:-${MFA_REQUIRED:-admins}}" \
        "admins|$(t "Required for admins (recommended)" "Obligatoria para admins (recomendado)")|$(t "Admins must set it up the first time they sign in; members may." "Los admins la configuran al entrar la primera vez; los miembros, si quieren.")" \
        "everyone|$(t "Required for everyone" "Obligatoria para todos")|$(t "Every user must set it up when signing in." "Todos la configuran al iniciar sesión.")" \
        "optional|$(t "Optional" "Opcional")|$(t "Nobody is forced; each user decides in their account settings." "Nadie está obligado; cada uno decide en su cuenta.")")

    echo ""
    if [[ "$URL_SCHEME" != "https" ]]; then
        print_warning "$(t "Google sign-in needs HTTPS (Google rejects http:// redirect URIs)" \
                           "El login con Google necesita HTTPS (Google rechaza redirect URIs http://)")"
        GOOGLE_CLIENT_ID=""; GOOGLE_CLIENT_SECRET=""
    elif ask_yes_no "$(t "Add \"Sign in with Google\"?" "¿Añadir \"Login con Google\"?")" "$([[ -n "${GOOGLE_CLIENT_ID:-}" ]] && echo y || echo n)"; then
        echo ""
        print_info "$(t "Create an OAuth client ID (Web application) at https://console.cloud.google.com/apis/credentials with:" \
                        "Crea un OAuth client ID (Web application) en https://console.cloud.google.com/apis/credentials con:")"
        print_info "  Authorized JavaScript origins: ${HEADSCALE_PUBLIC_URL}"
        print_info "  Authorized redirect URIs:      ${HEADSCALE_PUBLIC_URL}/authentik/source/oauth/callback/google/"
        GOOGLE_CLIENT_ID=$(ask_input "Google Client ID" "${GOOGLE_CLIENT_ID:-}")
        GOOGLE_CLIENT_SECRET=$(ask_input "Google Client Secret" "${GOOGLE_CLIENT_SECRET:-}")
    else
        GOOGLE_CLIENT_ID=""; GOOGLE_CLIENT_SECRET=""
    fi
}

generate_secrets() {
    # Signs the web UI session cookies; kept so open sessions survive
    [[ -z "${PORTAL_SESSION_SECRET:-}" ]] && PORTAL_SESSION_SECRET=$(generate_secret 32)

    [[ "$AUTH_PROVIDER" == "authentik" ]] || return 0
    # Never regenerated once set: the Postgres password is fixed when the
    # database is created and the secret key signs sessions and tokens. The
    # admin password is only read on Authentik's first start.
    [[ -z "${AUTHENTIK_SECRET_KEY:-}" ]] && AUTHENTIK_SECRET_KEY=$(generate_secret 32)
    [[ -z "${AUTHENTIK_PG_PASS:-}" ]] && AUTHENTIK_PG_PASS=$(generate_secret 24)
    [[ -z "${AUTHENTIK_BOOTSTRAP_PASSWORD:-}" ]] && AUTHENTIK_BOOTSTRAP_PASSWORD=$(generate_secret 12)
    # The web UI's own OIDC client
    if [[ -z "${PORTAL_OIDC_CLIENT_SECRET:-}" || "${PORTAL_OIDC_CLIENT_ID:-}" != "headscale-easy" ]]; then
        PORTAL_OIDC_CLIENT_SECRET=$(generate_secret 32)
    fi
    PORTAL_OIDC_CLIENT_ID="headscale-easy"
    # API token of the web UI's Authentik service account (the blueprint
    # creates both): lets admins change the two-factor mode from the web UI
    [[ -z "${PORTAL_AUTHENTIK_TOKEN:-}" ]] && PORTAL_AUTHENTIK_TOKEN=$(generate_secret 32)
    return 0
}

# How the web UI signs in and which uid/gid it runs with
portal_settings() {
    case "$AUTH_PROVIDER" in
        authentik)
            PORTAL_OIDC_ISSUER="${HEADSCALE_PUBLIC_URL}/authentik/application/o/headscale-easy/" ;;
        external)
            PORTAL_OIDC_ISSUER="$OIDC_ISSUER_URL"
            PORTAL_OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
            PORTAL_OIDC_CLIENT_SECRET="$OIDC_CLIENT_SECRET" ;;
        *)
            PORTAL_OIDC_ISSUER=""; PORTAL_OIDC_CLIENT_ID=""; PORTAL_OIDC_CLIENT_SECRET="" ;;
    esac
    # The UI writes headscale-config.yaml (DNS) and talks to the Docker socket
    # (validate and restart Headscale): it runs as the owner of the project
    # files plus the socket's group, never as root.
    PORTAL_UID=$(stat -c %u "$SCRIPT_DIR")
    PORTAL_GID=$(stat -c %g "$SCRIPT_DIR")
    DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 999)
}

# -----------------------------------------------------------------------------
# Generated files
# -----------------------------------------------------------------------------

generate_env_file() {
    portal_settings
    # Compose reads COMPOSE_PROFILES from .env: a plain 'docker compose up -d'
    # also starts Authentik when it is enabled.
    COMPOSE_PROFILES=$([[ "$AUTH_PROVIDER" == "authentik" ]] && echo "authentik" || echo "")

    cat > "$ENV_FILE" <<EOF
# =============================================================================
# Headscale Easy — configuration (generated by install.sh on $(date -u +%Y-%m-%d))
# ${PROJECT_URL}
# Re-run ./install.sh to change it. NEVER commit this file: it holds secrets.
# =============================================================================

# Installer and web UI default language (en, es)
UI_LANG=${UI_LANG}

# --- Network and access ------------------------------------------------------
# One domain. Caddy routes it: / -> Headscale, /admin -> Headscale Easy.
DOMAIN=${DOMAIN}
URL_SCHEME=${URL_SCHEME}
SERVER_URL=${SERVER_URL}
HEADSCALE_PUBLIC_URL=${HEADSCALE_PUBLIC_URL}
# letsencrypt | selfsigned | front | none
SSL_MODE=${SSL_MODE}
ACME_EMAIL=${ACME_EMAIL:-}
# With SSL_MODE=front: npm | nginx | traefik | caddy, and this machine's
# address as seen from that proxy
FRONT_PROXY=${FRONT_PROXY:-}
BACKEND_HOST=${BACKEND_HOST:-}
# With SSL_MODE=front: the proxy's LAN IP, so containers reach the public URL
# without NAT loopback (empty = DNS)
FRONT_PROXY_IP=${FRONT_PROXY_IP:-}

# --- Ports ---------------------------------------------------------------------
HTTP_PORT=${HTTP_PORT}
HTTPS_PORT=${HTTPS_PORT}
HEADSCALE_GRPC_PORT=${HEADSCALE_GRPC_PORT}
HEADSCALE_HTTP_PORT=${HEADSCALE_HTTP_PORT}
HEADSCALE_DERP_PORT=${HEADSCALE_DERP_PORT}
HEADSCALE_METRICS_PORT=${HEADSCALE_METRICS_PORT}

# --- Tailnet ---------------------------------------------------------------------
TAILNET_NAME=${TAILNET_NAME}
ADMIN_USER=${ADMIN_USER}
IP_PREFIXES_V4=${IP_PREFIXES_V4}
IP_PREFIXES_V6=${IP_PREFIXES_V6}
LOG_LEVEL=${LOG_LEVEL}
# Each user only reaches their own devices (ACL autogroup:self). Only applied
# when Headscale has no policy yet; an existing policy is never overwritten.
NETWORK_ISOLATION=${NETWORK_ISOLATION}

# Headscale API key used by the web UI (and admin sign-in without OIDC).
# Filled in by the installer. When it expires, re-run ./install.sh.
APIKEY_EXPIRATION=${APIKEY_EXPIRATION:-90d}
HEADSCALE_API_KEY=${HEADSCALE_API_KEY:-}

# --- Sign-in ---------------------------------------------------------------------
# authentik | external | none
AUTH_PROVIDER=${AUTH_PROVIDER}
COMPOSE_PROFILES=${COMPOSE_PROFILES}
ENABLE_OIDC=${ENABLE_OIDC}
OIDC_ISSUER_URL="${OIDC_ISSUER_URL}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID}"
OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET}"
OIDC_SCOPE="${OIDC_SCOPE}"
OIDC_EMAIL_CLAIM="${OIDC_EMAIL_CLAIM}"

# --- Web UI ----------------------------------------------------------------------
PORTAL_SESSION_SECRET=${PORTAL_SESSION_SECRET}
PORTAL_OIDC_ISSUER="${PORTAL_OIDC_ISSUER:-}"
PORTAL_OIDC_CLIENT_ID="${PORTAL_OIDC_CLIENT_ID:-}"
PORTAL_OIDC_CLIENT_SECRET="${PORTAL_OIDC_CLIENT_SECRET:-}"
# Also accept the Headscale API key to sign in as admin (emergency access)
PORTAL_API_KEY_LOGIN=${PORTAL_API_KEY_LOGIN}
# Admins: members of these groups (Authentik) or owners of these emails
PORTAL_ADMIN_GROUPS="${PORTAL_ADMIN_GROUPS:-vpn-admins,authentik Admins}"
PORTAL_ADMIN_EMAILS="${PORTAL_ADMIN_EMAILS:-}"
# uid/gid the UI runs with (owner of the project files) and the Docker socket gid
PORTAL_UID=${PORTAL_UID}
PORTAL_GID=${PORTAL_GID}
DOCKER_GID=${DOCKER_GID}

# --- Authentik (AUTH_PROVIDER=authentik) -----------------------------------------
# Never change the secret key or the database password after the first start.
AUTHENTIK_SECRET_KEY=${AUTHENTIK_SECRET_KEY:-}
AUTHENTIK_PG_PASS=${AUTHENTIK_PG_PASS:-}
# Only read on Authentik's FIRST start (change the password in Authentik)
AUTHENTIK_ADMIN_EMAIL=${AUTHENTIK_ADMIN_EMAIL:-}
AUTHENTIK_BOOTSTRAP_PASSWORD=${AUTHENTIK_BOOTSTRAP_PASSWORD:-}
# Two-factor authentication: admins (required for admins), everyone, optional.
# Admins can change it live from the web UI (Settings > General); ./install.sh
# applies the value chosen here.
MFA_REQUIRED=${MFA_REQUIRED:-admins}
# API token of the web UI's Authentik service account (two-factor mode only)
PORTAL_AUTHENTIK_TOKEN=${PORTAL_AUTHENTIK_TOKEN:-}
# Sign in with Google (empty = off). Google redirect URI:
#   ${HEADSCALE_PUBLIC_URL}/authentik/source/oauth/callback/google/
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-}"

# --- Advanced ----------------------------------------------------------------------
TZ=${TZ:-UTC}
NETWORK_NAME=headscale-net
HSE_VERSION=${HSE_VERSION:-latest}
HEADSCALE_IMAGE_TAG=${HEADSCALE_IMAGE_TAG:-latest}
CADDY_IMAGE_TAG=${CADDY_IMAGE_TAG:-2-alpine}
AUTHENTIK_IMAGE_TAG=${AUTHENTIK_IMAGE_TAG:-2026.8.3}
EOF
    chmod 600 "$ENV_FILE"
    print_success "$(t "Written" "Generado"): .env"
}

# The dns: block of headscale-config.yaml lives between markers. The web UI
# edits it; when the config is regenerated the existing block is kept so those
# changes survive. Otherwise the default one is written.
dns_block() {
    local begin="# >>> dns: managed by Headscale Easy (do not edit between these markers)"
    local end="# <<< dns"
    local current="$SCRIPT_DIR/headscale-config.yaml"

    if [[ -f "$current" ]] && grep -qF "$begin" "$current"; then
        awk -v b="$begin" -v e="$end" '
            $0 == b { on = 1; print; next }
            on { print }
            on && $0 == e { exit }' "$current"
        return 0
    fi

    # Default. A dns: section without markers (older install) keeps its
    # base_domain and MagicDNS setting.
    local base="${TAILNET_NAME}.headscale.net" magic="true" b m
    if [[ -f "$current" ]]; then
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

# Device key expiry: a marked block the web UI edits (Settings → General). Kept
# when the config is regenerated; 180 days (Tailscale's default) otherwise.
key_expiry_block() {
    local begin="  # >>> key expiry: managed by Headscale Easy (do not edit between these markers)"
    local end="  # <<< key expiry"
    local current="$SCRIPT_DIR/headscale-config.yaml"
    if [[ -f "$current" ]] && grep -qF "$begin" "$current"; then
        awk -v b="$begin" -v e="$end" '
            $0 == b { on = 1; print; next }
            on { print }
            on && $0 == e { exit }' "$current"
        return 0
    fi
    printf '%s\n  expiry: %s\n%s\n' "$begin" "${NODE_KEY_EXPIRY:-180d}" "$end"
}

generate_headscale_config() {
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        local scope_list="" s
        for s in $OIDC_SCOPE; do scope_list+="    - ${s}"$'\n'; done
        # Authentik does not mark emails as verified (admins create the users,
        # there is no verification step) and Headscale only syncs verified ones
        # by default.
        local email_verified="true"
        [[ "$AUTH_PROVIDER" == "authentik" ]] && email_verified="false"
        # Who may sign in is decided by the provider (Authentik: application
        # bindings). Add allowed_groups/allowed_domains by hand if needed.
        OIDC_CONFIG=$(cat <<EOFC
oidc:
  # Headscale does not start until it can read the issuer's configuration
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
        OIDC_CONFIG="# OIDC disabled"
    fi

    # Headscale always sits behind Caddy: trust Docker's private bridge range
    # (172.16.0.0/12) so logs show real client IPs. Its port is not published.
    TRUSTED_PROXIES_CONFIG=$'trusted_proxies:\n  - 172.16.0.0/12'
    if [[ "$SSL_MODE" == "front" ]]; then
        TRUSTED_PROXIES_CONFIG+=$'\n'"  # Add your front proxy's CIDR to see real client IPs, e.g.:"
        TRUSTED_PROXIES_CONFIG+=$'\n'"  # - 192.168.1.50/32"
    fi
    DNS_CONFIG=$(dns_block)
    KEY_EXPIRY_CONFIG=$(key_expiry_block)

    export SERVER_URL HEADSCALE_HTTP_PORT HEADSCALE_METRICS_PORT HEADSCALE_GRPC_PORT \
           IP_PREFIXES_V4 IP_PREFIXES_V6 TAILNET_NAME HEADSCALE_DERP_PORT LOG_LEVEL \
           OIDC_CONFIG TRUSTED_PROXIES_CONFIG DNS_CONFIG KEY_EXPIRY_CONFIG
    envsubst < "$TEMPLATES_DIR/headscale-config.yaml.tmpl" > "$SCRIPT_DIR/headscale-config.yaml"
    print_success "$(t "Written" "Generado"): headscale-config.yaml"
}

generate_caddyfile() {
    case "$SSL_MODE" in
        letsencrypt)
            CADDY_DOMAIN="$DOMAIN"
            CADDY_TLS="tls ${ACME_EMAIL}"
            CADDY_HSTS="Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\"" ;;
        selfsigned)
            CADDY_DOMAIN="$DOMAIN"
            CADDY_TLS="tls internal"
            CADDY_HSTS="# HSTS disabled (self-signed certificate)" ;;
        *)
            # ':80' rather than 'http://DOMAIN': without a certificate the site
            # is reached by several names (localhost, LAN IP, the name the
            # front proxy uses) and a domain would 404 all the others.
            CADDY_DOMAIN=":80"
            CADDY_TLS="# No TLS here: Caddy only routes over HTTP"
            CADDY_HSTS=$([[ "$SSL_MODE" == "front" ]] && echo "# HSTS: sent by the front proxy" || echo "# HSTS disabled (no TLS)") ;;
    esac

    # HTTP -> HTTPS only when Caddy holds the certificate
    HTTP_REDIRECT=""
    if [[ "$SSL_MODE" == "letsencrypt" || "$SSL_MODE" == "selfsigned" ]]; then
        HTTP_REDIRECT=$(printf 'http://%s {\n    redir https://{host}{uri} permanent\n}' "$DOMAIN")
    fi

    if [[ "$AUTH_PROVIDER" == "authentik" ]]; then
        # Authentik builds its issuer and redirects from the request scheme.
        # With SSL_MODE=front Caddy receives HTTP, so force what the browser sees.
        local proto_line="# X-Forwarded-Proto: from the incoming request"
        [[ "$SSL_MODE" == "front" ]] && proto_line="header_up X-Forwarded-Proto https"
        AUTHENTIK_ROUTE=$(cat <<EOF
    # Authentik (identity provider) under /authentik/ (AUTHENTIK_WEB__PATH):
    # the prefix is not stripped. /authentik alone redirects so it does not
    # fall into Headscale's catch-all.
    redir /authentik /authentik/ 308
    # Shortcut to the add-user form
    redir /add-user /authentik/if/flow/headscale-easy-add-user/ 302
    handle /authentik/* {
        reverse_proxy authentik-server:9000 {
            header_up X-Real-IP {remote_host}
            ${proto_line}
        }
    }
EOF
        )
    else
        AUTHENTIK_ROUTE="    # Authentik disabled (AUTH_PROVIDER=${AUTH_PROVIDER})"
    fi

    export CADDY_DOMAIN CADDY_TLS CADDY_HSTS HEADSCALE_HTTP_PORT HTTP_REDIRECT AUTHENTIK_ROUTE
    envsubst < "$TEMPLATES_DIR/Caddyfile.tmpl" > "$SCRIPT_DIR/Caddyfile"
    print_success "$(t "Written" "Generado"): Caddyfile"
}

# Headscale and the UI validate the OIDC issuer against the PUBLIC URL of
# Authentik (it builds the issuer from the request host and scheme and OIDC
# clients require an exact match), so they must reach that URL from inside
# their containers:
#   - DOMAIN resolves to the host gateway, where Caddy publishes its ports:
#     same port and certificate a browser sees, no NAT loopback needed.
#   - With SSL_MODE=front TLS is on another machine: FRONT_PROXY_IP or DNS.
#   - With SSL_MODE=selfsigned they also need to trust Caddy's CA.
authentik_reachability_block() {
    local target=""
    if [[ "$DOMAIN" =~ ^[0-9.]+$ ]]; then
        target=""
    elif [[ "$SSL_MODE" == "front" ]]; then
        target="${FRONT_PROXY_IP:-}"
    else
        target="host-gateway"
    fi
    local hosts="" out="" svc ca_env ca_vol=""
    [[ -n "$target" ]] && hosts=$'\n'"    extra_hosts:"$'\n'"      - \"${DOMAIN}:${target}\""
    for svc in headscale web; do
        ca_vol=""
        if [[ "$SSL_MODE" == "selfsigned" ]]; then
            # Go (Headscale) adds SSL_CERT_DIR; the UI (Python) loads EXTRA_CA_FILE
            case "$svc" in
                headscale) ca_env="      - SSL_CERT_DIR=/etc/ssl/certs:/caddy-ca" ;;
                web)       ca_env="      - EXTRA_CA_FILE=/caddy-ca/root.crt" ;;
            esac
            ca_vol=$'\n'"    volumes:"$'\n'"      - ./data/caddy-ca:/caddy-ca:ro"$'\n'"    environment:"$'\n'"${ca_env}"
        fi
        [[ -z "$hosts" && -z "$ca_vol" ]] && continue
        out+=$'\n'"  ${svc}:${hosts}${ca_vol}"
    done
    [[ -z "$out" ]] && return 0
    printf '\n  # Reach the public Authentik URL from the containers (OIDC)%s\n' "$out"
}

# Caddy's published ports depend on who holds the certificate. Compose MERGES
# 'ports' lists (only adds), so variable ports cannot live in
# docker-compose.yml: this override would be unable to remove them.
generate_compose_override() {
    local ports_block
    if [[ "$SSL_MODE" == "letsencrypt" || "$SSL_MODE" == "selfsigned" ]]; then
        ports_block=$'      - "${HTTP_PORT:-80}:80"\n      - "${HTTPS_PORT:-443}:443"\n      - "${HTTPS_PORT:-443}:443/udp"'
    else
        ports_block=$'      # No TLS on this machine: HTTP only\n      - "${HTTP_PORT:-80}:80"'
    fi
    local oidc_block=""
    [[ "$AUTH_PROVIDER" == "authentik" ]] && oidc_block=$(authentik_reachability_block)

    cat > "$SCRIPT_DIR/docker-compose.override.yml" <<EOF
# Generated by install.sh — do not edit: it is rewritten on every run.
# Caddy's ports (they depend on SSL_MODE) and, with Authentik, how the
# containers reach its public URL.
services:
  caddy:
    ports:
${ports_block}
${oidc_block}
EOF
    print_success "$(t "Written" "Generado"): docker-compose.override.yml"
}

# Caddy already routes by path, so the front proxy has ONE destination and
# needs nothing about /admin or CORS: the snippet is short.
generate_front_proxy_snippet() {
    [[ "$SSL_MODE" == "front" ]] || return 0
    local tmpl out
    case "$FRONT_PROXY" in
        npm)     tmpl="front-npm.md.tmpl";      out="NGINX-PROXY-MANAGER.md" ;;
        nginx)   tmpl="front-nginx.conf.tmpl";  out="nginx-${DOMAIN}.conf" ;;
        traefik) tmpl="front-traefik.yml.tmpl"; out="traefik-${DOMAIN}.yml" ;;
        caddy)   tmpl="front-caddy.tmpl";       out="Caddyfile" ;;
        *) return 0 ;;
    esac
    mkdir -p "$SCRIPT_DIR/reverse-proxy"
    export DOMAIN BACKEND_HOST HTTP_PORT HEADSCALE_DERP_PORT HEADSCALE_PUBLIC_URL
    # Explicit variable list: the nginx/Traefik templates are full of $host,
    # $http_upgrade... that a bare envsubst would wipe out.
    envsubst '${DOMAIN} ${BACKEND_HOST} ${HTTP_PORT} ${HEADSCALE_DERP_PORT} ${HEADSCALE_PUBLIC_URL}' \
        < "$TEMPLATES_DIR/$tmpl" > "$SCRIPT_DIR/reverse-proxy/$out"
    print_success "$(t "Written" "Generado"): reverse-proxy/${out} ($(t "copy it to the proxy machine" "cópialo a la máquina del proxy"))"
}

generate_files() {
    print_header "$(t "GENERATING CONFIGURATION" "GENERANDO CONFIGURACIÓN")"
    generate_secrets
    generate_env_file
    mkdir -p "$DATA_DIR/caddy-logs" "$DATA_DIR/web"
    generate_headscale_config
    generate_caddyfile
    generate_compose_override
    generate_front_proxy_snippet
}

# -----------------------------------------------------------------------------
# Deployment
# -----------------------------------------------------------------------------

pull_images() {
    print_info "$(t "Pulling images (the web UI is built locally if its image is not published yet)..." \
                    "Descargando imágenes (el panel se construye en local si su imagen aún no está publicada)...")"
    docker compose pull --ignore-pull-failures --quiet 2>/dev/null || true
    docker compose build --quiet web
}

# With Authentik, Headscale does not start (only_start_if_oidc_is_available)
# until the issuer answers on the public URL. That needs, in order: Authentik
# with the blueprint applied and Caddy routing /authentik/.
start_authentik_first() {
    [[ "$AUTH_PROVIDER" == "authentik" ]] || return 0
    print_header "$(t "STARTING AUTHENTIK" "ARRANCANDO AUTHENTIK")"
    docker compose up -d authentik-postgresql authentik-server authentik-worker
    # --force-recreate: on reconfigure Caddy runs with the old Caddyfile
    docker compose up -d --no-deps --force-recreate caddy

    if [[ "$SSL_MODE" == "selfsigned" ]]; then
        export_root_ca
        mkdir -p "$SCRIPT_DIR/data/caddy-ca"
        [[ -s "$SCRIPT_DIR/caddy-root-ca.crt" ]] && cp "$SCRIPT_DIR/caddy-root-ca.crt" "$SCRIPT_DIR/data/caddy-ca/root.crt"
    fi

    # Behind a front proxy, Headscale reaches the issuer THROUGH that proxy
    if [[ "$SSL_MODE" == "front" ]]; then
        echo ""
        print_warning "$(t "Headscale validates sign-ins against ${HEADSCALE_PUBLIC_URL}/authentik/ through the front proxy," \
                           "Headscale valida los inicios de sesión contra ${HEADSCALE_PUBLIC_URL}/authentik/ a través del proxy de delante,")"
        print_warning "$(t "so that proxy must be ready now: reverse-proxy/ has its configuration." \
                           "así que ese proxy tiene que estar listo ya: reverse-proxy/ tiene su configuración.")"
        read -r -p "$(echo -e "${CYAN}?${NC} $(t "Press Enter once the proxy forwards ${DOMAIN} to ${BACKEND_HOST}:${HTTP_PORT}" "Pulsa Enter cuando el proxy reenvíe ${DOMAIN} a ${BACKEND_HOST}:${HTTP_PORT}"): ")" _
    fi

    print_info "$(t "Waiting for Authentik to publish the OIDC provider (1-3 min the first time)..." \
                    "Esperando a que Authentik publique el proveedor OIDC (1-3 min la primera vez)...")"
    local url="http://authentik-server:9000/authentik/application/o/headscale/.well-known/openid-configuration"
    local waited=0
    while [ $waited -lt 300 ]; do
        if docker exec caddy wget -q -O /dev/null "$url" 2>/dev/null; then
            echo ""
            # Authentik re-applies a blueprint only when the FILE changes, not
            # its environment variables: force it so a new domain, secret or
            # Google setting takes effect (takes 1-2 min).
            print_info "$(t "Applying the blueprint with the current settings (1-2 min)..." \
                            "Aplicando el blueprint con la configuración actual (1-2 min)...")"
            if ! docker exec authentik-worker ak apply_blueprint custom/headscale.yaml >/dev/null 2>&1; then
                print_error "$(t "Could not apply the Authentik blueprint" "No se pudo aplicar el blueprint de Authentik")"
                print_info "docker exec authentik-worker ak apply_blueprint custom/headscale.yaml"
                exit 1
            fi
            print_success "$(t "Authentik is ready" "Authentik está listo")"
            return 0
        fi
        sleep 5; waited=$((waited + 5)); echo -n "."
    done
    echo ""
    print_error "$(t "Authentik did not publish the OIDC provider after 300 s" "Authentik no publicó el proveedor OIDC tras 300 s")"
    print_info "docker compose logs authentik-server authentik-worker"
    exit 1
}

# Authentik after switching to another sign-in method. Its data volumes are
# kept: nothing that cannot be recovered is deleted.
stop_unused_authentik() {
    if [[ "$AUTH_PROVIDER" != "authentik" ]] && docker ps -a --format '{{.Names}}' | grep -qE '^authentik-(server|worker|postgresql)$'; then
        print_info "$(t "Stopping Authentik (no longer used; its data is kept)..." "Deteniendo Authentik (ya no se usa; sus datos se conservan)...")"
        docker compose --profile authentik rm -sf authentik-server authentik-worker authentik-postgresql >/dev/null
    fi
    return 0
}

start_headscale_first() {
    # Headscale must be up BEFORE the UI: only a running Headscale can issue
    # the API key the UI needs.
    print_header "$(t "STARTING HEADSCALE" "ARRANCANDO HEADSCALE")"
    # --force-recreate: on reconfigure the config changes but the container does not
    docker compose up -d --force-recreate headscale
    local max_wait=90 waited=0 state
    [[ "$ENABLE_OIDC" == "true" ]] && max_wait=180
    while [ $waited -lt $max_wait ]; do
        state=$(docker inspect -f '{{.State.Health.Status}}' headscale 2>/dev/null || echo "starting")
        if [[ "$state" == "healthy" ]]; then echo ""; print_success "$(t "Headscale is ready" "Headscale está listo")"; return 0; fi
        sleep 2; waited=$((waited + 2)); echo -n "."
    done
    echo ""
    print_error "$(t "Headscale did not become healthy after ${max_wait} s" "Headscale no llegó a estar sano tras ${max_wait} s")"
    print_info "docker compose logs headscale"
    if [[ "$ENABLE_OIDC" == "true" ]]; then
        print_info "$(t "With OIDC, the usual cause is that it cannot reach the issuer from its container:" \
                        "Con OIDC, la causa habitual es que no alcanza el issuer desde su contenedor:")"
        print_info "  ${OIDC_ISSUER_URL}.well-known/openid-configuration"
    fi
    exit 1
}

# 0 if Headscale knows the key and it has not expired
api_key_valid() {
    local key="$1" prefix expires
    [[ "$key" =~ ^hskey-api- ]] || return 1
    prefix="${key:0:22}"  # hskey-api- + 12-character prefix (may contain "-")
    expires=$(docker exec headscale headscale apikeys list --output json 2>/dev/null | tr -d ' \t\n' \
              | grep -oE "\"prefix\":\"${prefix}[^\"]*\",\"expiration\":\{\"seconds\":[0-9]+" | grep -oE '[0-9]+$' || true)
    [[ -n "$expires" && "$expires" -gt "$(date +%s)" ]]
}

bootstrap_headscale() {
    print_header "$(t "USER AND API KEY" "USUARIO Y API KEY")"
    # Initial user (idempotent: 'users create' fails if it exists)
    if docker exec headscale headscale users list --output json 2>/dev/null | grep -q "\"name\": *\"${ADMIN_USER}\""; then
        print_info "$(t "User '${ADMIN_USER}' already exists" "El usuario '${ADMIN_USER}' ya existe")"
    elif docker exec headscale headscale users create "${ADMIN_USER}" >/dev/null 2>&1; then
        print_success "$(t "User created:" "Usuario creado:") ${ADMIN_USER}"
    else
        print_error "$(t "Could not create user" "No se pudo crear el usuario") '${ADMIN_USER}'"
        exit 1
    fi
    # Headscale 0.29 wants the numeric id in 'preauthkeys create --user'
    ADMIN_USER_ID=$(docker exec headscale headscale users list --output json 2>/dev/null | tr -d ' \t\n' \
                    | grep -oE "\"id\":[0-9]+,\"name\":\"${ADMIN_USER}\"" | grep -oE '[0-9]+' | head -1)
    ADMIN_USER_ID="${ADMIN_USER_ID:-<id>}"

    # API key: Headscale only shows the full value once. Keep the one in .env
    # while it is still valid so reconfiguring does not pile up keys. The web
    # UI renews it by itself and saves the new one in data/web/api-key: if the
    # .env one is no longer valid, try that one before creating another.
    local renewed="$DATA_DIR/web/api-key"
    if [[ -s "$renewed" ]] && ! api_key_valid "${HEADSCALE_API_KEY:-}" && api_key_valid "$(tr -d '[:space:]' < "$renewed")"; then
        HEADSCALE_API_KEY=$(tr -d '[:space:]' < "$renewed")
        sed -i "s|^HEADSCALE_API_KEY=.*|HEADSCALE_API_KEY=${HEADSCALE_API_KEY}|" "$ENV_FILE"
        print_info "$(t "Using the API key renewed by the web UI" "Se usa la API key renovada por el panel")"
        return 0
    fi
    if [[ -n "${HEADSCALE_API_KEY:-}" ]]; then
        local prefix expires
        prefix="${HEADSCALE_API_KEY:0:22}"  # hskey-api- + 12-character prefix (may contain "-")
        expires=$(docker exec headscale headscale apikeys list --output json 2>/dev/null | tr -d ' \t\n' \
                  | grep -oE "\"prefix\":\"${prefix}[^\"]*\",\"expiration\":\{\"seconds\":[0-9]+" | grep -oE '[0-9]+$' || true)
        if [[ -n "$expires" && "$expires" -gt "$(date +%s)" ]]; then
            print_info "$(t "Keeping the API key from .env" "Se conserva la API key de .env")"
            return 0
        fi
        print_warning "$(t "The API key in .env expired or no longer exists: creating a new one" "La API key de .env caducó o ya no existe: se crea otra")"
    fi
    HEADSCALE_API_KEY=$(docker exec headscale headscale apikeys create --expiration "${APIKEY_EXPIRATION:-90d}" 2>/dev/null | tr -d '\r\n')
    if [[ ! "$HEADSCALE_API_KEY" =~ ^hskey- ]]; then
        print_error "$(t "The API key could not be created" "No se pudo crear la API key")"
        HEADSCALE_API_KEY=""
        return 0
    fi
    sed -i "s|^HEADSCALE_API_KEY=.*|HEADSCALE_API_KEY=${HEADSCALE_API_KEY}|" "$ENV_FILE"
    print_success "$(t "API key created (valid ${APIKEY_EXPIRATION:-90d}) and saved to .env" "API key creada (válida ${APIKEY_EXPIRATION:-90d}) y guardada en .env")"
}

# Per-user isolation policy: autogroup:member -> autogroup:self. Only applied
# when Headscale has no policy yet: an existing one is never overwritten.
apply_network_policy() {
    [[ "${NETWORK_ISOLATION:-true}" == "true" ]] || return 0
    if docker exec headscale headscale policy get >/dev/null 2>&1; then
        print_info "$(t "Headscale already has an ACL policy: kept as is" "Headscale ya tiene una política ACL: se conserva")"
        return 0
    fi
    local policy
    policy=$(cat <<'EOFP'
{
  // Generated by Headscale Easy (NETWORK_ISOLATION=true).
  // Each user only reaches their own devices, admins included.
  // Edit it in the web UI: Access controls → Policy editor.
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}
  ]
}
EOFP
    )
    # The Headscale image is distroless (no sh): the policy goes in via stdin
    if printf '%s\n' "$policy" | docker exec -i headscale headscale policy set -f /dev/stdin >/dev/null 2>&1; then
        print_success "$(t "Network isolation applied: each user only reaches their own devices" "Aislamiento aplicado: cada usuario sólo alcanza sus dispositivos")"
    else
        print_warning "$(t "Could not apply the isolation policy; add it in the web UI:" "No se pudo aplicar la política de aislamiento; añádela en el panel:")"
        printf '%s\n' "$policy"
    fi
}

deploy_stack() {
    print_header "$(t "STARTING THE STACK" "ARRANCANDO EL STACK")"
    stop_unused_authentik
    docker compose up -d
    # Caddyfile and UI settings may have changed: 'up' does not re-read files
    docker compose restart caddy web >/dev/null
    local waited=0
    while [ $waited -lt 60 ]; do
        [[ "$(docker inspect -f '{{.State.Health.Status}}' headscale-easy 2>/dev/null)" == "healthy" ]] && break
        sleep 2; waited=$((waited + 2)); echo -n "."
    done
    echo ""
    print_success "$(t "Stack running" "Stack en marcha")"
    export_root_ca
}

# The blueprint only sets the two-factor mode when it creates the policy (so
# restarts never revert what admins set from the web UI): apply the one
# chosen in this run through the web UI, which has the Authentik token.
apply_mfa_mode() {
    [[ "$AUTH_PROVIDER" == "authentik" ]] || return 0
    local result="" tries=0
    # The blueprint applied a moment ago may still be creating the token
    while [ $tries -lt 6 ]; do
        result=$(docker exec headscale-easy python /app/mfa.py set "${MFA_REQUIRED:-admins}" 2>&1) && break
        result=""
        sleep 5; tries=$((tries + 1))
    done
    if [[ -n "$result" ]]; then
        print_success "$(t "Two-factor authentication:" "Autenticación en dos pasos:") ${MFA_REQUIRED:-admins}"
    else
        print_warning "$(t "Could not apply the two-factor mode; change it from the web UI (Settings > General)" \
                           "No se pudo aplicar el modo de dos pasos; cámbialo desde el panel (Ajustes > General)")"
        print_info "docker exec headscale-easy python /app/mfa.py set ${MFA_REQUIRED:-admins}"
    fi
}

# With SSL_MODE=selfsigned, Tailscale clients reject Caddy's certificate
# ("x509: certificate signed by unknown authority") until its CA is installed.
export_root_ca() {
    [[ "${SSL_MODE:-none}" == "selfsigned" ]] || return 0
    local src="/data/caddy/pki/authorities/local/root.crt" dst="${SCRIPT_DIR}/caddy-root-ca.crt" waited=0
    while [ $waited -lt 30 ]; do
        if docker exec caddy test -f "$src" 2>/dev/null && docker exec caddy cat "$src" > "$dst" 2>/dev/null && [[ -s "$dst" ]]; then
            chmod 644 "$dst"
            return 0
        fi
        sleep 2; waited=$((waited + 2))
    done
    rm -f "$dst"
    print_warning "$(t "Could not export Caddy's root CA:" "No se pudo exportar la CA raíz de Caddy:") docker exec caddy cat ${src} > caddy-root-ca.crt"
}

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

show_summary() {
    local url="${HEADSCALE_PUBLIC_URL}"
    print_header "$(t "HEADSCALE EASY IS READY" "HEADSCALE EASY ESTÁ LISTO")"
    echo -e "  ${BOLD}$(t "Web UI" "Panel web"):${NC}        ${url}/admin/"
    echo -e "  ${BOLD}$(t "Control plane" "Plano de control"):${NC} ${url}"
    echo ""

    if [[ "$SSL_MODE" == "front" ]]; then
        print_warning "$(t "Configure the front proxy with the file in reverse-proxy/ and point ${DOMAIN} at it." \
                           "Configura el proxy de delante con el fichero de reverse-proxy/ y apunta ${DOMAIN} a él.")"
        print_warning "$(t "Open UDP ${HEADSCALE_DERP_PORT} straight to ${BACKEND_HOST}: DERP does not go through the proxy." \
                           "Abre el UDP ${HEADSCALE_DERP_PORT} directo a ${BACKEND_HOST}: DERP no pasa por el proxy.")"
        echo ""
    fi
    [[ "$URL_SCHEME" == "http" ]] && { print_warning "$(t "No HTTPS: keep this inside a trusted network." "Sin HTTPS: úsalo sólo en una red de confianza.")"; echo ""; }
    if [[ "$SSL_MODE" == "selfsigned" && -s "${SCRIPT_DIR}/caddy-root-ca.crt" ]]; then
        print_warning "$(t "Self-signed certificate: install caddy-root-ca.crt on every client (Android/iOS need Let's Encrypt)." \
                           "Certificado autofirmado: instala caddy-root-ca.crt en cada cliente (Android/iOS necesitan Let's Encrypt).")"
        echo ""
    fi

    echo -e "  ${BOLD}$(t "Sign in" "Inicio de sesión"):${NC}"
    case "$AUTH_PROVIDER" in
        authentik)
            echo -e "    $(t "User" "Usuario"): ${BOLD}akadmin${NC}   $(t "Password" "Contraseña"): ${BOLD}${AUTHENTIK_BOOTSTRAP_PASSWORD}${NC}"
            echo -e "    $(t "(first-start password: change it in" "(contraseña del primer arranque: cámbiala en") ${url}/authentik/if/user/)"
            echo -e "    $(t "Add people at" "Da de alta usuarios en") ${BOLD}${url}/add-user${NC} $(t "or from Users in the web UI." "o desde Usuarios en el panel.")"
            echo -e "    $(t "Authentik admin:" "Administración de Authentik:") ${url}/authentik/if/admin/"
            [[ -n "${GOOGLE_CLIENT_ID:-}" ]] && echo -e "    $(t "Google sign-in is on: new Google users have no group until an admin adds them." "Login con Google activo: los usuarios nuevos no tienen grupo hasta que un admin los añade.")"
            ;;
        external)
            echo -e "    $(t "With your OIDC provider. Admins:" "Con tu proveedor OIDC. Admins:") ${PORTAL_ADMIN_EMAILS:-—}" ;;
        none)
            echo -e "    $(t "With this Headscale API key (admin access, keep it secret):" "Con esta API key de Headscale (acceso de admin, guárdala en secreto):")"
            echo -e "    ${BOLD}${HEADSCALE_API_KEY:-}${NC}" ;;
    esac
    echo ""
    echo -e "  ${BOLD}$(t "Connect a device" "Conectar un dispositivo"):${NC}"
    echo -e "    ${YELLOW}tailscale up --login-server=${url}${NC}"
    echo -e "    $(t "or generate an auth key in the web UI (Settings → Keys)." "o genera una clave en el panel (Ajustes → Claves).")"
    echo ""
    echo -e "  ${BOLD}$(t "Useful commands" "Comandos útiles"):${NC} docker compose ps · docker compose logs -f · ./install.sh"
    echo ""
    echo -e "  ${CYAN}Headscale Easy v${INSTALLER_VERSION}${NC} · $(t "by" "por") Rafa Madolell · ${PROJECT_URL}"
    echo -e "  ☕ $(t "If it saves you time, buy me a coffee:" "Si te ahorra tiempo, invítame a un café:") ${SPONSOR_URL}"
    echo -e "  ⭐ $(t "And a star on GitHub helps a lot." "Y una estrella en GitHub ayuda mucho.")"
    echo ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

banner() {
    echo -e "${CYAN}${BOLD}"
    cat <<'EOF'
   _   _                _               _        _____
  | | | | ___  __ _  __| |___  ___ __ _| | ___  | ____|__ _ ___ _   _
  | |_| |/ _ \/ _` |/ _` / __|/ __/ _` | |/ _ \ |  _| / _` / __| | | |
  |  _  |  __/ (_| | (_| \__ \ (_| (_| | |  __/ | |__| (_| \__ \ |_| |
  |_| |_|\___|\__,_|\__,_|___/\___\__,_|_|\___| |_____\__,_|___/\__, |
                                                                |___/
EOF
    echo -e "${NC}  The open source Tailscale alternative · v${INSTALLER_VERSION}"
    echo -e "  by Rafa Madolell (@insanerask77) · ${PROJECT_URL}"
    echo ""
}

main() {
    clear 2>/dev/null || true
    banner
    choose_language

    if [[ -f "$ENV_FILE" ]]; then
        echo ""
        print_info "$(t "Existing installation found: its settings are the defaults (data is kept)." \
                        "Hay una instalación: sus valores son los de por defecto (los datos se conservan).")"
        local chosen="$UI_LANG"
        set -a
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        set +a
        UI_LANG="$chosen"
    fi

    check_dependencies
    configure_network
    configure_ports
    compute_public_urls
    configure_tailnet
    configure_auth
    generate_files

    echo ""
    if ! ask_yes_no "$(t "Deploy the stack now?" "¿Desplegar el stack ahora?")" "y"; then
        print_info "$(t "Configuration written. Deploy later with:" "Configuración escrita. Despliega más tarde con:") ./install.sh"
        exit 0
    fi

    pull_images
    start_authentik_first
    start_headscale_first
    bootstrap_headscale
    apply_network_policy
    deploy_stack
    apply_mfa_mode
    show_summary
}

main "$@"
