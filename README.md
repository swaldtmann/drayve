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
make burn NAME=                    Tear down server + clean local files
make status NAME=                  Show server status
make list                          List all configured hosts
make validate NAME=                Validate stack.yaml for a host
make lint                          Lint playbooks + roles
make secrets-init                  Scaffold AGE key + SOPS config
make secrets-template NAME=        Scaffold SOPS secrets for a host
make backup NAME=                  Deploy/update backup config
```

## Documentation

- **[Quickstart](docs/quickstart.md)** — from zero to running stack in under 10 minutes
- **[Configuration](docs/configuration.md)** — full `stack.yaml` reference, all options
- **[Architecture](docs/architecture.md)** — how the pieces fit together, directory layout, provisioning flow
- **[Secrets](docs/secrets.md)** — quickstart vs SOPS, setup, editing, secret reference

## Examples

- `examples/stack-minimal.yaml` — Basic auth, light monitoring, quickstart secrets
- `examples/stack-full.yaml` — Authelia + LLDAP, full monitoring, SOPS secrets

## Requirements

- Python 3.8+, Ansible 2.14+
- Target: Ubuntu 22.04 / 24.04
- For Hetzner auto-provision: `hcloud` CLI

## License

Apache 2.0
