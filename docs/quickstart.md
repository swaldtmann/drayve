# Quickstart

Get a fully configured Docker stack running on a fresh Ubuntu server in under 10 minutes.

## Prerequisites

**On your server:**
- Ubuntu 22.04 or 24.04 (fresh install)
- Root SSH access via key (`ssh root@yourserver` must work)
- A domain pointing to the server (A-record + wildcard):
  ```
  example.com       → 203.0.113.10
  *.example.com     → 203.0.113.10
  ```

**On your local machine:**
- Python 3.8+
- Ansible 2.14+
- An SSH key pair (`~/.ssh/id_ed25519`)

## Setup

```bash
git clone https://codeberg.org/StephanWaldtmann/drayve.git
cd drayve

# Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml ansible ansible-lint
```

## Configure

Copy the minimal example and edit it:

```bash
cp examples/stack-minimal.yaml stack.yaml
```

For an existing server (any provider), set `provider.type: manual`:

```yaml
drayve_version: "0.1.0"

stack:
  name: myserver
  domain: example.com

provider:
  type: manual        # skip server creation, use existing host

auth:
  provider: basic     # none | basic | authelia

monitoring:
  profile: light      # full | light | none

secrets:
  mode: quickstart    # auto-generate secrets

backup:
  enabled: false
```

For Hetzner Cloud users, set `provider.type: hetzner` — this creates the server automatically via `hcloud` CLI.

## Add your server to the inventory

Edit `ansible/inventory/hosts.yml`:

```yaml
all:
  children:
    drayve:
      hosts:
        myserver:
          ansible_host: 203.0.113.10
          ansible_user: root
          drayve_domain: example.com
          drayve_hostname: myserver
```

## Deploy

```bash
make validate                                      # check stack.yaml
make provision NAME=myserver DOMAIN=example.com    # provision + deploy
```

This will:
1. Install packages, configure firewall (UFW), harden SSH
2. Install Docker
3. Deploy Traefik (reverse proxy + automatic TLS via Let's Encrypt)
4. Set up authentication (basic auth or Authelia)
5. Deploy monitoring stack (Grafana, Prometheus, Loki — depending on profile)
6. Deploy CrowdSec (intrusion detection)
7. Generate and distribute secrets

After provisioning, your services are available at:
- `https://example.com` — Landing page
- `https://grafana.example.com` — Grafana dashboards (if monitoring: full)
- `https://traefik.example.com` — Traefik dashboard

## Re-deploy after changes

```bash
# Edit stack.yaml, then:
make deploy-dev
```

## Tear down

```bash
make burn NAME=myserver
```

For Hetzner: deletes the server. For manual provider: cleans up local inventory and secrets only.

## What's included

| Component | Description |
|-----------|-------------|
| Traefik | Reverse proxy, automatic TLS, dashboard |
| CrowdSec | Intrusion detection + Traefik bouncer |
| Grafana | Dashboards (7 included: stack, host, containers, traefik, logs, crowdsec, backup) |
| Prometheus | Metrics collection |
| Loki + Promtail | Log aggregation |
| cAdvisor | Container metrics |
| node_exporter | Host metrics |
| Basic Auth / Authelia | Authentication layer |
| Landing Page | Service overview with status badges |

## Next steps

- See `examples/stack-full.yaml` for Authelia + LLDAP + SOPS secrets
- See `config/stack.schema.yaml` for all configuration options
- Add apps: `examples/apps/` (coming soon)
