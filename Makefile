# Drayve Makefile — Generic Ops interface for Docker stacks
#
# Usage:
#   make init NAME=demo DOMAIN=demo.example.com [HOST=1.2.3.4]
#   make provision NAME=demo
#   make deploy NAME=demo
#   make burn NAME=demo
#   make status NAME=demo

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Config ---
ANSIBLE_DIR := ansible
DEPLOY_DIR := deploy
INVENTORY := $(DEPLOY_DIR)/hosts.yaml
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

# Per-host paths (set when NAME is defined)
ifdef NAME
HOST_DIR := $(DEPLOY_DIR)/$(NAME)
STACK_CONFIG := $(HOST_DIR)/stack.yaml
SECRETS_FILE := $(HOST_DIR)/secrets.yml
endif

ANSIBLE := cd $(ANSIBLE_DIR) && ansible-playbook -i ../$(INVENTORY)

# --- Guards ---
_require-name:
ifndef NAME
	$(error NAME is required. Usage: make <target> NAME=<name>)
endif

_require-host-dir: _require-name
	@if [ ! -d "$(HOST_DIR)" ]; then \
		echo "Error: $(HOST_DIR) not found. Run: make init NAME=$(NAME) DOMAIN=<domain>"; \
		exit 1; \
	fi

_require-stack: _require-host-dir
	@if [ ! -f "$(STACK_CONFIG)" ]; then \
		echo "Error: $(STACK_CONFIG) not found."; \
		exit 1; \
	fi

_require-inventory:
	@if [ ! -f "$(INVENTORY)" ]; then \
		echo "Error: $(INVENTORY) not found. Run: make init NAME=<name> DOMAIN=<domain>"; \
		exit 1; \
	fi

# --- Helpers ---
_stack_val = $(shell $(PYTHON) -c "import yaml; c=yaml.safe_load(open('$(STACK_CONFIG)')); print($(1))" 2>/dev/null)
_host_domain = $(call _stack_val,c.get('stack',{}).get('domain',''))

# --- Targets ---

.PHONY: help init validate lint provision deploy deploy-prod burn status secrets-init secrets-template backup list

help:  ## Show available targets
	@grep -E '^[a-z][a-z0-9_-]+:.*##' $(MAKEFILE_LIST) | sort | awk -F ':.*##' '{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

init: _require-name  ## Scaffold new host (NAME= DOMAIN= [HOST=])
ifndef DOMAIN
	$(error DOMAIN is required. Usage: make init NAME=<name> DOMAIN=<domain> [HOST=<ip>])
endif
	@scripts/init-host.sh $(NAME) $(DOMAIN) $(or $(HOST),)

validate: _require-stack  ## Validate stack.yaml for a host (NAME=)
	@$(PYTHON) scripts/validate-stack.py $(STACK_CONFIG)

lint:  ## Lint playbooks + roles
	@echo "==> Running ansible-lint..."
	@cd $(ANSIBLE_DIR) && ansible-lint playbooks/ roles/ 2>&1 | grep -v "^WARNING" | sed 's/^/    /'
	@echo "==> Lint passed"

provision: _require-name _require-stack _require-inventory validate  ## Provision + deploy server (NAME=)
	@provider=$(call _stack_val,c.get('provider',{}).get('type','hetzner')); \
	domain=$(_host_domain); \
	if [ "$$provider" = "manual" ]; then \
		echo "==> Provider: manual — skipping server creation"; \
	else \
		echo "==> Provisioning $(NAME) ($$domain) via $$provider"; \
	fi
	@secrets_mode=$(call _stack_val,c.get('secrets',{}).get('mode','quickstart')); \
	if [ "$$secrets_mode" = "quickstart" ] && [ ! -f "$(SECRETS_FILE)" ]; then \
		echo "==> Generating quickstart secrets"; \
		scripts/secrets-generate.sh $(NAME); \
	fi
	$(ANSIBLE) playbooks/provision.yml -e drayve_name=$(NAME) -e domain=$(_host_domain) -e @../$(STACK_CONFIG)

deploy: _require-name _require-stack _require-inventory validate  ## Re-deploy a host (NAME=)
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e @../$(STACK_CONFIG)

deploy-prod: _require-name _require-stack _require-inventory validate  ## Deploy to prod (NAME= REF=)
ifndef REF
	$(error REF is required for prod deploy. Usage: make deploy-prod NAME=<name> REF=<tag>)
endif
	@[ "$(CONFIRM)" = "y" ] || { printf "Deploy $(REF) to $(NAME) PRODUCTION? [y/N] "; read ans; [ "$$ans" = "y" ] || exit 1; }
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e deploy_ref=$(REF) -e @../$(STACK_CONFIG)

burn: _require-name  ## Tear down server (NAME= [CONFIRM=y])
	@[ "$(CONFIRM)" = "y" ] || { printf "BURN $(NAME) — delete server + local files? [y/N] "; read ans; [ "$$ans" = "y" ] || exit 1; }
	@if [ -f "$(STACK_CONFIG)" ]; then \
		$(MAKE) -s _burn-with-stack; \
	else \
		$(MAKE) -s _burn-without-stack; \
	fi

_burn-with-stack:
	$(ANSIBLE) playbooks/burn.yml -e drayve_name=$(NAME) -e @../$(STACK_CONFIG)
	@echo "==> Cleaning deploy/$(NAME)/"
	@rm -rf $(HOST_DIR)
	@echo "==> Remove $(NAME) from $(INVENTORY) manually if needed"

_burn-without-stack:
	$(ANSIBLE) playbooks/burn.yml -e drayve_name=$(NAME)
	@rm -rf $(HOST_DIR)

status: _require-name  ## Show server status (NAME=)
	@echo "==> $(NAME)"
	@ssh root@$(NAME) "docker ps --format 'table {{.Names}}\t{{.Status}}' | sort" 2>/dev/null || echo "    SSH failed"

list:  ## List all configured hosts
	@if [ -f "$(INVENTORY)" ]; then \
		echo "Hosts in $(INVENTORY):"; \
		grep -E '^\s{8}[a-z]' $(INVENTORY) | sed 's/:$$//' | awk '{print "  " $$1}'; \
	else \
		echo "No $(INVENTORY) found. Run: make init NAME=<name> DOMAIN=<domain>"; \
	fi

secrets-init:  ## Scaffold AGE key + .sops.yaml (one-time setup)
	@scripts/secrets-init.sh

secrets-template: _require-name  ## Scaffold SOPS secrets file for a host (NAME=)
	@scripts/secrets-template.sh $(NAME)

backup: _require-name _require-stack _require-inventory  ## Deploy/update backup config (NAME=)
	$(ANSIBLE) playbooks/backup.yml -l $(NAME) -e @../$(STACK_CONFIG)
