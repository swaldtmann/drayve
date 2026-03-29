# Changelog

## 2026-03-29 — Initial Release (Maiden Voyage Phase 0 + 1)

Drayve extracted from kigulls-ops as a standalone, generic Ops framework.

### Added
- **stack.yaml schema** (`config/stack.schema.yaml`) — single-file configuration for auth, monitoring, secrets, backup, provider.
- **Validation** (`scripts/validate-stack.py`) — checks required fields, enums, constraints (e.g. lldap requires authelia).
- **Makefile** — 11 targets: provision, deploy-dev, deploy-prod, burn, status, smoke, validate, lint, secrets-init, secrets-template, backup.
- **Ansible roles** (all generic, zero KIgulls references):
  - `common` — SSH hardening, UFW, swap, packages, app user.
  - `docker` — Docker CE install, journald logging.
  - `traefik` — Reverse proxy, CrowdSec, auth-aware middleware.
  - `auth` — Provider dispatcher (none / basic / authelia).
  - `authelia` — SSO with optional LLDAP bind user.
  - `lldap` — LDAP user backend with provisioning.
  - `monitoring` — Profile-based (full / light / none) with a-la-carte overrides.
  - `secrets` — Loads per-host secrets (quickstart or SOPS).
  - `deploy` — Templates Compose + .env, pulls, starts stack.
- **Secrets management:**
  - Quickstart mode — auto-generated random secrets, no setup needed.
  - SOPS mode — AGE key + encrypted secrets (`secrets-init.sh`, `secrets-template.sh`).
- **docker-compose.yml.j2** — Traefik, CrowdSec, Auth (basic/authelia/none), Monitoring (profile-based), whoami proof-of-life.
- **7 Grafana dashboards** (auto-provisioned): Host, Containers, Traefik, CrowdSec, Logs, Backup (new), Stack Overview (new).
- **Playbooks:** provision.yml (hetzner + manual provider), deploy.yml.
- **Examples:** stack-minimal.yaml, stack-full.yaml.
