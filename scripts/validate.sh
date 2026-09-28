#!/usr/bin/env bash

# =============================================================================
# HEADSCALE + MI VPN - VALIDADOR DE ESTRUCTURA
# =============================================================================
# Script para validar que todos los archivos necesarios están presentes
# Uso: ./scripts/validate.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

echo -e "${CYAN}${BOLD}Validando estructura del proyecto...${NC}\n"

errors=0
warnings=0

# Archivos requeridos
required_files=(
    "install.sh"
    "uninstall.sh"
    "docker-compose.yml"
    ".env.example"
    ".gitignore"
    "README.md"
    "LICENSE"
    "templates/headscale-config.yaml.tmpl"
    "templates/Caddyfile.tmpl"
    "authentik/blueprints/headscale.yaml"
    "authentik/branding/custom.css"
    "portal/app.py"
    "portal/headscale.py"
    "portal/views.py"
    "portal/views_admin.py"
    "portal/static/style.css"
    "scripts/utils.sh"
)

# Archivos opcionales (solo warning)
optional_files=(
    "QUICKSTART.md"
    "CONTRIBUTING.md"
)

# Verificar archivos requeridos
for file in "${required_files[@]}"; do
    if [ -f "$PROJECT_DIR/$file" ]; then
        print_success "Encontrado: $file"
    else
        print_error "Falta archivo requerido: $file"
        ((errors++))
    fi
done

echo ""

# Verificar archivos opcionales
for file in "${optional_files[@]}"; do
    if [ -f "$PROJECT_DIR/$file" ]; then
        print_success "Encontrado: $file"
    else
        print_warning "Falta archivo opcional: $file"
        ((warnings++))
    fi
done

echo ""

# Verificar permisos de ejecución
executables=("install.sh" "uninstall.sh" "scripts/utils.sh")

for file in "${executables[@]}"; do
    if [ -x "$PROJECT_DIR/$file" ]; then
        print_success "Ejecutable: $file"
    else
        print_error "No es ejecutable: $file"
        ((errors++))
    fi
done

echo ""

# Verificar directorios
required_dirs=("templates" "scripts" "data")

for dir in "${required_dirs[@]}"; do
    if [ -d "$PROJECT_DIR/$dir" ]; then
        print_success "Directorio encontrado: $dir/"
    else
        print_warning "Directorio faltante (se creará): $dir/"
        mkdir -p "$PROJECT_DIR/$dir"
        ((warnings++))
    fi
done

echo ""

# Verificar sintaxis de scripts bash
print_info "Verificando sintaxis de scripts bash..."

for script in "install.sh" "uninstall.sh" "scripts/utils.sh"; do
    if bash -n "$PROJECT_DIR/$script" 2>/dev/null; then
        print_success "Sintaxis OK: $script"
    else
        print_error "Error de sintaxis: $script"
        ((errors++))
    fi
done

echo ""

# Verificar sintaxis de YAML/Docker Compose
if command -v docker &>/dev/null; then
    print_info "Verificando sintaxis de docker-compose.yml..."
    cd "$PROJECT_DIR"
    if docker compose config >/dev/null 2>&1; then
        print_success "Sintaxis OK: docker-compose.yml"
    else
        print_error "Error de sintaxis en docker-compose.yml"
        ((errors++))
    fi
else
    print_warning "Docker no instalado, saltando validación de docker-compose.yml"
    ((warnings++))
fi

echo ""

# Verificar .env.example
print_info "Verificando .env.example..."

required_vars=(
    "SERVER_URL"
    "SSL_MODE"
    "DOMAIN"
    "TAILNET_NAME"
    "PORTAL_SESSION_SECRET"
)

missing_vars=()

for var in "${required_vars[@]}"; do
    if grep -q "^${var}=" "$PROJECT_DIR/.env.example" 2>/dev/null; then
        print_success "Variable definida: $var"
    else
        print_error "Variable faltante en .env.example: $var"
        missing_vars+=("$var")
        ((errors++))
    fi
done

echo ""

# Resumen final
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ $errors -eq 0 ] && [ $warnings -eq 0 ]; then
    echo -e "${GREEN}${BOLD}✓ Validación completada sin errores ni warnings${NC}"
    echo ""
    print_success "La estructura del proyecto es correcta"
    print_info "Puedes ejecutar ./install.sh para comenzar la instalación"
    exit 0
elif [ $errors -eq 0 ]; then
    echo -e "${YELLOW}${BOLD}⚠ Validación completada con warnings${NC}"
    echo ""
    print_warning "Se encontraron $warnings warning(s)"
    print_info "El proyecto debería funcionar, pero algunos archivos opcionales faltan"
    print_info "Puedes ejecutar ./install.sh para comenzar la instalación"
    exit 0
else
    echo -e "${RED}${BOLD}✗ Validación fallida${NC}"
    echo ""
    print_error "Se encontraron $errors error(es) y $warnings warning(s)"
    print_error "Corrige los errores antes de ejecutar ./install.sh"

    if [ ${#missing_vars[@]} -gt 0 ]; then
        echo ""
        print_error "Variables faltantes en .env.example:"
        for var in "${missing_vars[@]}"; do
            echo "  - $var"
        done
    fi

    exit 1
fi
