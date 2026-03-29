# Architecture

## Overview

Drayve manages a single Docker Compose stack on a single server. All services share one Traefik reverse proxy and one Docker network.

```
┌─────────────────────────────────────────────────┐
│  Server                                         │
│                                                 │
│  ┌─────────┐  ┌────��─────┐  ┌───────────────┐  │
│  │ Traefik │──│ CrowdSec │  │  Landing Page │  │
│  └────┬────┘  └─────────��┘  └───────────────┘  │
│       │                                         │
│  ┌────┴────────────────────────────────────┐    │
│  │  Docker Network (proxy)                 │    │
│  │                                         │    │
│  │  ┌─────────┐ ┌────────┐ ┌───────────┐  │    │
│  │  │ Grafana │ │  Loki  │ │Prometheus │  │    │
│  │  └─────────┘ └──────���─┘ └───────────┘  │    │
│  │                                         │    │
│  │  ┌──────────┐ ┌──────��───┐ ┌────────┐  │    │
│  │  │ Promtail │ │ cAdvisor │ │node_exp│  ���    │
│  │  └──────────┘ └──���───────┘ └────���───┘  │    │
│  │                                         │    │
│  │  ┌───���──────┐ ┌───────┐                │    │
│  │  │ Authelia │ │ LLDAP │  (if enabled)  │    │
│  │  └──────────┘ └───────┘                │    │
│  └─────���───────────────────────────────────┘    │
└──────────────────────────────���──────────────────┘
```

## Directory layout on the server

```
/opt/drayve/
├── deploy/stack/           # Docker Compose stack
│   ├── docker-compose.yml
│   ├── .env
│   ├── traefik/
│   │   ├── config/
│   │   │   ├── traefik.yml
│   │   │   └── dynamic/
│   │   └── certs/
│   │       └── acme.json
│   ├── grafana/
│   │   └── dashboards/
│   ├── prometheus/
│   ├��─ loki/
│   ��── promtail/
│   ├── crowdsec/
│   ├── authelia/           (if auth.provider: authelia)
│   ├── lldap/              (if auth.lldap: true)
│   └── landing/
├── config/                 # Host-level config
└── backups/                (if backup.target: local)
```

## Repository layout

```
drayve/
├── ansible/
│   ├── playbooks/
│   │   ├── provision.yml   # Full provisioning pipeline
│   │   ├── deploy.yml      # Re-deploy stack
│   │   └── burn.yml        # Tear down server
│   ├── roles/
│   │   ├── common/         # Packages, firewall, SSH, swap, users
│   │   ├── docker/         # Docker CE install
│   │   ├── secrets/        # Secret distribution
│   │   ├── traefik/        # Reverse proxy + TLS + CrowdSec
│   │   ├── auth/           # Auth dispatcher (basic/authelia/none)
│   │   ├── authelia/       # Authelia SSO
│   │   ├── lldap/          # LLDAP user backend
│   │   ├── monitoring/     # Grafana, Prometheus, Loki, etc.
│   │   ├── deploy/         # Docker Compose up
│   │   └── backup/         # Backup config
│   ├── inventory/
│   │   ├── hosts.yml       # Static inventory
│   │   ├── group_vars/     # Shared defaults
│   │   └── host_vars/      # Per-host overrides
│   └── secrets/            # Secrets (gitignored)
├── config/
│   └── stack.schema.yaml   # Schema definition
├── scripts/
│   ├── secrets-generate.sh
│   ├── secrets-init.sh
│   ├── secrets-template.sh
│   ���── validate-stack.py
├── examples/
│   ├── stack-minimal.yaml
│   ├── stack-full.yaml
│   └���─ apps/               # App templates (coming soon)
├── docs/
├── stack.yaml              # Your config (gitignored)
├── Makefile
└── README.md
```

## Provisioning flow

```
make provision NAME=myserver DOMAIN=example.com
        │
        ▼
┌─ Phase 1: Create server (hetzner only) ─┐
│  hcloud server create                    │
│  Wait for SSH                            │
│  Accept host key                         │
│  Add to in-memory inventory              │
└──────────────────────────────────────────┘
        │
        ▼
┌─ Phase 2: Provision host ────────────────┐
│  secrets   → distribute secrets          │
│  common    → packages, firewall, SSH,    │
│              swap, users, directories    ��
│  docker    → install Docker CE           │
│  traefik   → reverse proxy + TLS +       │
│              CrowdSec bouncer            │
│  auth      → basic auth or Authelia      │
│  monitoring→ Grafana, Prometheus, Loki   │
│  deploy    → docker compose up           │
└───────────���──────────────────────────────┘
```

For `provider.type: manual`, Phase 1 is skipped entirely.

## Monitoring profiles

| Profile | Containers | RAM | Use case |
|---------|-----------|-----|----------|
| `full` | Grafana, Prometheus, Loki, Promtail, cAdvisor, node_exporter | ~800MB | Self-contained monitoring |
| `light` | node_exporter, Promtail | ~50MB | Push metrics to remote Grafana |
| `none` | — | 0 | No monitoring |

## Authentication

| Provider | Components | Description |
|----------|-----------|-------------|
| `none` | — | No authentication, all services publicly accessible |
| `basic` | Traefik middleware | HTTP basic auth via Traefik, credentials in secrets |
| `authelia` | Authelia + optional LLDAP | Full SSO portal with session management, 2FA support |

## Secrets

| Mode | Encryption | Setup | Use case |
|------|-----------|-------|----------|
| `quickstart` | None (plaintext) | Zero config | Development, testing, single-admin setups |
| `sops` | AGE encryption | `make secrets-init` | Production, multi-admin, version-controlled secrets |
