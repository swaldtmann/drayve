# consumer.mk — Drayve consumer Makefile snippet
#
# Consumer repos hook in via `include` and set NAME +
# SECRETS_FILE / ENV_FILE / AGE_KEY_LINK where needed.
#
# Bootstrap in the consumer Makefile (before the include):
#
#   NAME := myhost
#   DRAYVE_REF ?= v0.3.1
#   DRAYVE_DIR := vendor/drayve
#
#   vendor:  ## Clone/update drayve at DRAYVE_REF
#       @if [ ! -d $(DRAYVE_DIR)/.git ]; then \
#           git clone https://github.com/swaldtmann/drayve.git $(DRAYVE_DIR); \
#       fi
#       @git -C $(DRAYVE_DIR) fetch --tags --quiet
#       @git -C $(DRAYVE_DIR) checkout --quiet $(DRAYVE_REF)
#       @if git -C $(DRAYVE_DIR) show-ref --verify --quiet refs/remotes/origin/$(DRAYVE_REF); then \
#           git -C $(DRAYVE_DIR) reset --hard --quiet origin/$(DRAYVE_REF); \
#       fi
#       @ln -sfn $(DRAYVE_DIR)/ansible/inventory/group_vars group_vars
#
#   -include $(DRAYVE_DIR)/ansible/consumer.mk
#
# Variation points (default values):
#   NAME              — required, inventory group + snapshot host
#   SECRETS_FILE      — secrets.yml
#   ENV_FILE          — env.override
#   SOPS_AGE_KEY_FILE — $(HOME)/.config/sops/age/drayve-$(NAME).txt
#   SNAPSHOT_HOST     — $(NAME)
#   AGE_KEY_LINK      — empty = off, "1" = create symlink deploy/.age-key.txt
#                       (as a prerequisite of deploy-prod/-check/backup-deploy)
#   PROD_SNAPSHOT     — optional command, called as
#                       `<cmd> <host> --reason <text>` (may contain its own
#                       arguments); empty = no snapshot
#   DRAYVE_REF        — git ref for vendor (comes from the consumer)
#   EXTRA_ANSIBLE_VARS — empty = off, otherwise appended to deploy-prod/-check/-check-fast/
#                       backup-deploy, applies identically to all targets.
#                       Set file-based, not inline JSON —
#                       e.g. -e @override.yml (inline "-e '{"a":{"b":"c"}}'"
#                       breaks on shell quoting as soon as Make hands the
#                       recipe line to /bin/sh)

ifndef NAME
$(error consumer.mk: NAME is not set. Set NAME := <inventory-group> before the include.)
endif

SECRETS_FILE      ?= secrets.yml
ENV_FILE          ?= env.override
SOPS_AGE_KEY_FILE ?= $(HOME)/.config/sops/age/drayve-$(NAME).txt
SNAPSHOT_HOST     ?= $(NAME)
PROD_SNAPSHOT     ?=
SKIP_SNAPSHOT     ?=
EXTRA_ANSIBLE_VARS ?=

export SOPS_AGE_KEY_FILE

ANSIBLE := cd $(DRAYVE_DIR)/ansible && ansible-playbook -i $(CURDIR)/hosts.yaml

# Conditional Prerequisites: opt-in age-key-link
ifeq ($(AGE_KEY_LINK),1)
DEPLOY_DEPS := vendor age-key-link pre-snapshot
CHECK_DEPS  := vendor age-key-link
else
DEPLOY_DEPS := vendor pre-snapshot
CHECK_DEPS  := vendor
endif

help:  ## Show targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

age-key-link:  ## Create deploy/.age-key.txt symlink (drayve secrets role expects it)
	@ln -sf $(SOPS_AGE_KEY_FILE) deploy/.age-key.txt
	@echo "deploy/.age-key.txt -> $(SOPS_AGE_KEY_FILE)"

pre-snapshot:  ## Pre-Deploy snapshot via PROD_SNAPSHOT (SKIP_SNAPSHOT=1 skips it)
	@if [ -n "$(SKIP_SNAPSHOT)" ]; then \
		echo "pre-snapshot: SKIP_SNAPSHOT set — skipped (emergency override)"; \
	elif [ -z "$(PROD_SNAPSHOT)" ]; then \
		echo "pre-snapshot: PROD_SNAPSHOT not set — no pre-deploy snapshot taken"; \
	else \
		$(PROD_SNAPSHOT) $(SNAPSHOT_HOST) --reason "drayve-deploy-$(DRAYVE_REF)"; \
	fi

deploy-prod: $(DEPLOY_DEPS)  ## Deploy DRAYVE_REF to host (CONFIRM=y to skip prompt, SKIP_SNAPSHOT=1 skips the snapshot, EXTRA_ANSIBLE_VARS for overrides)
	@[ "$(CONFIRM)" = "y" ] || { printf "Deploy $(DRAYVE_REF) to $(NAME) PRODUCTION? [y/N] "; read ans; [ "$$ans" = "y" ] || exit 1; }
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml $(EXTRA_ANSIBLE_VARS)

backup-deploy: $(CHECK_DEPS)  ## Apply backup.yml to host: installs/updates kedge at the pinned version (CONFIRM=y skips prompt, EXTRA_ANSIBLE_VARS="--check --diff" for a dry run)
	@[ "$(CONFIRM)" = "y" ] || { printf "Apply backup config ($(DRAYVE_REF)) to $(NAME) PRODUCTION? [y/N] "; read ans; [ "$$ans" = "y" ] || exit 1; }
	$(ANSIBLE) playbooks/backup.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml $(EXTRA_ANSIBLE_VARS)

deploy-check: $(CHECK_DEPS)  ## Deep dry-run: deploy.yml --check (KNOWN-BROKEN in v0.4.x, prefer deploy-check-fast)
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml --check $(EXTRA_ANSIBLE_VARS)

deploy-check-fast: $(CHECK_DEPS)  ## Fast static validation: stack.yaml + secrets, no host contact (EXTRA_ANSIBLE_VARS for overrides)
	$(ANSIBLE) playbooks/validate.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml $(EXTRA_ANSIBLE_VARS)

secrets-edit:  ## Edit SECRETS_FILE via sops
	sops $(SECRETS_FILE)

secrets-edit-env:  ## Edit ENV_FILE via sops (dotenv type explicit)
	sops --input-type dotenv --output-type dotenv $(ENV_FILE)

ping:  ## Ansible ping target host
	@cd $(DRAYVE_DIR)/ansible && ansible -i $(CURDIR)/hosts.yaml -m ping $(NAME)

.PHONY: help age-key-link pre-snapshot deploy-prod backup-deploy deploy-check deploy-check-fast secrets-edit secrets-edit-env ping
