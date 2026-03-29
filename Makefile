# Drayve Makefile — Generic Ops interface for Docker stacks
#
# Usage:
#   make provision NAME=demo DOMAIN=demo.example.com    Provision new server
#   make deploy-dev                                     Deploy to dev
#   make deploy-prod REF=stable-2026-03-24              Deploy to prod
#   make burn NAME=demo                                 Tear down server
#   make status                                         All servers
#   make secrets-init                                   Scaffold AGE key + SOPS config
#   make secrets-template NAME=myhost                   Scaffold SOPS secrets file

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Config ---
ANSIBLE_DIR := ansible
ANSIBLE := cd $(ANSIBLE_DIR) && ansible-playbook
STACK_CONFIG ?= stack.yaml
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

# --- Guards ---
_require-name:
ifndef NAME
	$(error NAME is required. Usage: make <target> NAME=<name>)
endif

_require-domain:
ifndef DOMAIN
	$(error DOMAIN is required. Usage: make provision NAME=<name> DOMAIN=<domain>)
endif

_require-stack:
	@if [ ! -f "$(STACK_CONFIG)" ]; then \
		echo "Error: $(STACK_CONFIG) not found."; \
		echo "Copy an example: cp examples/stack-minimal.yaml stack.yaml"; \
		exit 1; \
	fi

# --- Helpers ---
_stack_val = $(shell $(PYTHON) -c "import yaml; c=yaml.safe_load(open('$(STACK_CONFIG)')); print($(1))" 2>/dev/null)

# --- Targets ---

.PHONY: help validate lint provision deploy-dev deploy-prod burn status smoke secrets-init secrets-template backup

help:  ## Show available targets
	@grep -E '^[a-z][a-z0-9_-]+:.*##' $(MAKEFILE_LIST) | sort | awk -F ':.*##' '{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

validate: _require-stack  ## Validate stack.yaml against schema
	@$(PYTHON) scripts/validate-stack.py $(STACK_CONFIG)

lint:  ## Lint playbooks + roles + validate stack.yaml
	@echo "==> Running ansible-lint..."
	@cd $(ANSIBLE_DIR) && ansible-lint playbooks/ roles/ 2>&1 | grep -v "^WARNING" | sed 's/^/    /'
	@echo "==> Checking stack.yaml schema..."
	@if [ -f "$(STACK_CONFIG)" ]; then \
		$(PYTHON) scripts/validate-stack.py $(STACK_CONFIG); \
	else \
		echo "    No stack.yaml — skipping (copy from examples/)"; \
	fi
	@echo "==> Lint passed"

provision: _require-name _require-domain _require-stack validate  ## Provision new server (NAME= DOMAIN=)
	@provider=$(call _stack_val,c.get('provider',{}).get('type','hetzner')); \
	if [ "$$provider" = "manual" ]; then \
		echo "==> Provider: manual — skipping server creation"; \
	else \
		echo "==> Provisioning $(NAME) ($(DOMAIN)) via $$provider"; \
	fi
	@secrets_mode=$(call _stack_val,c.get('secrets',{}).get('mode','quickstart')); \
	if [ "$$secrets_mode" = "quickstart" ] && [ ! -f "$(ANSIBLE_DIR)/secrets/$(NAME).sops.yml" ]; then \
		echo "==> Generating quickstart secrets"; \
		scripts/secrets-generate.sh $(NAME); \
	fi
	$(ANSIBLE) playbooks/provision.yml -e name=$(NAME) -e domain=$(DOMAIN) -e @../$(STACK_CONFIG)

deploy-dev: _require-stack validate  ## Deploy to dev server
	$(ANSIBLE) playbooks/deploy.yml -e target=group-dev -e @../$(STACK_CONFIG)

deploy-prod: _require-stack validate  ## Deploy to prod (REF= required, CONFIRM=y to skip prompt)
ifndef REF
	$(error REF is required for prod deploy. Usage: make deploy-prod REF=stable-2026-03-24)
endif
	@[ "$(CONFIRM)" = "y" ] || { printf "Deploy $(REF) to PRODUCTION? [y/N] "; read ans; [ "$$ans" = "y" ] || exit 1; }
	$(ANSIBLE) playbooks/deploy.yml -e target=group-prod -e deploy_ref=$(REF) -e @../$(STACK_CONFIG)

burn: _require-name  ## Tear down server (NAME=)
	@echo "==> Deleting $(NAME)"
	@provider=$(call _stack_val,c.get('provider',{}).get('type','hetzner')); \
	if [ "$$provider" = "manual" ]; then \
		echo "    Provider: manual — skipping server deletion, cleaning up inventory only"; \
	else \
		echo "    Deleting server via $$provider"; \
	fi
	@echo "==> Cleaning up inventory"
	@rm -rf "$(ANSIBLE_DIR)/inventory/host_vars/$(NAME)"
	@rm -f "$(ANSIBLE_DIR)/secrets/$(NAME).sops.yml"
	@echo "==> Done"

status:  ## Show server status (optional: NAME=)
ifdef NAME
	@echo "==> $(NAME)"
	@ssh root@$(NAME) "docker ps --format 'table {{.Names}}\t{{.Status}}' | sort" 2>/dev/null || echo "    SSH failed"
else
	@echo "No provider-specific status without NAME. Use: make status NAME=<host>"
endif

smoke: deploy-dev  ## Deploy to dev + verify
	@echo "==> Smoke passed"

secrets-init:  ## Scaffold AGE key + .sops.yaml (one-time setup)
	@scripts/secrets-init.sh

secrets-template: _require-name  ## Scaffold SOPS secrets file for new host (NAME=)
	@scripts/secrets-template.sh $(NAME)

backup: _require-stack  ## Deploy/update backup config
ifdef NAME
	$(ANSIBLE) playbooks/backup.yml -l $(NAME) -e @../$(STACK_CONFIG)
else
	$(ANSIBLE) playbooks/backup.yml -e @../$(STACK_CONFIG)
endif
