# Headscale Easy — https://github.com/insanerask77/headscale-easy
# Run `make` to list the targets.

.DEFAULT_GOAL := help
.PHONY: help install uninstall purge install-1x uninstall-1x purge-1x validate lint test i18n status logs restart health \
        up down ps users nodes routes user key apikey backup restore update config

help: ## Show this help
	@echo "Headscale Easy — make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- Setup --------------------------------------------------------------------

install: ## Install the all-in-one image (asks a few questions; run again to update)
	@./install.sh

uninstall: ## Remove the all-in-one container (keeps data)
	@./uninstall.sh

purge: ## Remove the all-in-one container AND all data
	@./uninstall.sh --purge

install-1x: ## 1.x: run the old interactive installer (split compose file, also to reconfigure)
	@./legacy/install-1x.sh

uninstall-1x: ## 1.x: remove the containers (keeps data)
	@./legacy/uninstall-1x.sh

purge-1x: ## 1.x: remove the containers AND all data
	@./legacy/uninstall-1x.sh --purge

# --- Development ------------------------------------------------------------------

validate: ## Check the project structure and configuration
	@./scripts/validate.sh

lint: ## shellcheck + Python syntax + i18n coverage
	@shellcheck -S warning install.sh uninstall.sh legacy/*.sh scripts/*.sh backup/*.sh
	@python3 -m py_compile web/*.py helper/*.py
	@python3 scripts/check_i18n.py

test: ## Unit tests (Python standard library only)
	@python3 -m unittest discover -s tests

i18n: ## Report untranslated UI strings
	@python3 scripts/check_i18n.py

# --- Services ---------------------------------------------------------------------

up: ## Start the stack
	@docker compose up -d

down: ## Stop the stack
	@docker compose down

ps: ## Container status
	@docker compose ps

status: ps

logs: ## Follow logs (service=name for one)
	@./scripts/utils.sh logs $(service)

restart: ## Restart services (service=name for one)
	@./scripts/utils.sh restart $(service)

health: ## Health of every container
	@./scripts/utils.sh health

update: ## Pull new images and recreate containers
	@./scripts/utils.sh update

backup: ## Back up now (also daily; BACKUP_* in .env)
	@./scripts/utils.sh backup

restore: ## Restore a backup (file=backups/headscale-easy-....tar.gz or remote:path/...)
	@test -n "$(file)" || { echo "Usage: make restore file=backups/headscale-easy-YYYYmmdd-HHMMSS.tar.gz"; exit 1; }
	@./scripts/restore.sh $(file)

config: ## Show .env with secrets hidden
	@./scripts/utils.sh config:show

# --- Headscale --------------------------------------------------------------------

users: ## List users
	@./scripts/utils.sh users:list

nodes: ## List machines
	@./scripts/utils.sh nodes:list $(user)

routes: ## List subnet routes and exit nodes
	@./scripts/utils.sh routes:list

user: ## Create a user (name=...)
	@test -n "$(name)" || { echo "Usage: make user name=alice"; exit 1; }
	@./scripts/utils.sh users:create $(name)

key: ## Create a reusable 24h auth key (user=...)
	@test -n "$(user)" || { echo "Usage: make key user=alice"; exit 1; }
	@./scripts/utils.sh preauth:create $(user)

apikey: ## Create a Headscale API key
	@./scripts/utils.sh apikey:create
