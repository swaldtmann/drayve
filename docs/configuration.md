# Configuration Reference

All Drayve configuration lives in a single `stack.yaml` at the repository root. The full schema is defined in `config/stack.schema.yaml`.

## Minimal example

```yaml
drayve_version: "0.1.0"
stack:
  name: myserver
  domain: example.com
```

Everything else has sensible defaults. This gives you: basic auth, full monitoring, CrowdSec, quickstart secrets, no backup.

## Sections

### `stack` (required)

| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Server name. Used for inventory, hcloud, DNS. Pattern: `^[a-z][a-z0-9-]*$` |
| `domain` | string | Primary domain. Services are exposed as subdomains (`grafana.<domain>`, `traefik.<domain>`, etc.) |
| `acme_email` | string | Email for Let's Encrypt certificate notifications. Required for TLS. |

### `provider`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `type` | string | `hetzner` | `hetzner` — auto-provision via hcloud CLI. `manual` — use an existing server |
| `server_type` | string | `cx23` | Hetzner server type (hetzner only) |
| `location` | string | `fsn1` | Hetzner datacenter (hetzner only) |
| `hcloud_ssh_key` | string | — | Name of the SSH key in hcloud. **Required** for `type: hetzner` — provision fails without it |
| `hcloud_dns_context` | string | — | hcloud CLI context for DNS API. Used by `make burn` for DNS cleanup. If empty, DNS records must be removed manually |

### `auth`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `basic` | `none` — no auth. `basic` — HTTP basic auth. `authelia` — full SSO with web portal. `authentik` — enterprise SSO (planned) |
| `lldap` | bool | `false` | Deploy LLDAP as user backend (authelia only) |
| `lldap_base_dn` | string | `DC=drayve,DC=local` | LDAP base DN (authelia + lldap only) |
| `access_control.default_policy` | string | `one_factor` | Authelia policy for requests no rule matches: `deny`, `one_factor`, `two_factor`, `bypass` (authelia only) |
| `access_control.rules` | list | — | Authelia access rules, see below (authelia only) |
| `users` | list | — | File-backend users: `name`, `email` (required), `displayname`, `groups`. Authelia with `lldap: false` only; hashes go in `secrets.yml` |

#### Access control (`auth.access_control`)

Each rule has `domain` (string or list, `*.example.com` matches subdomains), `policy`, and optionally `groups` (matches members of any listed group), `resources` (path regexes) and `networks` (CIDR). **The first matching rule wins**, so put broad rules for privileged groups before narrow ones. A rule with `groups` cannot use `policy: bypass` (Authelia needs a login to know the group). Without `access_control`, Authelia keeps `default_policy: one_factor` and no rules, as before.

```yaml
auth:
  provider: authelia
  access_control:
    default_policy: deny
    rules:
      - domain: ["example.com", "*.example.com"]   # admins: everything, with 2FA
        policy: two_factor
        groups: [admins]
      - domain: grafana.example.com                # viewers: this one domain only
        policy: one_factor
        groups: [viewers]
```

Full example: `examples/stack-authelia-file.yaml`. Reference: <https://www.authelia.com/configuration/security/access-control/>.

#### File user backend (`lldap: false`)

`auth.users` plus `authelia_user_password_hashes` in `secrets.yml` (see `docs/secrets.md`) are the only source: each deploy rewrites `authelia/users_database.yml` (mode `0600`), and Authelia reloads it (`watch: true`). Password change and password reset are switched off in Authelia, otherwise it would write the file back and the next deploy would overwrite the change. A missing user or hash fails `make validate` and the deploy before anything is written. Hash generation: `authelia crypto hash generate argon2` (command in `docs/secrets.md`). References: <https://www.authelia.com/configuration/first-factor/file/>, <https://www.authelia.com/configuration/first-factor/introduction/>.

