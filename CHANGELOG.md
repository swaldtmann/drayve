# Changelog

## 0.1.0 — 2026-03-29

### Architecture
- **Multi-host deploy/ structure** — framework (ansible/, config/) separated from user infrastructure (deploy/). Each host gets `deploy/<name>/stack.yaml` + `secrets.yml`. Gitignored.
- **`make init`** scaffolds new hosts from minimal example.
- **`make list`** shows all configured hosts.
- **compose.override.yml** — users add their own services (Nextcloud example included). Docker Compose merges automatically.
- **One network** (`drayve_default`) for the platform stack. User apps bring their own backend networks for DB isolation.

### Stack
- **Ansible roles** (all generic):
  - `common` — SSH hardening, UFW, swap, packages, app user.
  - `docker` — Docker CE install, journald logging.
  - `traefik` — Reverse proxy, automatic TLS, CrowdSec bouncer plugin, auth-aware middleware.
  - `auth` — Provider dispatcher (none / basic / authelia).
  - `authelia` — SSO with optional LLDAP backend.
  - `lldap` — LDAP user directory with provisioning.
  - `monitoring` — Profile-based (full / light / none) with a-la-carte overrides.
  - `secrets` — Per-host secrets (quickstart or SOPS with AGE).
  - `deploy` — Templates Compose + .env, pulls, starts stack, deploys overrides.
  - `backup` — Restic-based, cron-scheduled. (docker-stack-backup integration planned)
- **7 Grafana dashboards** (auto-provisioned): Stack Overview, Host, Containers, Traefik, Logs, CrowdSec, Backup.
- **Landing page** with live status badges and links to all services.
- **CrowdSec** with healthcheck, Traefik bouncer plugin (maxlerebourg v1.5.1), `service_healthy` dependency.

### Configuration
- **stack.yaml** — single-file config: auth, monitoring, secrets, backup, provider, acme_email, lldap_base_dn.
- **Schema validation** (`config/stack.schema.yaml`, `make validate`).
- **Nested dict resolution** — all stack.yaml values (auth.provider, monitoring.profile, security.crowdsec, backup.enabled, etc.) correctly resolved from nested structure in every role.
- **`acme_email`** configurable per stack (no hardcoded email).
- **`crowdsec_enabled`** single source of truth in group_vars, no more `| default(true)` scattered across templates.

### Secrets
- **Quickstart mode** — auto-generated random secrets, zero setup.
- **SOPS mode** — AGE key in `deploy/.age-key.txt`, encrypted secrets safe to commit.
- **Key preservation** — existing bouncer/LAPI keys on server preserved during re-deploy unless new secret explicitly provided.

### Operations
- **`make provision NAME=`** — full provisioning (hetzner auto-create or manual existing server).
- **`make deploy NAME=`** — re-deploy after config changes. Force-recreates Traefik on config change, reloads Prometheus via API.
- **`make burn NAME= [CONFIRM=y]`** — tears down server, cleans DNS (hetzner), removes local files. Requires confirmation.
- **`make status NAME=`** / **`make validate NAME=`** / **`make lint`**.
- **`drayve_name`** used instead of Ansible reserved `name` variable.

### Documentation
- **README** — component overview, quick start, make targets.
- **docs/quickstart.md** — zero to running stack, multi-host management, compose override.
- **docs/configuration.md** — full stack.yaml reference.
- **docs/architecture.md** — diagrams, directory layout, provisioning flow, logging strategy.
- **docs/secrets.md** — quickstart vs SOPS, AGE key backup warning.

### Providers
- **Hetzner Cloud** — auto-provision server, DNS cleanup on burn.
- **Manual** — bring your own Ubuntu server, SSH access only requirement.
