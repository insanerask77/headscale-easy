# Headscale Easy — https://github.com/insanerask77/headscale-easy
# Run `make` to list the targets.

.DEFAULT_GOAL := help
.PHONY: help up down validate lint test i18n

help: ## Show this help
	@echo "Headscale Easy — make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- Setup --------------------------------------------------------------------

up: ## Start Headscale Easy (docker compose up -d)
	@docker compose up -d

down: ## Stop it (keeps the data)
	@docker compose down

# --- Development ------------------------------------------------------------------

validate: ## Check the project structure and configuration
	@./scripts/validate.sh

lint: ## shellcheck + Python syntax + i18n coverage
	@shellcheck -S warning scripts/*.sh backup/*.sh
	@python3 -m py_compile web/*.py aio/*.py
	@python3 scripts/check_i18n.py

test: ## Unit tests (Python standard library only)
	@python3 -m unittest discover -s tests

i18n: ## Report untranslated UI strings
	@python3 scripts/check_i18n.py
