# consumer.mk — Drayve-Konsumenten-Makefile-Snippet (AFKI-W-126)
#
# Konsumenten-Repos haengen sich per `include` ein und setzen NAME +
# bei Bedarf SECRETS_FILE / ENV_FILE / AGE_KEY_LINK.
#
# Bootstrap im Konsumenten-Makefile (vor dem Include):
#
#   NAME := prod-genua
#   DRAYVE_REF ?= v0.3.1
#   DRAYVE_DIR := vendor/drayve
#
#   vendor:  ## Clone/update drayve at DRAYVE_REF
#       @if [ ! -d $(DRAYVE_DIR)/.git ]; then \
#           git clone https://codeberg.org/StephanWaldtmann/drayve.git $(DRAYVE_DIR); \
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
# Variantenpunkte (Default-Werte):
#   NAME              — required, Inventory-Group + Snapshot-Host
#   SECRETS_FILE      — secrets.yml
#   ENV_FILE          — env.override
#   SOPS_AGE_KEY_FILE — $(HOME)/.config/sops/age/drayve-$(NAME).txt
#   SNAPSHOT_HOST     — $(NAME)
#   AGE_KEY_LINK      — leer = aus, "1" = symlink deploy/.age-key.txt anlegen
#                       (in deploy-prod/-check als Prerequisite)
#   PROD_SNAPSHOT     — Pfad zum prod-snapshot-Wrapper
#   DRAYVE_REF        — git-ref fuer vendor (kommt aus Konsument)

ifndef NAME
$(error consumer.mk: NAME ist nicht gesetzt. Setze NAME := <inventory-group> vor dem include.)
endif

SECRETS_FILE      ?= secrets.yml
ENV_FILE          ?= env.override
SOPS_AGE_KEY_FILE ?= $(HOME)/.config/sops/age/drayve-$(NAME).txt
SNAPSHOT_HOST     ?= $(NAME)
PROD_SNAPSHOT     ?= /Users/sw/claudes-welt/tools/prod-snapshot
SKIP_SNAPSHOT     ?=

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
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

age-key-link:  ## Create deploy/.age-key.txt symlink (drayve secrets role expects it)
	@ln -sf $(SOPS_AGE_KEY_FILE) deploy/.age-key.txt
	@echo "deploy/.age-key.txt -> $(SOPS_AGE_KEY_FILE)"

pre-snapshot:  ## Pre-Deploy hcloud-Snapshot (AFKI-W-125, SKIP_SNAPSHOT=1 ueberspringt)
	@if [ -n "$(SKIP_SNAPSHOT)" ]; then \
		echo "pre-snapshot: SKIP_SNAPSHOT gesetzt — uebersprungen (Notfall-Override)"; \
	else \
		$(PROD_SNAPSHOT) $(SNAPSHOT_HOST) --reason "drayve-deploy-$(DRAYVE_REF)"; \
	fi

deploy-prod: $(DEPLOY_DEPS)  ## Deploy DRAYVE_REF to host (CONFIRM=y to skip prompt, SKIP_SNAPSHOT=1 ueberspringt Snapshot)
	@[ "$(CONFIRM)" = "y" ] || { printf "Deploy $(DRAYVE_REF) to $(NAME) PRODUCTION? [y/N] "; read ans; [ "$$ans" = "y" ] || exit 1; }
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml

deploy-check: $(CHECK_DEPS)  ## Deep dry-run: deploy.yml --check (KNOWN-BROKEN in v0.4.x, prefer deploy-check-fast — W-131)
	$(ANSIBLE) playbooks/deploy.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml --check

deploy-check-fast: $(CHECK_DEPS)  ## Fast static validation: stack.yaml + secrets, no host contact (W-131)
	$(ANSIBLE) playbooks/validate.yml -l $(NAME) -e deploy_ref=$(DRAYVE_REF) -e ops_secrets_root=$(CURDIR) -e @$(CURDIR)/stack.yaml

secrets-edit:  ## Edit SECRETS_FILE via sops
	sops $(SECRETS_FILE)

secrets-edit-env:  ## Edit ENV_FILE via sops (dotenv-Typ explizit)
	sops --input-type dotenv --output-type dotenv $(ENV_FILE)

ping:  ## Ansible ping target host
	@cd $(DRAYVE_DIR)/ansible && ansible -i $(CURDIR)/hosts.yaml -m ping $(NAME)

.PHONY: help age-key-link pre-snapshot deploy-prod deploy-check deploy-check-fast secrets-edit secrets-edit-env ping
