# drayve

[![Test & Lint](https://codeberg.org/StephanWaldtmann/drayve/badges/workflows/test.yml/badge.svg)](https://codeberg.org/StephanWaldtmann/drayve/actions?workflow=test.yml)

Generic Ops framework for Docker stacks. Provision, deploy, monitor, secure, backup — from a single `stack.yaml`.

One config file. One command. Full stack.

## What it does

Drayve turns a fresh Ubuntu server into a production-ready Docker host with reverse proxy, TLS, monitoring, intrusion detection, and authentication — all configured from a single YAML file.

Works on any VPS, cloud instance, or bare metal server. Hetzner Cloud users get automated provisioning; everyone else brings their own server.

## Get started

**[Quickstart Guide](docs/quickstart.md)** — from zero to running stack in under 10 minutes.

```bash
git clone https://codeberg.org/StephanWaldtmann/drayve.git
cd drayve
pip install pyyaml ansible

make init NAME=myserver DOMAIN=example.com HOST=203.0.113.10
# Edit deploy/myserver/stack.yaml to match your needs
make provision NAME=myserver
```

## stack.yaml

All configuration lives in one file. See `config/stack.schema.yaml` for the full schema.

```yaml
drayve_version: "0.1.0"

stack:
  name: myserver
  domain: example.com

provider:
  type: manual          # manual (existing server) | hetzner (auto-provision)

auth:
  provider: basic       # none | basic | authelia

monitoring:
  profile: full         # full (~800MB) | light (~50MB) | none

security:
  crowdsec: true        # intrusion detection + Traefik bouncer

secrets:
  mode: quickstart      # quickstart (auto-generate) | sops (versioned)

backup:
  enabled: true
  target: local         # local | storagebox | s3 | ssh
```

## What's included

| Component | Description |
|-----------|-------------|
| **Traefik** | Reverse proxy, automatic TLS via Let's Encrypt |
| **CrowdSec** | Intrusion detection + Traefik bouncer plugin |
| **Grafana** | 7 dashboards: stack, host, containers, traefik, logs, crowdsec, backup |
| **Prometheus** | Metrics collection |
| **Loki + Promtail** | Log aggregation |
| **Basic Auth / Authelia** | Authentication layer (with optional LLDAP backend) |
| **Landing Page** | Service overview, auto-generated from Docker labels at deploy time |

## Make targets

```
make init NAME= DOMAIN= [HOST=]   Scaffold new host in deploy/
make provision NAME=               Provision + deploy server
make deploy NAME=                  Re-deploy after config changes
make deploy-prod NAME= REF=       Deploy tagged release to prod
make burn NAME= [CONFIRM=y]        Tear down server + clean local files (backs up first)
make status NAME=                  Show server status
make logs NAME= [SVC=]             Tail container logs (optionally filter by service)
make list                          List all configured hosts
make validate NAME=                Validate stack.yaml for a host
make lint                          Lint playbooks + roles
make secrets-init                  Scaffold AGE key + SOPS config
make secrets-template NAME=        Scaffold SOPS secrets for a host
make backup NAME=                  Deploy/update backup config
make test                          Lint + validate all examples
make test-role NAME=common         Test a single role (Molecule + Hetzner)
make test-integration              Full stack test (Molecule + Hetzner)
```

## Writing a consumer repo

A consumer (host repo) keeps its own `hosts.yaml`, `stack.yaml`, secrets — and a small `Makefile` that vendors drayve and includes the shared targets:

```make
SHELL := /bin/bash
.DEFAULT_GOAL := help

NAME        := my-host           # required: inventory group + snapshot host
DRAYVE_REF  ?= v0.4.0            # tag/branch in drayve to vendor
DRAYVE_REPO ?= https://codeberg.org/StephanWaldtmann/drayve.git
DRAYVE_DIR  := vendor/drayve

# Optional: override paths/flags before the include
# SECRETS_FILE := deploy/my-host/secrets.yml
# ENV_FILE     := deploy/my-host/env.override
# AGE_KEY_LINK := 1                # opt-in: symlink deploy/.age-key.txt

vendor:  ## Clone or update vendored drayve at DRAYVE_REF
	@if [ ! -d $(DRAYVE_DIR)/.git ]; then \
		git clone $(DRAYVE_REPO) $(DRAYVE_DIR); \
	fi
	@git -C $(DRAYVE_DIR) fetch --tags --quiet
	@git -C $(DRAYVE_DIR) checkout --quiet $(DRAYVE_REF)
	@ln -sfn $(DRAYVE_DIR)/ansible/inventory/group_vars group_vars

.PHONY: vendor

-include $(DRAYVE_DIR)/ansible/consumer.mk
```

After `make vendor`, the include picks up `ansible/consumer.mk` from the vendored copy and provides: `help`, `pre-snapshot`, `deploy-prod`, `deploy-check`, `secrets-edit`, `secrets-edit-env`, `ping`, `age-key-link`. Run `make help` to see the full list.

Variant points (set before the include):

| Variable | Default | Purpose |
|---|---|---|
| `NAME` | *required* | inventory group + snapshot host |
| `SECRETS_FILE` | `secrets.yml` | sops-managed secrets |
| `ENV_FILE` | `env.override` | sops-managed env file (dotenv) |
| `SOPS_AGE_KEY_FILE` | `~/.config/sops/age/drayve-$(NAME).txt` | private age key |
| `SNAPSHOT_HOST` | `$(NAME)` | hcloud snapshot target |
| `AGE_KEY_LINK` | empty | `1` adds `age-key-link` to deploy prereqs |
| `PROD_SNAPSHOT` | `tools/prod-snapshot` path | pre-deploy snapshot wrapper |

## Documentation

- **[Quickstart](docs/quickstart.md)** — from zero to running stack in under 10 minutes
- **[Configuration](docs/configuration.md)** — full `stack.yaml` reference, all options
- **[Architecture](docs/architecture.md)** — how the pieces fit together, directory layout, provisioning flow
- **[Secrets](docs/secrets.md)** — quickstart vs SOPS, setup, editing, secret reference
- **[Single-App Host](docs/single-app-host.md)** — one-app-per-host layout (no landing, vendor-submodule option)

## Adding your own services

Create a directory under `services/` on the server with its own `compose.yml` and `.env`:

```
/opt/drayve/services/nextcloud/
├── compose.yml
└── .env
```

Your services connect to the frame via Docker labels — the same pattern Traefik already uses:

```yaml
services:
  nextcloud:
    image: nextcloud:29
    networks:
      - drayve
    labels:
      # Routing (Traefik standard)
      traefik.enable: "true"
      traefik.http.routers.nextcloud.rule: "Host(`cloud.example.com`)"
      traefik.http.routers.nextcloud.middlewares: "authentik@file"

      # Landing page (optional)
      drayve.landing.name: "Nextcloud"
      drayve.landing.icon: "cloud"
      drayve.landing.category: "Collaboration"

      # Custom Grafana dashboard (optional)
      drayve.monitoring.dashboard: "nextcloud"

networks:
  drayve:
    external: true
```

Then deploy:

```bash
make deploy NAME=myserver
```

Ansible scans all `services/*/compose.yml` at deploy time and updates the landing page, auth config, and dashboard provisioning automatically. No central registry needed — labels are the contract.

## Examples

- `examples/stack-minimal.yaml` — Basic auth, light monitoring, quickstart secrets
- `examples/stack-full.yaml` — Authelia + LLDAP, full monitoring, SOPS secrets

## Testing

Role and integration tests use [Molecule](https://molecule.readthedocs.io/) with the Hetzner Cloud driver — they create real servers, run the roles, verify with Testinfra, and tear down.

> **Note:** After test failures, debug **before** running `molecule destroy` — the ephemeral SSH key in `~/.ansible/tmp/molecule.*/ssh_key` is deleted on destroy, so you can't SSH into the test instance afterwards.

### Molecule Scenarios

| Scenario | Auth | Monitoring | Extras |
|----------|------|------------|--------|
| `basic` | basic | full | Default scenario |
| `integration` | basic | full | Full stack test |
| `light` | basic | light | Minimal monitoring |
| `auth-none` | none | full | No authentication |
| `authelia` | authelia | full | Authelia + LLDAP |
| `authentik` | authentik | full | Authentik OIDC |
| `backup` | basic | full | Borgmatic backup |
| `sops` | basic | full | SOPS secrets |

```bash
# One-time setup: copy the example and add your Hetzner Cloud API token
cp .env.test.example .env.test
# Edit .env.test — add your HCLOUD_TOKEN
# ⚠ .env.test is required — without it, Molecule tests will fail.

# Test a single role (uses uv if available)
make test-role NAME=common
make test-role NAME=docker

# Full stack integration test
make test-integration

# Lint + validate only (no server needed)
make test
```

The Makefile loads `.env.test` automatically. Alternatively, `export HCLOUD_TOKEN=...` in your shell.

## Requirements

- Python 3.8+, Ansible 2.14+
- Target: **Ubuntu 22.04 / 24.04 only.** Debian and RHEL-family hosts are
  not yet supported — the docker role hardcodes the Ubuntu apt repo
  (`download.docker.com/linux/ubuntu`). Distro-agnostic provisioning is
  tracked but not implemented; pick Ubuntu when you spin up a fresh host.
- For Hetzner auto-provision: `hcloud` CLI
- For Molecule tests: Hetzner Cloud API token (see [Testing](#testing))

## Built with

This project is developed with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Anthropic Claude Opus 4.6).

## License

Apache 2.0
