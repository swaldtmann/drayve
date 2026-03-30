# Changelog

## 0.2.0-rc1 — 2026-03-30

### Security
- **OIDC client_secret hashed** — Authelia config now uses PBKDF2-SHA512 hashes instead of plaintext. Hash generated once, stored persistently for idempotency. (#59)
- **CrowdSec IP whitelist** — `security.crowdsec_whitelist` in stack.yaml, deployed as parser-level whitelist. (#S167)

### Fixes
- **LLDAP groupId dynamic** — `lldap_strict_readonly` group ID resolved via GraphQL instead of hardcoded `3`. (#58)
- **LLDAP email collision** — check if user exists before `createUser` mutation, prevents UNIQUE constraint errors on every deploy. (#69)
- **LLDAP healthcheck timing** — increased `start_period` to 20s, reduced interval to 5s. Reduces Authelia LDAP race condition on startup. (#85)
- **Grafana Feature-Toggle** — `GF_FEATURE_TOGGLES_DISABLE=kubernetesDashboards` prevents intermittent 403 on OIDC users. (#82)
- **Grafana dashboard queries** — precise error/warning regex, CrowdSec NaN guard, correct metric names, range+lastNotNull for stat panels.
- **Grafana restart guard** — skip `docker compose up` when compose file doesn't exist yet (first provision).
- **htpasswd idempotent** — basic auth file only generated once, not on every deploy.
- **validate-stack warnings** — suppress `acme_email` warning for example files. (#89)

### Features
- **`make dashboards NAME=`** — deploy only Grafana dashboards/datasources without full redeploy. (#87)
- **`make bans/unban NAME=`** — manage CrowdSec decisions from CLI. (#S167)
- **Grafana auto-restart** — monitoring role restarts Grafana on dashboard/datasource changes.
- **LLDAP user provisioning** — users + groups from stack.yaml, auto-generated passwords.
- **Grafana OIDC** — full SSO via Authelia, role mapping from LLDAP groups.
- **Landing page apps** — user apps from stack.yaml shown on landing page.

### Testing
- **3 Molecule scenarios** — `authelia` (full+SSO), `basic` (basic auth+full monitoring), `light` (no auth+light monitoring). All pass converge, idempotency, and verification.
- **44 Testinfra assertions** across all scenarios.
- **Upgrade-path tested** on live drayve-authbox instance.

### Documentation
- First login guide, files overview, env.override docs.

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
