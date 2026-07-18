# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.8] - 2026-07-18

### Fixed
- **`acme_dns_provider` was silently ignored** — `stack.yaml`'s
  `acme_dns_provider`/`acme_dns_env_vars`/`acme_dns_propagation_delay` had no
  resolve task mapping them to Ansible vars, and even where they did apply,
  every Traefik router label hardcoded `certresolver=letsencrypt` (the
  HTTP-01 resolver) — the correctly-configured `letsencrypt-dns` resolver in
  `traefik.yml.j2` was defined but never referenced. New `acme_challenge`
  setting (`http`, default, or `dns`) now actually switches the resolver
  used by every router. DRAYVE-W-011.
- Verified (no code change needed): the pinned `traefik:v3.6` image bundles
  lego v4.35.x, well past the v4.27 release that added Hetzner Cloud DNS API
  support (`HETZNER_API_TOKEN`, already Drayve's default) — the legacy
  `dns.hetzner.com` API concern from DRAYVE-W-011 Fund 2 was already resolved
  upstream by the time it was investigated.

## [0.6.6] - 2026-07-16

### Fixed
- **`deploy : Docker compose up` idempotence-flake** — `changed_when` matched
  bare `'Started'` in `docker compose up` output, which Compose also prints
  when a container is merely restarted without any config change (e.g. a
  `depends_on: condition: service_healthy` dependency getting re-evaluated
  on every `up` and hitting a health-poll race). This caused sporadic
  Molecule idempotence failures on the auth-provider scenarios (authelia,
  authentik, authentik-ldap, integration — the heaviest/slowest-converging
  ones, giving the race the widest window). Now only `'Created'`/`'Recreated'`
  count as changed — real drift, not a benign restart. CW-W-181.

## [0.6.1]

### Fixed
- **`.kedge.env.j2` quoting bug** — `BACKUP_EXCLUDE_MOUNTS` rendered without
  shell quotes, so a space-separated multi-path value broke `source
  .kedge.env` itself (bash tried to run the second path as a command),
  aborting `kedge backup` under `set -e` before it ran. Blocked
  `monitoring: profile: full` stacks, where kedge's `discover_bind_mounts`
  picks up cAdvisor's `/` rootfs mount plus node-exporter/promtail system
  paths (`/sys`, `/var/log`, `/var/run`, `/var/lib/docker`) and needs all
  of them excluded at once. Found live on EWH-W-132's persistent cutover
  lab (P5.6c). Added a regression test that actually `source`s the
  rendered env file in bash instead of just parsing `KEY=value` lines —
  the prior test suite would not have caught this.

## [0.6.5] - 2026-07-15

### Changed
- **`backup_kedge_version` default bumped v0.3.2 -> v0.3.4** — v0.3.3 fixes
  `KEDGE_VERSION` falling back to `dev` when kedge runs through drayve's
  `/usr/local/bin/kedge -> /opt/kedge/backup.sh` symlink (unresolved
  `SCRIPT_DIR` broke `git describe`); v0.3.4 is a shellcheck-only cleanup
  on top. Also corrected `docs/backup-with-kedge.md`'s tunables table,
  which still listed the stale `v0.3.1` default.

## [0.6.4] - 2026-07-15

### Added
- **kedge cron fail-signal wrapper** — new `backup_kedge_cron_wrapper` var
  wraps both the `kedge backup` and `kedge prune` cron lines with a
  generic `<wrapper> <job-name> -- <cmd...>` contract (e.g. a fail-signal
  helper). kedge's own `BACKUP_FAIL_HOOK`/`BACKUP_POST_HOOK` only fire from
  `cmd_backup`'s cleanup trap — `cmd_prune` has no hook or healthcheck
  integration at all, so a per-job cron wrapper is the only place that
  covers both uniformly. Empty (default): cron lines are unwrapped, byte-
  identical to before this option existed. Anlass: CW-W-178 RCA — prod-genua
  had fail-alerting (AFKI-W-219, `alert-pub`) hand-patched directly into
  `/etc/cron.d/kedge-genua`/`kedge-genua-prune`, invisible to and
  incompatible with the templated `/etc/cron.d/kedge-<stack_name>` file.

## [0.6.3] - 2026-07-15

### Fixed
- **Grafana never got the `auth@file` middleware** — `landing` and `dashboard`
  (Traefik's own UI) were gated behind `auth@file` whenever `auth_provider !=
  'none'`, but `grafana`'s router labels never got the same treatment. Grafana
  relied solely on its own native login (admin/`GF_SECURITY_ADMIN_PASSWORD`,
  or OIDC auto-login for authelia/authentik), so with `auth: provider: basic`
  it was the only public admin surface without an infra-level BasicAuth gate.
  Found live on ewh-lab (EWH-W-132/EWH-W-131 Folge, 2026-07-15) rolling out
  `auth:basic`. Now consistent with landing/dashboard for every
  `auth_provider != none`.

## [0.6.2]

### Added
- **kedge `BACKUP_PRE_HOOK` passthrough** — new `backup_kedge_pre_hook` var
  renders `BACKUP_PRE_HOOK` in `.kedge.env.j2` (quoted, same fix class as
  `BACKUP_EXCLUDE_MOUNTS` above). Lets a stack run an arbitrary command
  before kedge's own backup steps — e.g. dumping a native (non-Docker)
  service into a fixed path that a bind-mount entry then hands to kedge's
  external-mount archiver. Anlass: EWH-W-132, ewh-lab runs a native `ds389`
  directory server outside any container; kedge's Compose-only
  auto-discovery can't see it otherwise.
- **Declarative OIDC clients** (W-144) — `stack.yaml` accepts
  `auth.oidc_clients` (list), `auth.authorization_policies` and
  `auth.claims_policies` (dicts). The Authelia config template iterates
  over the list instead of the previous Grafana-only hardcode. Per-client
  plain secret comes from `host_secrets.authelia_oidc_<id>_secret`, the
  PBKDF2-SHA512 hash is generated once on the host
  (`<authelia-data>/.oidc_<id>_hash`), and the plain secret is exposed to
  downstream services as `AUTHELIA_OIDC_<ID>_SECRET` in `.env`.
  Backward-compatible: stacks without `auth.oidc_clients` keep the legacy
  single Grafana client. Anlass: S328-Folge12 — Forgejo SSO outage caused
  by `make deploy-prod` re-templating manual OIDC additions. See
  `docs/auth-oidc-clients.md`.
- **`make deploy-check-fast`** (W-131) — fast static validation of
  `stack.yaml` and host secrets without touching the target host.
  Runs `playbooks/validate.yml` locally and asserts: preflight vars,
  `apps[].category`, `host_secrets.lldap_jwt_secret`/`lldap_key_seed`
  when `auth.lldap` is enabled, and kedge backup prerequisites when
  `backup.target=kedge`. Replaces the routine "did I break the
  config?" use of `deploy-check` while the deep dry-run remains
  structurally broken in v0.4.x.
- **lldap ENV-coverage** — drayve compose env now drives the lldap
  config knobs that previously fell through to the docker-image default
  (`LLDAP_LDAP_USER_EMAIL`, `LLDAP_HTTP_URL`, `LLDAP_VERBOSE`). Combined
  with the existing JWT/key-seed/key-file settings, the env-block is now
  the single source of truth for everything drayve manages.
- `LLDAP_HTTP_URL=https://ldap.<drayve_domain>` makes password-reset
  email links work — the docker default `http://localhost` is unusable
  in production.
- `lldap_verbose` (default `false`) toggles `LLDAP_VERBOSE` for
  on-demand debug logging without an image switch.
- Consumers can extend / override any other `LLDAP_*` knob via
  `deploy/<host>/env.override` per upstream contract
  (lldap_config.docker_template.toml: *"All values can be overridden
  through environment variables"*).

### Changed
- **Kedge cron now lives in `/etc/cron.d/kedge-<stack_name>`** instead of
  the root crontab (Ansible `cron` module). Single file holds both the
  daily backup and the weekly prune entry. Rationale: visible to
  `ls /etc/cron.d/`, debuggable without `crontab -l`, follows distro
  convention (apt packages drop their cron jobs there), atomically
  versionable via template checksum, and matches the legacy AFKI-W-044
  layout that consumers already know.
- New canonical `stack_name` fact resolved from `stack.name` (stack.yaml)
  with fallback to `drayve_name` then `inventory_hostname`. Drives the
  cron file name today; available to other roles for derived artefact
  names.
- `backup_kedge_version` default bumped from `v0.3.1` to `v0.3.2`
  (kedge tool/format-version split, see kedge#21).

### Migration
- Existing installs with `target=kedge`: the next deploy will (a) remove
  any stale `drayve-backup`, `drayve-kedge-backup`, `drayve-kedge-prune`
  entries from the root crontab, (b) write
  `/etc/cron.d/kedge-<stack_name>`. Old hand-managed files like
  `/etc/cron.d/kedge-genua` (different name from `stack.name`) are NOT
  touched — remove them by hand once the new file is verified, or rename
  before the deploy so the template idempotently replaces them.

### Added
- **Per-install lldap private key** — `roles/lldap/` now requires
  `lldap_key_seed` (>= 12 chars, 32 generated by
  `scripts/secrets-generate.sh`). Compose-template forwards
  `LLDAP_KEY_SEED` and pins `LLDAP_KEY_FILE=` (empty) so lldap derives
  the private key deterministically from the seed only.
- Without the seed, lldap >= v0.6 falls back to the docker-template
  default `key_seed = "RanD0m STR1ng"` (lldap/lldap@main
  `lldap_config.docker_template.toml` L118), shared across every drayve
  install — pw-hash isolation across installs was effectively broken.
- The empty `LLDAP_KEY_FILE` also sidesteps the lldap >= v0.6
  ambiguous-source bail (lldap/lldap#778) where a present
  `key_seed` plus an existing `server_key` file aborts on container
  restart. Existing installs migrate without pw-hash loss as long as
  `host_secrets.lldap_key_seed` matches the pre-existing
  `lldap_config.toml` `key_seed` value.

## [0.5.0] - 2026-05-07

### Added
- **`backup.target: kedge`** — backup role can now delegate to the
  [kedge](https://codeberg.org/StephanWaldtmann/kedge) CLI instead of
  running its in-role restic wrapper. When selected, the role:
  - installs `restic`, `jq`, `rsync`, `git`,
  - clones `kedge` to `backup_kedge_install_dir` (default `/opt/kedge`)
    pinned to `backup_kedge_version` (default `v0.3.1`),
  - symlinks the CLI to `/usr/local/bin/kedge`,
  - renders `/root/.kedge.env` from `host_secrets`
    (`templates/.kedge.env.j2`, mode 0600, `no_log: true`),
  - installs two idempotent cron entries: daily `kedge backup`
    (`backup_schedule`) and weekly `kedge prune`
    (`backup_prune_schedule`, default `30 4 * * 0`),
  - removes the legacy `drayve-backup` cron entry to avoid double-runs.
- New role defaults: `backup_kedge_repo`, `backup_kedge_version`,
  `backup_kedge_install_dir`, `backup_kedge_env_file`,
  `backup_kedge_log_file`, `backup_kedge_stack_dir`,
  `backup_kedge_restic_repository`, `backup_kedge_stop_stack`,
  `backup_kedge_exclude_mounts`, `backup_kedge_healthcheck_url`,
  `backup_prune_schedule`.
- New required `host_secrets` keys when `backup.target: kedge`:
  `backup_restic_password` (already used by legacy targets) and
  `backup_kedge_restic_repository` (or set as a stack var).
- `docs/backup-with-kedge.md` — usage and migration notes.

### Changed
- Backup role splits cron deployment per target. The legacy
  `local`/`sftp` cron job (`drayve-backup`) is unchanged; the kedge
  target installs `drayve-kedge-backup` + `drayve-kedge-prune` instead.
- Restic install task gated to `local`/`sftp`; the kedge dependency
  task installs the same plus `jq`, `rsync`, `git`.

### Deprecated
- `backup.target: local` and `backup.target: sftp` — the in-role restic
  wrapper. No removal in 0.5.x; `kedge` is the recommended target for
  new deployments. Migration: see `docs/backup-with-kedge.md`.

## [0.4.1] - 2026-05-06

### Fixed
- **secrets role: sops decrypt now runs in `--check` mode**
  (`ansible/roles/secrets/tasks/main.yml`). The decrypt command task was
  silently skipped under `ansible-playbook --check`, leaving
  `host_secrets` unset; the plaintext-fallback `include_vars` then loaded
  the still-encrypted YAML and produced a `host_secrets` dict without any
  of the real secret keys. With v0.4.0's new `lldap_jwt_secret`
  length-assertion this surfaced as a `deploy-check` failure on every
  sops-mode host. Fix: `check_mode: false` on the read-only decrypt
  command. dry-run results are now meaningful again.

## [0.4.0] - 2026-05-06

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
  errors that surfaced when `group_vars/all/vars.yml` was not loaded
  (W-115).
- **Bouncer-watchdog gating regression test** —
  `tests/python/test_bouncer_watchdog_gating.py` pins the gating
  conditions so regressions in the systemd-timer scaffolding are caught
  at unit-test speed (W-119).
- **`ansible/consumer.mk`** — shared Makefile snippet for downstream
  consumer repos (drayve-prod-genua, drayve-rezepte). Provides
  `vendor`, `deploy`, `deploy-prod`, `provision`, `validate`, `status`,
  `logs`, `backup`, `burn` targets plus required-var asserts as a
  single `include`. Replaces ~35–36 lines of drift-prone boilerplate
  per consumer; both consumers switched in lockstep with this release
  (W-126).

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
