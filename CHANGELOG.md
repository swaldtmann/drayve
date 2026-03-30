# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security
- OIDC client_secret hashed — Authelia config now uses PBKDF2-SHA512 hashes instead of plaintext (#59)
- CrowdSec IP whitelist — `security.crowdsec_whitelist` in stack.yaml, deployed as parser-level whitelist (#S167)

### Fixed
- LLDAP groupId dynamic — resolved via GraphQL instead of hardcoded `3` (#58)
- LLDAP email collision — check if user exists before `createUser` mutation (#69)
- LLDAP healthcheck timing — increased `start_period` to 20s, reduced interval to 5s (#85)
- Grafana Feature-Toggle — `GF_FEATURE_TOGGLES_DISABLE=kubernetesDashboards` prevents 403 on OIDC users (#82)
- Grafana dashboard queries — precise error/warning regex, CrowdSec NaN guard, correct metric names
- Grafana restart guard — skip `docker compose up` when compose file doesn't exist yet
- htpasswd idempotent — basic auth file only generated once, not on every deploy
- validate-stack warnings — suppress `acme_email` warning for example files (#89)

### Added
- `make dashboards NAME=` — deploy only Grafana dashboards/datasources without full redeploy (#87)
- `make bans/unban NAME=` — manage CrowdSec decisions from CLI (#S167)
- Grafana auto-restart on dashboard/datasource changes
- LLDAP user provisioning — users + groups from stack.yaml, auto-generated passwords
- Grafana OIDC — full SSO via Authelia, role mapping from LLDAP groups
- Landing page apps — user apps from stack.yaml shown on landing page

### Testing
- 3 Molecule scenarios — `authelia`, `basic`, `light`. All pass converge, idempotency, and verification
- 44 Testinfra assertions across all scenarios
- Upgrade-path tested on live drayve-authbox instance

### Documentation
- First login guide, files overview, env.override docs

## [0.1.0] - 2026-03-29

### Added
- Multi-host deploy/ structure — framework separated from user infrastructure
- `make init` scaffolds new hosts, `make list` shows configured hosts
- compose.override.yml for user services (Nextcloud example included)
- Ansible roles: common, docker, traefik, auth, authelia, lldap, monitoring, secrets, deploy, backup
- 7 Grafana dashboards (auto-provisioned): Stack Overview, Host, Containers, Traefik, Logs, CrowdSec, Backup
- Landing page with live status badges
- CrowdSec with Traefik bouncer plugin
- stack.yaml single-file config with schema validation
- Quickstart + SOPS secret modes
- Hetzner Cloud + Manual providers
- `make provision/deploy/burn/status/validate/lint`
- Documentation: quickstart, configuration, architecture, secrets