### `monitoring`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `profile` | string | `full` | `full` (~800MB) — Grafana, Prometheus, Loki, Promtail, cAdvisor, node_exporter. `light` (~50MB) — node_exporter + Promtail only. `none` — no monitoring |
| `loki_url` | string | `http://loki:3100/...` | Loki push URL for Promtail. **Required** for `light` profile (no local Loki). Defaults to local Loki for `full` profile |
| `loki_basic_auth_user` | string | — | HTTP Basic-Auth username for the Promtail Loki client (e.g. an external Loki behind Basic-Auth). Password goes in `secrets.yml` as `monitoring_loki_basic_auth_password`, never here. Empty (default) — no Basic-Auth block rendered |
| `loki_host_label` | string | — | `external_labels.host` value sent with every log line to Loki — use when several hosts push into the same external Loki. Empty (default) — no label rendered |
| `grafana` | bool | — | Override Grafana on/off regardless of profile |
| `prometheus` | bool | — | Override Prometheus on/off |
| `loki` | bool | — | Override Loki on/off |
| `cadvisor` | bool | — | Override cAdvisor on/off |
| `node_exporter` | bool | — | Override node_exporter on/off |

### `security`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `crowdsec` | bool | `true` | Deploy CrowdSec + Traefik bouncer plugin |

### `headers`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `clacks` | bool | `false` | Send `X-Clacks-Overhead: GNU Terry Pratchett` on every response served through Traefik (middleware `clacks@file` on the `websecure` entrypoint) |

### `secrets`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `quickstart` | `quickstart` — auto-generate random secrets in plaintext. `sops` — encrypted secrets via AGE + SOPS |

### `backup`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable backup |
| `schedule` | string | `0 3 * * *` | Cron expression |
| `target` | string | `local` | `kedge` (recommended), `local`, `sftp` |

### `extra_files`

Out-of-band static config files for services in `compose.override.yml` that
Drayve does not template — e.g. an `nginx.conf` for a reverse-proxy sidecar, a
hand-maintained `.htpasswd`, a `redis.conf`. Each entry is copied from
`deploy/<host>/<src>` on the controller to `<deploy_dir>/<dest>` on the target
host **before `docker compose up`**, so bind-mounts in the override resolve.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `src` | string | — (required) | Path relative to `deploy/<host>/` on the controller. Must exist locally; a missing src fails the deploy early (before compose up), not as a downstream container crash. No leading `/`, no `..`. |
| `dest` | string | value of `src` | Path relative to the deploy dir on the target host. No leading `/`, no `..`. |
| `mode` | string | `0644` | Octal file mode on the target host. |

```yaml
extra_files:
  - src: caddy/Caddyfile          # → <deploy_dir>/caddy/Caddyfile on the host
  - src: api/.htpasswd
    dest: api/.htpasswd
    mode: "0640"
```

The matching bind-mount in `deploy/<host>/compose.override.yml`:

```yaml
services:
  caddy:
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
```

Drayve ships the file verbatim — it does not parse or validate the content.
Treat secrets in `extra_files` like any other deploy-dir state: if the file
holds credentials, keep it out of the public repo and under your SOPS/secrets
discipline.

### Landing page

The landing page is auto-generated at deploy time by scanning `services/*/compose.yml` for `drayve.landing.*` Docker labels. No configuration in `stack.yaml` needed.

Services declare their landing page presence via labels in their `compose.yml`:

```yaml
labels:
  drayve.landing.name: "Nextcloud"
  drayve.landing.icon: "cloud"
  drayve.landing.category: "Collaboration"
```

No labels → service does not appear on the landing page.

## Ansible defaults

Beyond `stack.yaml`, Ansible role defaults can be overridden in `ansible/inventory/group_vars/all/vars.yml` or per-host in `host_vars/<name>/`:

| Variable | Default | Description |
|----------|---------|-------------|
| `drayve_root` | `/opt/drayve` | Base directory on the server |
| `drayve_user` | `drayve` | System user for running services |
| `drayve_uid` | `1000` | UID/GID for the drayve user |
| `swap_size_mb` | `2048` | Swap file size |
| `acme_email` | — | Set via `stack.acme_email` (see above) |
| `ssh_password_auth` | `no` | SSH password authentication |
| `ssh_root_login` | `prohibit-password` | SSH root login policy |
| `mem_*` | varies | Memory limits per container (e.g. `mem_grafana: 256m`) |

## Examples

- `deploy/_example/stack.yaml` — default template (used by `make init`)
- `examples/stack-minimal.yaml` — smallest useful config
- `examples/stack-full.yaml` — enterprise setup with Authelia, LLDAP, SOPS, storagebox backup
