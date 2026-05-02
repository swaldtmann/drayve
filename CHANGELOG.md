# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `make test-syntax-provision` — Ansible syntax-check for `provision.yml`
  with required stub vars (`drayve_name`, `domain`, `provider_type=manual`).
  Closes the test gap noted in v0.3.1 hotfix discussion.
- **Preflight variable validation** (`ansible/tasks/preflight_vars.yml`) —
  imported as the first task of `deploy.yml` and `backup.yml`. Asserts
  that `drayve_domain`, `drayve_root`, `ops_secrets_root`, and
  `drayve_user` are defined and non-empty before any role runs. On
  failure, prints current values plus a hint about inventory layout and
  `docs/quickstart.md`. Replaces the late, cryptic variable-resolution
  errors that surfaced when `group_vars/all/vars.yml` was not loaded.

### Fixed
- `make test-syntax` no longer swallows ansible-playbook errors. The
  previous pipe ended with `... | grep -vE '...' || true`, which made the
  whole pipeline exit 0 even when `--syntax-check` failed. Replaced with
  `set -o pipefail && ... | (grep -vE '...' || true)`.

## [0.3.0] - 2026-05-02 — single-app hardenings + DNS provider choice

A consolidated harvest of frictions surfaced while bringing up Mealie on
its own host (rezepte.ewaldshof.de). Most patches keep existing
multi-app stacks (drayve-prod-genua, drayve-ewh-dev) working unchanged
behind backward-compatible defaults; one is breaking for downstreams
that overrode `ops_repo_root` directly (see migration below).

### Added
- **Configurable ACME DNS-01 provider** — `stack.acme_dns_provider`,
  `stack.acme_dns_propagation_delay`, `stack.acme_dns_env_vars` replace
  the hardcoded `hetzner` / `30s` / single-token configuration. Hosts
  whose DNS lives at Cloudflare, Netcup, Route 53, etc. no longer need
  to fork the Traefik template (W-107).
- **Automatic Traefik routing label for multi-network services** —
  drayve scans `compose.override.yml` during deploy and injects
  `traefik.docker.network=drayve_default` on services that join two or
  more networks and route through Traefik. Prevents the "504 with
  healthy container" trap that took rezepte.ewaldshof.de down for
  about 5.5h. Configurable via `multinet_primary_network`; user-pinned
  labels are preserved (W-112).
- **`make first-deploy NAME=`** — actionable error when deploy is run
  on an unprovisioned host (W-109 #3).
- **`apps[].category` is now required** — schema validation fails with
  a precise error instead of letting the landing template crash
  (W-109 #5).
- **Single-app host opt-out for the apex landing page** —
  `stack.single_app: true` (or `services.landing.enabled: false`)
  removes the landing container without override hacks (W-109 #6).
- **CrowdSec bouncer self-heal watchdog** — systemd-timer-driven
  health probe restarts Traefik when the bouncer plugin enters
  fail-closed or hold-mode after LAPI restarts. Ships with 20 bats
  tests; opt-out via `bouncer_watchdog_enabled: false` (W-109 #7).
- **`make test-unit`** — pytest + bats runners for unit-level checks
  that don't need a live host.
- **`docs/dns-providers.md`** — provider matrix and switching guide.
- **`docs/single-app-host.md`** — single-app deploy walkthrough
  (W-109 #9).

### Changed
- **`ops_repo_root` split into `ops_secrets_root` + `ops_deploy_root`**
  — secrets-role and deploy-role no longer fight over the same
  variable. Single-app sites can keep an OSS-conformant `deploy/<host>/`
  layout without symlink trickery (W-109 #1, see migration below).
- **`env.override` supports SOPS transparently** — drayve detects
  encrypted env.override files (`sops:` header / `ENC[` heuristic) and
  decrypts via `community.sops`. Plain-text env.override still works
  unchanged. Closes the "sops creation_rule plus plaintext lookup"
  reflex-trap that bricked drayve-rezepte (W-109 #2 / W-112 part 2).

### Documentation
- README requirements section now states explicitly that drayve assumes
  Ubuntu LTS hosts. Distro-agnostic docker-role is tracked but
  deferred (W-109 #4).
- `docs/secrets.md` documents the env.override SOPS mode and the
  per-app secrets migration path (W-109 #8).

### Migration

For consumers of pre-0.3.0:

1. Replace any `ops_repo_root: ...` overrides with the appropriate of
   `ops_secrets_root` (where your secrets / `deploy/<host>/` live) and
   `ops_deploy_root` (where the drayve framework lives — usually a
   submodule path or vendored copy). The shipped `make` targets infer
   the right values; manual playbook invocations need both vars set.
2. Stacks with `apps:` must set `category` on every entry. Run
   `make validate NAME=<host>` to find missing values.
3. `compose.override.yml` files for multi-network services no longer
   need a manual `traefik.docker.network=...` label — drayve injects
   it. Existing labels are preserved, so no action required, but the
   workaround can be removed for clarity.

## [0.2.0] - 2026-04-27 — initial public release

History before this tag was squashed during the OSS push. The items below
describe the feature set shipped in v0.2.0; issue/commit references point
to pre-squash history and are no longer reachable in this repository.


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
- Upgrade-path tested on live instance

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
