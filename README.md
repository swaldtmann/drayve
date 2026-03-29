# drayve

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
| **Landing Page** | Service overview with live status badges |

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

## Documentation

- **[Quickstart](docs/quickstart.md)** — from zero to running stack in under 10 minutes
- **[Configuration](docs/configuration.md)** — full `stack.yaml` reference, all options
- **[Architecture](docs/architecture.md)** — how the pieces fit together, directory layout, provisioning flow
- **[Secrets](docs/secrets.md)** — quickstart vs SOPS, setup, editing, secret reference

## Adding your own services

Put a `compose.override.yml` in your host's deploy directory:

```bash
cp deploy/_example/compose.override.yml deploy/myserver/compose.override.yml
# Edit to add your services (Nextcloud example included)
```

For service secrets (passwords, API keys), create an `env.override`:

```bash
cp deploy/_example/env.override deploy/myserver/env.override
# Add your secrets (NC_DB_PASSWORD=..., etc.)
```

To show your apps on the landing page, add them to `stack.yaml`:

```yaml
apps:
  - name: Nextcloud
    url: https://cloud.example.com
    icon: "&#x2601;"
    description: Files, calendar, contacts
```

Then deploy:

```bash
make deploy NAME=myserver
```

Docker Compose merges the override with the platform stack automatically. Your services get TLS via Traefik — just add the labels.

## Examples

- `examples/stack-minimal.yaml` — Basic auth, light monitoring, quickstart secrets
- `examples/stack-full.yaml` — Authelia + LLDAP, full monitoring, SOPS secrets
- `deploy/_example/compose.override.yml` — Nextcloud with MariaDB + Redis
- `deploy/_example/env.override` — Secret template for user services

## Testing

Role and integration tests use [Molecule](https://molecule.readthedocs.io/) with the Hetzner Cloud driver — they create real servers, run the roles, verify with Testinfra, and tear down.

```bash
# One-time: copy the example and add your Hetzner Cloud API token
cp .env.test.example .env.test
# Edit .env.test — add your HCLOUD_TOKEN

# Test a single role
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
- Target: Ubuntu 22.04 / 24.04
- For Hetzner auto-provision: `hcloud` CLI
- For Molecule tests: Hetzner Cloud API token (see [Testing](#testing))

## Built with

This project is developed with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Anthropic Claude Opus 4.6).

## License

Apache 2.0
