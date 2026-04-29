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
make setup              # venv, dependencies, git hooks
source .venv/bin/activate
```

## Initialize a host

```bash
make init NAME=webshop DOMAIN=shop.example.com HOST=203.0.113.10
```

This creates:
- `deploy/webshop/stack.yaml` — config for this host
- `deploy/hosts.yaml` — inventory with connection details

Edit the stack config to match your needs:

```bash
vim deploy/webshop/stack.yaml
```

```yaml
drayve_version: "0.1.0"

stack:
  name: webshop
  domain: shop.example.com

provider:
  type: manual        # use existing server

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

## Deploy

For a fresh host, always start with `make provision`. It creates the
`drayve` user, installs Docker, and runs the full stack deploy in one go.
The plain `make deploy` target is for re-deploys against an
already-provisioned host — running it first will fail with an explicit
"run make provision first" message.

```bash
make provision NAME=webshop
```

This will:
1. Generate secrets (quickstart mode)
2. Install packages, configure firewall (UFW), harden SSH
3. Install Docker (creates the `drayve` user)
4. Deploy Traefik (reverse proxy + automatic TLS via Let's Encrypt)
5. Set up authentication (basic auth or Authelia)
6. Deploy monitoring stack (Grafana, Prometheus, Loki — depending on profile)
7. Deploy CrowdSec (intrusion detection)

After provisioning, your services are available at:
- `https://shop.example.com` — Landing page
- `https://grafana.shop.example.com` — Grafana dashboards (if monitoring: full)
- `https://traefik.shop.example.com` — Traefik dashboard
- `https://auth.shop.example.com` — Authelia login (if auth: authelia)

## First login

All protected pages redirect to the auth provider. What you need depends on your `auth.provider` setting:

### Basic auth

Username and password are in `deploy/<name>/secrets.yml`:

```yaml
auth_basic_user: admin
auth_basic_password: "..."
```

Your browser will show a standard HTTP auth prompt.

### Authelia + LLDAP

Authelia authenticates against LLDAP (a lightweight LDAP server). The default admin user is `admin` — the password is in `deploy/<name>/secrets.yml`:

```yaml
lldap_admin_password: "..."
```

Use this to log in at `https://auth.<domain>/`. After login, you're redirected to the page you originally requested.

**To create additional users:** Define them in your `stack.yaml` and re-deploy — see [Configuration](configuration.md).

## Files overview

After `make provision`, your host directory contains:

```
deploy/webshop/
├── stack.yaml              # What to deploy (auth, monitoring, domain, ...)
└── secrets.yml             # Auto-generated passwords (quickstart mode)
                            # Contains: LLDAP, Authelia, Grafana, CrowdSec secrets
```

| File | Managed by | Contains |
|------|------------|----------|
| `stack.yaml` | You | Stack configuration — what gets deployed |
| `secrets.yml` | `make provision` (quickstart) or you (SOPS) | Platform secrets (auth, monitoring, security) |

Your services live on the server in `/opt/drayve/services/<name>/` — see [Adding your own services](#adding-your-own-services).

**Important:** `deploy/` is gitignored. These files contain your infrastructure secrets. Back them up. In SOPS mode, also back up `deploy/.age-key.txt` — it's the master key.

## Managing multiple hosts

Each host gets its own directory under `deploy/`:

```bash
make init NAME=webshop    DOMAIN=shop.example.com    HOST=203.0.113.10
make init NAME=monitoring DOMAIN=mon.example.com     HOST=203.0.113.20
make init NAME=client-xyz DOMAIN=ops.client-xyz.com  HOST=198.51.100.5
```

```
deploy/
├── hosts.yaml              # all hosts in one inventory
├── webshop/
│   ├── stack.yaml          # webshop config
│   └── secrets.yml         # webshop secrets (auto-generated)
├── monitoring/
│   ├── stack.yaml
│   └── secrets.yml
└── client-xyz/
    ├── stack.yaml
    └── secrets.yml
```

Each host can have completely different configs — different auth providers, monitoring profiles, backup targets.

```bash
make list                      # show all configured hosts
make deploy NAME=webshop       # re-deploy after config changes
make status NAME=monitoring    # check server status
make logs NAME=webshop         # tail container logs
make logs NAME=webshop SVC=traefik  # tail one service
make burn NAME=client-xyz      # asks for confirmation first
```

The `deploy/` directory is gitignored — it contains your infrastructure details and secrets. **Back it up.** In SOPS mode, `deploy/.age-key.txt` is the master key for all your encrypted secrets. Lose it and you lose access to every secret across every host. See [Secrets](secrets.md) for details.

## Adding your own services

Create a directory on the server under `/opt/drayve/services/` with a `compose.yml` and optional `.env`:

```bash
ssh root@yourserver
mkdir -p /opt/drayve/services/nextcloud
vim /opt/drayve/services/nextcloud/compose.yml
```

```yaml
services:
  nextcloud:
    image: nextcloud:29
    networks:
      - drayve
    labels:
      traefik.enable: "true"
      traefik.http.routers.nextcloud.rule: "Host(`cloud.shop.example.com`)"
      drayve.landing.name: "Nextcloud"
      drayve.landing.icon: "cloud"

networks:
  drayve:
    external: true
```

Then re-deploy to pick up the new service:

```bash
make deploy NAME=webshop
```

Ansible scans `services/*/compose.yml` and updates the landing page, auth config, and Grafana dashboards automatically.

### Version-controlled alternative: `compose.override.yml`

If you want the extra service definition to live alongside your other Drayve config (useful for teams and clean re-provisioning), put it in `deploy/<name>/compose.override.yml` instead. That file is copied to the server during `make deploy` and merged into the main stack. Secrets go into `deploy/<name>/env.override`. See `examples/compose-overrides/` for ready-to-adapt snippets (Nextcloud, Forgejo).

## Re-deploy after changes

```bash
vim deploy/webshop/stack.yaml   # change config
make deploy NAME=webshop        # apply changes
```

## Tear down

```bash
make burn NAME=webshop              # asks: "BURN webshop — delete server + local files? [y/N]"
make burn NAME=webshop CONFIRM=y    # skip prompt (for scripts)
```

For Hetzner: backs up `acme.json` from the server to `/tmp/<name>-acme.json`, deletes the server + DNS records, then backs up local user files (`compose.override.yml`, `env.override`, `stack.yaml`) to `/tmp/drayve-burn-<name>/` before cleaning `deploy/<name>/`. For manual provider: cleans local files only (server untouched).

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
| Landing Page | Service overview, auto-generated from Docker labels |

## Next steps

- See `deploy/_example/stack.yaml` for the config template
- See [Configuration](configuration.md) for all options
- See [Architecture](architecture.md) for how the pieces fit together
- See [Secrets](secrets.md) for quickstart vs SOPS
