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
ANSIBLE_LINT := $(if $(wildcard .venv/bin/ansible-lint),.venv/bin/ansible-lint,ansible-lint)
MOLECULE := $(if $(wildcard .venv/bin/molecule),PATH="$(CURDIR)/.venv/bin:$(PATH)" .venv/bin/molecule,$(shell command -v uv >/dev/null 2>&1 && echo "uv run molecule" || echo "molecule"))

# Load test env (HCLOUD_TOKEN etc.) if present
-include .env.test
export

# Per-host paths (set when NAME is defined)
ifdef NAME
HOST_DIR := $(DEPLOY_DIR)/$(NAME)
STACK_CONFIG := $(HOST_DIR)/stack.yaml
SECRETS_FILE := $(HOST_DIR)/secrets.yml
endif

ANSIBLE := cd $(ANSIBLE_DIR) && ansible-playbook -i ../$(INVENTORY) -i inventory/

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

.PHONY: help init validate lint provision deploy deploy-prod burn status secrets-init secrets-template backup list test test-role test-integration setup unban bans auth-sync dashboards

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
	@cd $(ANSIBLE_DIR) && $(CURDIR)/$(ANSIBLE_LINT) playbooks/ roles/ 2>&1 | grep -v "^WARNING" | sed 's/^/    /'
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
	@$(MAKE) -s _burn-backup-local
	@echo "==> Cleaning deploy/$(NAME)/"
	@rm -rf $(HOST_DIR)
	@echo "==> Remove $(NAME) from $(INVENTORY) manually if needed"

_burn-without-stack:
	$(ANSIBLE) playbooks/burn.yml -e drayve_name=$(NAME)
	@$(MAKE) -s _burn-backup-local
	@rm -rf $(HOST_DIR)

_burn-backup-local:
	@_has_files=0; \
	for f in compose.override.yml env.override stack.yaml secrets.yml; do \
		if [ -f "$(HOST_DIR)/$$f" ]; then _has_files=1; break; fi; \
	done; \
	if [ "$$_has_files" = "1" ]; then \
		echo "==> Backing up local user files from deploy/$(NAME)/:"; \
		mkdir -p /tmp/drayve-burn-$(NAME); \
		for f in compose.override.yml env.override stack.yaml secrets.yml; do \
			if [ -f "$(HOST_DIR)/$$f" ]; then \
				cp "$(HOST_DIR)/$$f" "/tmp/drayve-burn-$(NAME)/$$f"; \
				echo "    $$f -> /tmp/drayve-burn-$(NAME)/$$f"; \
			fi; \
		done; \
	fi

status: _require-name _require-inventory  ## Show server status (NAME=)
	@_host=$$($(PYTHON) -c "import yaml; d=yaml.safe_load(open('$(INVENTORY)')); print(d.get('all',{}).get('children',{}).get('drayve',{}).get('hosts',{}).get('$(NAME)',{}).get('ansible_host','$(NAME)'))" 2>/dev/null || echo "$(NAME)"); \
	echo "==> $(NAME) ($$_host)"; \
	ssh root@$$_host "docker ps --format 'table {{.Names}}\t{{.Status}}' | sort" 2>/dev/null || echo "    SSH failed"

logs: _require-name _require-inventory  ## Tail container logs (NAME= [SVC=])
	@_host=$$($(PYTHON) -c "import yaml; d=yaml.safe_load(open('$(INVENTORY)')); print(d.get('all',{}).get('children',{}).get('drayve',{}).get('hosts',{}).get('$(NAME)',{}).get('ansible_host','$(NAME)'))" 2>/dev/null || echo "$(NAME)"); \
	ssh root@$$_host "cd /opt/drayve/deploy/stack && docker compose logs -f --tail 100 $(SVC)"

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

_require-hcloud-token:
ifndef HCLOUD_TOKEN
	$(error HCLOUD_TOKEN is required. Copy .env.test.example to .env.test and add your token)
endif

