# Contribuir a Headscale + Mi VPN Deployment

¡Gracias por tu interés en contribuir! Este documento proporciona guías para contribuir al proyecto.

## 🎯 Formas de Contribuir

- 🐛 Reportar bugs
- 💡 Sugerir nuevas características
- 📝 Mejorar la documentación
- 🔧 Enviar pull requests con fixes o mejoras
- ⭐ Dar una estrella al proyecto si te resulta útil

## 🐛 Reportar Bugs

Si encuentras un bug:

1. Verifica que no exista ya un [issue abierto](https://github.com/tu-usuario/tailscale-selfhosted/issues)
2. Crea un nuevo issue con:
   - Descripción clara del problema
   - Pasos para reproducirlo
   - Comportamiento esperado vs. comportamiento actual
   - Output de:
     ```bash
     docker compose ps
     docker compose logs
     cat .env  # Sin secretos
     ```
   - Sistema operativo y versión de Docker

## 💡 Sugerir Características

Para sugerir una nueva característica:

1. Verifica que no exista ya en [issues](https://github.com/tu-usuario/tailscale-selfhosted/issues)
2. Crea un issue describiendo:
   - El problema que resuelve
   - Cómo lo implementarías
   - Casos de uso
   - Posible impacto en la configuración existente

## 🔧 Pull Requests

### Proceso

1. **Fork** el repositorio
2. **Crea una rama** desde `main`:
   ```bash
   git checkout -b feature/mi-nueva-caracteristica
   ```
3. **Implementa tus cambios** siguiendo las guías de estilo
4. **Prueba** tu código:
   - Instalación desde cero
   - Reconfiguración sobre instalación existente
   - Casos edge (sin SSL, con OIDC, etc.)
5. **Commit** tus cambios con mensajes descriptivos:
   ```bash
   git commit -m "feat: agregar soporte para PostgreSQL"
   ```
6. **Push** a tu fork:
   ```bash
   git push origin feature/mi-nueva-caracteristica
   ```
7. **Abre un Pull Request** describiendo:
   - Qué cambia
   - Por qué es necesario
   - Cómo lo probaste

### Guía de Estilo

#### Bash Scripts

- Usar `#!/usr/bin/env bash` como shebang
- Usar `set -euo pipefail` para safety
- Validar todos los inputs del usuario
- Manejo explícito de errores
- Funciones con nombres descriptivos en `snake_case`
- Comentarios para lógica no obvia
- Colores para output (ya definidos en install.sh)

Ejemplo:

```bash
validate_domain() {
    local domain="$1"
    
    if [[ "$domain" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]]; then
        return 0
    fi
    
    print_error "Dominio inválido: $domain"
    return 1
}
```

#### YAML/Docker Compose

- Indentación: 2 espacios
- Comentarios descriptivos para secciones
- Variables de entorno con `${VAR:-default}`
- Healthchecks para todos los servicios
- Security best practices (cap_drop, no-new-privileges)

#### Documentación

- Markdown con GitHub Flavored Markdown
- Ejemplos de código en bloques con syntax highlighting
- Screenshots/diagramas cuando ayuden a clarificar
- Links internos con anchors (`#seccion`)

### Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` Nueva característica
- `fix:` Corrección de bug
- `docs:` Cambios en documentación
- `style:` Formateo, espacios, etc. (sin cambios funcionales)
- `refactor:` Refactorización sin cambiar funcionalidad
- `test:` Agregar o corregir tests
- `chore:` Mantenimiento, deps, etc.

Ejemplos:

```
feat: agregar soporte para PostgreSQL como alternativa a SQLite
fix: corregir validación de puertos en install.sh
docs: mejorar sección de troubleshooting en README
refactor: extraer lógica de validación a funciones separadas
```

## 🧪 Testing

Antes de enviar un PR, prueba:

### Instalación Limpia

```bash
# En una VM/contenedor limpio
git clone <tu-fork>
cd tailscale-selfhosted
./install.sh

# Probar diferentes combinaciones:
# - Con SSL (Let's Encrypt y autofirmado)
# - Sin SSL
# - Con OIDC
# - Sin OIDC
```

### Reconfiguración

```bash
# Sobre instalación existente
./install.sh

# Cambiar de HTTP a HTTPS
# Cambiar puertos
# Habilitar/deshabilitar OIDC
```

### Desinstalación

```bash
./uninstall.sh
./uninstall.sh --purge
```

## 📋 Áreas Prioritarias

Contribuciones especialmente bienvenidas en:

1. **Soporte para más distros**
   - Alpine Linux
   - OpenSUSE
   - Gentoo

2. **Backends de base de datos**
   - PostgreSQL
   - MySQL/MariaDB

3. **Opciones de despliegue**
   - Kubernetes/Helm charts
   - Terraform modules
   - Ansible playbook

4. **Monitoreo**
   - Prometheus + Grafana stack
   - Dashboards predefinidos
   - Alertas

5. **Alta disponibilidad**
   - Múltiples instancias de Headscale
   - Load balancing
   - Failover automático

6. **Migración**
   - Script para migrar desde instalación manual
   - Importar desde otros control planes

7. **Testing**
   - Tests automatizados para install.sh
   - CI/CD pipeline
   - Tests de integración

## 🌍 Internacionalización

Si quieres traducir la documentación o los mensajes del instalador:

1. Crea directorio `i18n/<idioma>/`
2. Traduce los archivos principales
3. Actualiza install.sh para detectar locale
4. Envía PR

Idiomas prioritarios: inglés, español, francés, alemán

## 📞 Preguntas

Si tienes preguntas sobre cómo contribuir:

- Abre un [issue de discusión](https://github.com/tu-usuario/tailscale-selfhosted/issues)
- Busca en issues existentes
- Contacta a los maintainers

## 🎓 Recursos

- [Headscale docs](https://headscale.net/)
- [Headscale API](https://headscale.net/stable/ref/api/)
- [Docker Compose docs](https://docs.docker.com/compose/)
- [Caddy docs](https://caddyserver.com/docs/)
- [Bash scripting guide](https://www.gnu.org/software/bash/manual/)

## 📜 Código de Conducta

### Nuestro Compromiso

Este proyecto está comprometido con proporcionar una experiencia libre de acoso para todos, independientemente de:

- Edad
- Tamaño corporal
- Discapacidad
- Etnia
- Identidad y expresión de género
- Nivel de experiencia
- Nacionalidad
- Apariencia personal
- Raza
- Religión
- Identidad y orientación sexual

### Comportamiento Esperado

- Usar lenguaje acogedor e inclusivo
- Respetar puntos de vista diferentes
- Aceptar críticas constructivas con gracia
- Enfocarse en lo mejor para la comunidad
- Mostrar empatía hacia otros miembros

### Comportamiento Inaceptable

- Comentarios despectivos, insultantes o discriminatorios
- Trolling, insultos o ataques personales
- Acoso público o privado
- Publicar información privada sin permiso
- Conducta que razonablemente podría considerarse inapropiada

### Aplicación

Los mantenedores del proyecto tienen el derecho de eliminar, editar o rechazar comentarios, commits, código, ediciones de wiki, issues y otras contribuciones que no estén alineadas con este Código de Conducta.

## 📄 Licencia

Al contribuir, aceptas que tus contribuciones se licencien bajo la misma licencia MIT que el proyecto.

---

¡Gracias por hacer de este proyecto algo mejor! 🎉
