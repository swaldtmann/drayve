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
| `server_type` | string | `cx22` | Hetzner server type (hetzner only) |
| `location` | string | `fsn1` | Hetzner datacenter (hetzner only) |

### `auth`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `basic` | `none` — no auth. `basic` — HTTP basic auth. `authelia` — full SSO with web portal |
| `lldap` | bool | `false` | Deploy LLDAP as user backend (authelia only) |
| `lldap_base_dn` | string | `DC=drayve,DC=local` | LDAP base DN (authelia + lldap only) |

### `monitoring`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `profile` | string | `full` | `full` (~800MB) — Grafana, Prometheus, Loki, Promtail, cAdvisor, node_exporter. `light` (~50MB) — node_exporter + Promtail only. `none` — no monitoring |
| `grafana` | bool | — | Override Grafana on/off regardless of profile |
| `prometheus` | bool | — | Override Prometheus on/off |
| `loki` | bool | — | Override Loki on/off |
| `cadvisor` | bool | — | Override cAdvisor on/off |
| `node_exporter` | bool | — | Override node_exporter on/off |

### `security`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `crowdsec` | bool | `true` | Deploy CrowdSec + Traefik bouncer plugin |

### `secrets`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `quickstart` | `quickstart` — auto-generate random secrets in plaintext. `sops` — encrypted secrets via AGE + SOPS |

### `backup`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable backup |
| `schedule` | string | `0 3 * * *` | Cron expression |
| `target` | string | `local` | `local`, `storagebox`, `s3`, `ssh` |

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
