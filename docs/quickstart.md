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

```bash
make provision NAME=webshop
```

This will:
1. Generate secrets (quickstart mode)
2. Install packages, configure firewall (UFW), harden SSH
3. Install Docker
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

**To create additional users:** Define them in your `stack.yaml` and re-deploy — see [Configuration](configuration.md). For manual access to the LLDAP admin panel, use an SSH tunnel: `ssh -L 17170:localhost:17170 root@<server-ip>`, then open `http://localhost:17170` in your browser. Port 17170 is not exposed on the host for security reasons.

### Nextcloud (if using compose.override)

Nextcloud has its own user database, separate from Authelia/LLDAP. The admin credentials are in `deploy/<name>/env.override`:

```
NC_ADMIN_USER=admin
NC_ADMIN_PASSWORD=...
```

Open `https://cloud.<domain>/` and log in directly — Nextcloud is not behind the Authelia middleware.

## Files overview

After `make provision`, your host directory contains:

```
deploy/webshop/
├── stack.yaml              # What to deploy (auth, monitoring, domain, ...)
├── secrets.yml             # Auto-generated passwords (quickstart mode)
│                           # Contains: LLDAP, Authelia, Grafana, CrowdSec secrets
├── compose.override.yml    # Your apps (optional — Nextcloud example in deploy/_example/)
└── env.override            # Passwords for your apps (optional — referenced by override)
```

| File | Managed by | Contains |
|------|------------|----------|
| `stack.yaml` | You | Stack configuration — what gets deployed |
| `secrets.yml` | `make provision` (quickstart) or you (SOPS) | Platform secrets (auth, monitoring, security) |
| `compose.override.yml` | You | Your services — Docker Compose merged with main stack |
| `env.override` | You | Your service secrets — appended to `.env` on server |

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

Put a `compose.override.yml` in your host directory:

```bash
cp deploy/_example/compose.override.yml deploy/webshop/compose.override.yml
vim deploy/webshop/compose.override.yml
```

Docker Compose merges it with the main stack automatically. Your services get TLS via Traefik — just add the labels. See the example for a complete Nextcloud setup.

### Secrets for your services

If your services need passwords or API keys, create an `env.override` file:

```bash
cp deploy/_example/env.override deploy/webshop/env.override
vim deploy/webshop/env.override
```

```
NC_DOMAIN=cloud.shop.example.com
NC_ADMIN_USER=admin
NC_ADMIN_PASSWORD=change-me-now
NC_DB_PASSWORD=change-me-now
NC_DB_ROOT_PASSWORD=change-me-now
NC_REDIS_PASSWORD=change-me-now
```

This file is appended to `.env` on the server during deploy. Your `compose.override.yml` can reference these variables with `${NC_DB_PASSWORD}` etc.

### Show apps on the landing page

Add your services to `stack.yaml`:

```yaml
apps:
  - name: Nextcloud
    url: https://cloud.shop.example.com
    icon: "&#x2601;"
    description: Files, calendar, contacts
```

Then deploy:

```bash
make deploy NAME=webshop
```

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
| Landing Page | Service overview with status badges |

## Next steps

- See `deploy/_example/stack.yaml` for the config template
- See `deploy/_example/compose.override.yml` for adding your own services (Nextcloud example)
- See [Configuration](configuration.md) for all options
- See [Architecture](architecture.md) for how the pieces fit together
- See [Secrets](secrets.md) for quickstart vs SOPS
