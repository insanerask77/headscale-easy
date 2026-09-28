# Makefile para Headscale + Mi VPN
# Comandos útiles para gestionar el proyecto

.PHONY: help install uninstall clean validate status logs restart backup update

help: ## Mostrar esta ayuda
	@echo "Headscale + Mi VPN - Comandos disponibles:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""

install: ## Ejecutar el instalador interactivo
	@./install.sh

uninstall: ## Desinstalar (mantiene datos)
	@./uninstall.sh

purge: ## Desinstalar y eliminar todos los datos
	@./uninstall.sh --purge

clean: ## Limpiar archivos generados (no afecta contenedores corriendo)
	@echo "Limpiando archivos generados..."
	@rm -f .env headscale-config.yaml Caddyfile docker-compose.override.yml
	@rm -rf data/
	@echo "✓ Archivos limpiados"

validate: ## Validar estructura del proyecto
	@./scripts/validate.sh

# Gestión de servicios

status: ## Ver estado de servicios
	@./scripts/utils.sh status

logs: ## Ver logs de todos los servicios
	@./scripts/utils.sh logs

logs-headscale: ## Ver logs solo de Headscale
	@./scripts/utils.sh logs headscale

logs-portal: ## Ver logs solo del panel Mi VPN
	@./scripts/utils.sh logs portal

restart: ## Reiniciar servicios
	@./scripts/utils.sh restart

health: ## Verificar estado de salud de servicios
	@./scripts/utils.sh health

# Docker Compose directo

up: ## Levantar servicios (detecta SSL automáticamente)
	@if [ -f .env ]; then \
		. .env && \
		if [ "$$ENABLE_SSL" = "true" ]; then \
			docker compose --profile ssl up -d; \
		else \
			docker compose up -d; \
		fi; \
	else \
		echo "Error: .env no existe. Ejecuta 'make install' primero."; \
		exit 1; \
	fi

down: ## Detener servicios
	@docker compose down

ps: ## Ver servicios corriendo
	@docker compose ps

# Gestión de Headscale

user-create: ## Crear usuario en Headscale (user=nombre)
	@if [ -z "$(user)" ]; then \
		echo "Uso: make user-create user=nombre"; \
		exit 1; \
	fi
	@./scripts/utils.sh users:create $(user)

users-list: ## Listar usuarios de Headscale
	@./scripts/utils.sh users:list

nodes-list: ## Listar nodos de Headscale
	@./scripts/utils.sh nodes:list

routes-list: ## Listar rutas de Headscale
	@./scripts/utils.sh routes:list

preauth-key: ## Generar clave de pre-autenticación (user=nombre)
	@if [ -z "$(user)" ]; then \
		echo "Uso: make preauth-key user=nombre"; \
		exit 1; \
	fi
	@./scripts/utils.sh preauth:create $(user)

# Utilidades

backup: ## Crear backup de datos
	@./scripts/utils.sh backup

update: ## Actualizar imágenes de Docker
	@./scripts/utils.sh update

config-show: ## Mostrar configuración actual (.env)
	@./scripts/utils.sh config:show

# Desarrollo

dev-shell-headscale: ## Abrir shell en contenedor Headscale
	@docker exec -it headscale /bin/sh

dev-shell-portal: ## Abrir shell en el contenedor del panel Mi VPN
	@docker exec -it mi-vpn /bin/sh

dev-test: ## Ejecutar pruebas de configuración
	@echo "Ejecutando pruebas de generación de configuración..."
	@bash -c 'source scripts/test-config-generation.sh 2>/dev/null || echo "Script de prueba no encontrado"'