test-role: _require-name _require-hcloud-token  ## Test a single role with Molecule (NAME=common|docker|...)
	@if [ ! -d "$(ANSIBLE_DIR)/roles/$(NAME)/molecule" ]; then \
		echo "Error: No Molecule scenario for role '$(NAME)'. Roles with tests:"; \
		ls -d $(ANSIBLE_DIR)/roles/*/molecule 2>/dev/null | sed 's|.*/roles/\(.*\)/molecule|  \1|'; \
		exit 1; \
	fi
	@echo "==> Testing role: $(NAME)"
	@cd $(ANSIBLE_DIR)/roles/$(NAME) && $(MOLECULE) test

test-integration: _require-hcloud-token  ## Full stack integration test (needs HCLOUD_TOKEN)
	@echo "==> Running integration test (creates Hetzner server)"
	@$(MOLECULE) test -s integration

test-authelia: _require-hcloud-token  ## Authelia + LLDAP integration test (needs HCLOUD_TOKEN)
	@echo "==> Running Authelia integration test (creates Hetzner server)"
	@$(MOLECULE) test -s authelia

test-authentik: _require-hcloud-token  ## Authentik integration test (needs HCLOUD_TOKEN)
	@echo "==> Running Authentik integration test (creates Hetzner server)"
	@$(MOLECULE) test -s authentik

test-authentik-ldap: _require-hcloud-token  ## Authentik + LDAP Source test (needs HCLOUD_TOKEN)
	@echo "==> Running Authentik + LDAP integration test (creates Hetzner server)"
	@$(MOLECULE) test -s authentik-ldap

test-auth-none: _require-hcloud-token  ## No-auth integration test (needs HCLOUD_TOKEN)
	@echo "==> Running no-auth integration test (creates Hetzner server)"
	@$(MOLECULE) test -s auth-none

test-backup: _require-hcloud-token  ## Backup integration test (needs HCLOUD_TOKEN)
	@echo "==> Running backup integration test (creates Hetzner server)"
	@$(MOLECULE) test -s backup

test-sops: _require-hcloud-token  ## SOPS secrets integration test (needs HCLOUD_TOKEN)
	@echo "==> Running SOPS integration test (creates Hetzner server)"
	@$(MOLECULE) test -s sops

setup:  ## One-time setup: venv + git hooks
	@echo "==> Setting up venv..."
	@python3 -m venv .venv
	@.venv/bin/pip install -q -r requirements.txt
	@echo "==> Installing git hooks..."
	@cp scripts/hooks/pre-push .git/hooks/pre-push
	@chmod +x .git/hooks/pre-push
	@echo "==> Done. Activate with: source .venv/bin/activate"

bans: _require-name _require-inventory  ## List active CrowdSec bans (NAME=)
	@_host=$$($(PYTHON) -c "import yaml; d=yaml.safe_load(open('$(INVENTORY)')); print(d.get('all',{}).get('children',{}).get('drayve',{}).get('hosts',{}).get('$(NAME)',{}).get('ansible_host','$(NAME)'))" 2>/dev/null || echo "$(NAME)"); \
	ssh root@$$_host "docker exec crowdsec cscli decisions list" 2>/dev/null || echo "    SSH failed"

unban: _require-name _require-inventory  ## Unban an IP (NAME= IP=)
ifndef IP
	$(error IP is required. Usage: make unban NAME=<name> IP=<ip>)
endif
	@_host=$$($(PYTHON) -c "import yaml; d=yaml.safe_load(open('$(INVENTORY)')); print(d.get('all',{}).get('children',{}).get('drayve',{}).get('hosts',{}).get('$(NAME)',{}).get('ansible_host','$(NAME)'))" 2>/dev/null || echo "$(NAME)"); \
	ssh root@$$_host "docker exec crowdsec cscli decisions delete --ip $(IP)" 2>/dev/null || echo "    SSH failed"

auth-sync: _require-name _require-stack _require-inventory  ## Sync Authentik providers from labels (NAME=)
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e @../$(STACK_CONFIG) --tags authentik

dashboards: _require-name _require-stack _require-inventory  ## Deploy only dashboards + datasources (NAME=)
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e @../$(STACK_CONFIG) --tags dashboards

test: lint  ## Run lint + validate all examples
	@echo "==> Validating examples..."
	@for f in examples/*.yaml; do $(PYTHON) scripts/validate-stack.py "$$f"; done
	@echo "==> All tests passed"
