# Architecture

## Overview

Drayve manages a single Docker Compose stack on a single server. All services share one Traefik reverse proxy and one Docker network. You can manage any number of servers, each with its own config.

```
                       +-----------+
                       |  Traefik  |---[ CrowdSec ]
                       +-----+-----+
                             |
              +--------------+--------------+
              |     Docker Network (proxy)  |
              |                             |
  +---------+ +--------+ +-----------+      |
  | Grafana | |  Loki  | | Prometheus|      |
  +---------+ +--------+ +-----------+      |
                                            |
  +----------+ +----------+ +----------+    |
  | Promtail | | cAdvisor | | node_exp |    |
  +----------+ +----------+ +----------+    |
                                            |
  +----------+ +-------+                    |
  | Authelia | | LLDAP |  (if enabled)      |
  +----------+ +-------+                    |
              +-----------------------------+

  +---------------+
  | Landing Page  |
  +---------------+
```

## Framework vs. deployment

Drayve separates the **framework** (tracked in git) from **your infrastructure** (gitignored, lives in `deploy/`).

```
drayve/                          <-- git clone
|
|-- ansible/                     <-- FRAMEWORK (tracked)
|   |-- playbooks/
|   |   |-- provision.yml
|   |   |-- deploy.yml
|   |   +-- burn.yml
|   |-- roles/
|   |   |-- common/             Packages, firewall, SSH, swap, users
|   |   |-- docker/             Docker CE install
|   |   |-- secrets/            Secret distribution
|   |   |-- traefik/            Reverse proxy + TLS + CrowdSec
|   |   |-- auth/               Auth dispatcher (basic/authelia/none)
|   |   |-- authelia/           Authelia SSO
|   |   |-- lldap/              LLDAP user backend
|   |   |-- monitoring/         Grafana, Prometheus, Loki, etc.
|   |   |-- deploy/             Docker Compose up
|   |   +-- backup/             Backup config
|   +-- inventory/
|       +-- group_vars/          Shared defaults (all hosts)
|
|-- deploy/                      <-- YOUR HOSTS (gitignored)
|   |-- hosts.yaml               All hosts, connection info
|   |-- _example/                Template (tracked)
|   |-- webshop/
|   |   |-- stack.yaml           Config for this host
|   |   |-- secrets.yml          Secrets for this host
|   |   |-- compose.override.yml Your services (optional)
|   |   +-- env.override         Secrets for your services (optional)
|   +-- monitoring/
|       |-- stack.yaml
|       +-- secrets.yml
|
|-- config/
|   +-- stack.schema.yaml        Schema definition
|-- scripts/
|   |-- init-host.sh             Scaffold new host
|   |-- secrets-generate.sh
|   |-- secrets-init.sh
|   |-- secrets-template.sh
|   +-- validate-stack.py
|-- examples/
|   |-- stack-minimal.yaml
|   +-- stack-full.yaml
|-- docs/
|-- Makefile
+-- README.md
```

## Directory layout on the server

```
/opt/drayve/
|-- deploy/stack/               Docker Compose stack
|   |-- docker-compose.yml
|   |-- docker-compose.override.yml  (if compose.override.yml exists)
|   |-- .env                    (includes env.override contents)
|   |-- traefik/
|   |   |-- config/
|   |   |   |-- traefik.yml
|   |   |   +-- dynamic/
|   |   +-- certs/
|   |       +-- acme.json
|   |-- grafana/
|   |   +-- dashboards/
|   |-- prometheus/
|   |-- loki/
|   |-- promtail/
|   |-- crowdsec/
|   |-- authelia/               (if auth.provider: authelia)
|   |-- lldap/                  (if auth.lldap: true)
|   +-- landing/
|-- config/
+-- backups/                    (if backup.target: local)
```

## Provisioning flow

```
make init NAME=webshop DOMAIN=shop.example.com HOST=203.0.113.10
make provision NAME=webshop
       |
       v
+- Phase 1: Create server (hetzner only) -+
|  hcloud server create                    |
|  Wait for SSH                            |
|  Accept host key                         |
|  Add to in-memory inventory              |
+------------------------------------------+
       |
       v
+- Phase 2: Provision host ---------------+
|  secrets    -> distribute secrets        |
|  common     -> packages, firewall, SSH,  |
|                swap, users, directories  |
|  docker     -> install Docker CE         |
|  traefik    -> reverse proxy + TLS +     |
|                CrowdSec bouncer          |
|  auth       -> basic auth or Authelia    |
|  monitoring -> Grafana, Prometheus, Loki |
|  deploy     -> docker compose up         |
+------------------------------------------+
```

For `provider.type: manual`, Phase 1 is skipped entirely.

## Multi-host management

Each host is independent — its own `stack.yaml`, its own `secrets.yml`, its own config. The shared framework (`ansible/roles/`) is the same for all hosts.

```bash
make init NAME=webshop    DOMAIN=shop.example.com    HOST=203.0.113.10
make init NAME=monitoring DOMAIN=mon.example.com     HOST=203.0.113.20
make init NAME=client-xyz DOMAIN=ops.client-xyz.com  HOST=198.51.100.5

make provision NAME=webshop
make provision NAME=monitoring
make provision NAME=client-xyz

make list                         # show all hosts
make deploy NAME=webshop          # re-deploy one host
make status NAME=monitoring       # check status
make burn NAME=client-xyz         # asks for confirmation
```

## Logging

The stack uses two log collection strategies by design:

- **Promtail** uses Docker service discovery to collect all container logs automatically. No per-service config needed.
- **CrowdSec** reads Traefik access logs from a shared volume (`traefik_logs`). It needs structured file-based logs for its parser engine — Docker-SD is not an option here.

Both strategies coexist. When adding new services, container logs are picked up by Promtail automatically. CrowdSec only watches Traefik.

## Monitoring profiles

| Profile | Containers | RAM | Use case |
|---------|-----------|-----|----------|
| `full` | Grafana, Prometheus, Loki, Promtail, cAdvisor, node_exporter | ~800MB | Self-contained monitoring |
| `light` | node_exporter, Promtail | ~50MB | Push metrics to remote Grafana |
| `none` | -- | 0 | No monitoring |

## Authentication

| Provider | Components | Description |
|----------|-----------|-------------|
| `none` | -- | No authentication, all services publicly accessible |
| `basic` | Traefik middleware | HTTP basic auth via Traefik, credentials in secrets |
| `authelia` | Authelia + optional LLDAP | Full SSO portal with session management, 2FA support |

## Secrets

| Mode | Encryption | Setup | Use case |
|------|-----------|-------|----------|
| `quickstart` | None (plaintext) | Zero config | Development, testing, single-admin setups |
| `sops` | AGE encryption | `make secrets-init` | Production, multi-admin, version-controlled secrets |
