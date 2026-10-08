# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.8.11] - 2026-10-08

### Added
- `backup_kedge_system_paths` (default empty), rendered as kedge's
  `SYSTEM_PATHS` in `/root/.kedge.env` when non-empty. Before, a site setting
  it had the value silently ignored, and a role run removed a hand-written
  `SYSTEM_PATHS` line from the host.

### Changed
- **Sites:** `backup_kedge_system_paths_exclude` is NOT a drayve variable; the
  exclude list is `backup_kedge_exclude_paths` (kedge `SYSTEM_PATHS_EXCLUDE`).
  With `backup_target: kedge`, the backup role now aborts early with a clear
  message if the old name is defined (even empty), instead of ignoring it.
  Rename it in your site config.
- **The deploy role prints the stderr of `docker compose up`** (new task
  "Show docker compose up output", only when the step reports a change), so a
  surprise recreate such as `traefik` in an idempotence run can be explained
  from the log.

### Fixed
- **authentik bootstrap no longer races the default flows.** The script waited
  only for `/-/health/ready/`, but authentik creates the default flows
  asynchronously via blueprints. A single early lookup could return no
  authorization flow and the script then sent `POST /providers/proxy/` with
  `authorization_flow: None` (HTTP error, converge failed). `bootstrap.py.j2`
  now polls (300 s limit, 5 s interval) until the authorization flow and the
  invalidation flow exist, falls back to explicit consent only after implicit
  consent has been missing for 60 s, and fails with a clear message naming the
  missing flow instead of creating a provider with an empty flow. No extra wait
  when the flows are already there.
- **First rollout rebuilt the CrowdSec container twice.** `config.yaml.local`
  is newly created on the first run, `docker compose up` creates `crowdsec`
  anyway, and the unconditional "Force-recreate CrowdSec" task then rebuilt it
  a second time (about 30 s of HTTP 403 on all services, since Traefik waits
  for CrowdSec to be healthy). The force-recreate is now skipped when
  `compose up` created or recreated `crowdsec` in the same run. Unchanged: a
  whitelist/config change with CrowdSec untouched by `compose up` still
  recreates it.

## [0.8.10] - 2026-10-08

### Fixed
- **`make lint` reported "Lint passed" even when ansible-lint failed.** The
  target piped ansible-lint through `grep`/`sed` without `pipefail`, so the
  exit status was always that of `sed`. It now fails when ansible-lint does.
- **ansible-lint failures in `roles/backup/tasks/kedge_version_hint.yml`**
  (`name[casing]` twice, `command-instead-of-module`), which made the
  `Test & Lint` workflow fail for `v0.8.9`. No change in behaviour.
- **`make help` in a site repo printed file paths instead of target names.**
  With more than one makefile in `MAKEFILE_LIST`, `grep` prefixed each line
  with the file name and the target column was lost; `consumer.mk` now uses
  `grep -h`. The test for `backup-deploy` in `make help` only passed because
  the checkout path happened to contain the string.

## [0.8.9] - 2026-10-08

### Added
- **`make backup-deploy` in `ansible/consumer.mk`** runs `playbooks/backup.yml`
  for a host (same arguments as `deploy-prod`, `CONFIRM=y` skips the prompt,
  `EXTRA_ANSIBLE_VARS="--check --diff"` for a dry run, no pre-snapshot). The
  deploy playbook does not run the backup role, so this is the way to move
  kedge to the version a release pins.
- **`deploy.yml` hints when kedge on the host is stale.** At the end of the
  deploy a read-only check (`roles/backup/tasks/kedge_version_hint.yml`)
  compares the host's kedge checkout with `backup_kedge_version` (site
  overrides respected) and points to `make backup-deploy` on a mismatch or a
  missing checkout. Only for hosts with backup enabled and target `kedge`;
  never fails the deploy and changes nothing on the host.

## [0.8.8] - 2026-10-07

### Fixed
- **Release-gate matrix workflow could not check out the repository.** The
  matrix value (a space-separated scenario list) went unquoted into the work
  directory, so `git clone` aborted before any scenario ran. `v0.8.7` was
  therefore tagged without a passed gate. No change to roles or templates.

## [0.8.7] - 2026-10-07

### Fixed
- **Role metadata of `host-base-update` states Apache-2.0**, matching `LICENSE`
  (was `MIT` in `meta/main.yml`).
- **Legacy backup script excludes transient SQLite rollback journals**
  (`--exclude '*.db-journal'` on the per-volume `restic backup`). CrowdSec's
  `crowdsec.db-journal` appears and vanishes during the live volume backup;
  restic then exits 3 (snapshot incomplete) and `set -e` failed the run
  (Molecule scenario `backup`). `-wal`/`-shm` stay in the snapshot.

### Changed
- **Documentation prepared for the public read-only mirror**: README clone URL
  and badge, CONTRIBUTING (e-mail reporting, no public issue tracker),
  SECURITY supported versions (0.8.x), internal paths removed from comments;
  internal `TODO.md` no longer tracked.

- **Molecule scenarios under `molecule/` now start local Incus VMs by default**
  (Ubuntu 24.04, 2 vCPU / 4 GiB / 20 GiB; `basic` and `light` 4 / 8 GiB). The
  platform and driver part moved out of the ten `molecule.yml` into the shared
  `.config/molecule/config.yml`; `DRAYVE_MOLECULE_DRIVER=molecule_hetznercloud`
  selects the previous Hetzner driver. `make test-*` targets only require
  `HCLOUD_TOKEN` for that value. Role-level scenarios (`common`, `docker`) are
  unchanged. See `docs/testing.md`.

- **Molecule scenarios take the Docker registry mirror from
  `DRAYVE_MOLECULE_REGISTRY_MIRROR`** (empty = no mirror) instead of the
  hard-coded `http://192.0.2.10:5000` (a documentation address) in nine
  `molecule.yml`; set once in `.config/molecule/config.yml`, so `integration`
  gets it too. The release-gate workflows set it to the pull-through cache of
  the runner VM. New `scripts/list-ci-images.py` lists the images to warm it.

- **behaviour change: `authentik_image` default is now
  `ghcr.io/goauthentik/server:2025.10.4`** (was `:2025.2.4`; compose template
  fallback, `vars.yml` and both Authentik Molecule scenarios). `2025.2.4` is
  published for arm64 only, so `molecule/authentik` and `molecule/authentik-ldap`
  could not start the server on an amd64 host; `2025.10.4` is a multi-arch index
  (linux/amd64 + linux/arm64). Existing hosts must NOT jump straight from
  2025.2.x: upstream requires upgrading through every release line
  (2025.4, 2025.6, 2025.8, 2025.10, latest patch each) with a database backup
  before each step. Pin `authentik_image` in the site repo to the next line
  first and walk up, then drop the pin. Since 2025.10 authentik no longer uses
  Redis; the `authentik-redis` container is still rendered but now unused
  (`AUTHENTIK_REDIS__*` is ignored) and authentik opens ~50% more PostgreSQL
  connections.

## [0.8.6] - 2026-10-02

### Changed
- journald: set `MaxFileSec` (new variable `journald_max_file_sec`, default
  `1day`) so the 14-day `MaxRetentionSec` also holds on low-volume hosts;
  journald deletes only whole archived files and would otherwise keep entries
  up to a month in the active file.
- **behaviour change: `backup_kedge_exclude_volumes` default is now
  `"traefik_logs crowdsec_db loki_data"`** (was `"traefik_logs"`). The CrowdSec
  database (alerts/decisions per IP) and Loki data (log lines) no longer end up
  in restic snapshots. Both start empty after a restore; run the deploy with
  `--tags secrets,deploy,crowdsec` afterwards so the Traefik bouncer key is
  registered again. Existing snapshots are not touched.

### Added
- `backup_kedge_exclude_paths` (default empty), rendered as kedge's
  `SYSTEM_PATHS_EXCLUDE`: restic `--exclude` patterns for paths below a bind
  mount (e.g. `/data/grocy/log`).

## [0.8.5] - 2026-10-01

### Changed
- **behaviour change: hosts move kedge to v0.5.1 on the next backup-playbook
  run.** `backup_kedge_version` default `v0.3.4` -> `v0.5.1`; before, a run
  would have reset a hand-updated `/opt/kedge` (e.g. `v0.5.0-17-gb43389e` on
  `prod-genua`) back to `v0.3.4`. `backup_kedge_repo` default is now the
  public GitHub mirror `https://github.com/swaldtmann/kedge.git` instead of
  `https://git.authbox.de/stephan/kedge.git` — the Forge requires a login and
  hosts have no credentials. Existing checkouts are re-pointed automatically
  (`ansible.builtin.git` runs `git remote set-url origin` when `repo` differs
  from the checkout's `origin`); `force: false` is kept, so local
  modifications in `/opt/kedge` still fail the task instead of being
  overwritten. Sites can override `backup_kedge_repo`/`backup_kedge_version`.

### Fixed
- **"Clone kedge repository" can no longer hang.** The task now sets
  `GIT_TERMINAL_PROMPT=0`; an unreadable repo fails immediately with
  `terminal prompts disabled` instead of waiting for a username (an
  `ansible-playbook --check --diff` run hung 11 minutes on it).

## [0.8.4] - 2026-10-01

### Added
- **CrowdSec signal sharing off by default** — new role default
  `crowdsec_signal_sharing: false` (`roles/traefik`). The role renders
  `crowdsec/config.yaml.local` (`api.server.online_client.sharing`), mounted
  read-only into the crowdsec container; the community blocklist is still
  pulled (no `DISABLE_ONLINE_API`). The deploy role force-recreates crowdsec
  when the file changes. Needs CrowdSec >= v1.6.4; CrowdSec's docs note the
  sharing setting can change how many IPs the community blocklist delivers
  (plan-dependent).
- **14-day log retention** — Traefik access log and journald are now
  bounded. `roles/traefik` deploys `/etc/logrotate.d/drayve-traefik`
  (daily, `rotate` = `traefik_access_log_retention_days` - 1 = 13,
  `maxage 14`, USR1 to the `traefik` container); the log path is derived
  from the compose project volume `drayve_traefik_logs` under Docker's
  data-root (`docker_daemon_extra['data-root']`, default `/var/lib/docker`),
  overridable via `traefik_access_log_path`. `roles/common` sets journald
  `MaxRetentionSec` from `journald_max_retention_sec` (default `14day`).
  `roles/backup` gains `backup_kedge_exclude_volumes` (rendered as
  `BACKUP_EXCLUDE_VOLUMES`, default `traefik_logs`) so the access logs no
  longer end up in restic snapshots; the misleading comment on
  `backup_kedge_exclude_mounts` is corrected. Behaviour change on existing
  hosts: journald restarts once (handler) and old journal/access-log data
  beyond 14 days is deleted.
- **Opt-in `X-Clacks-Overhead: GNU Terry Pratchett` response header** — new
  `stack.yaml` key `headers.clacks` (bool, default `false`). When `true`, the
  traefik role writes `traefik/config/dynamic/clacks.yml` (middleware
  `clacks@file`, `customResponseHeaders`) and the static config attaches it
  to the `websecure` entrypoint, so every router gets the header (same
  mechanism as `crowdsec@file`). With the default (`false`) the rendered
  `traefik.yml` stays byte-identical to v0.8.3 and no `clacks.yml` is
  written (a stale one is removed). `scripts/validate-stack.py` rejects a
  non-boolean value. Replaces hand-editing `traefik.yml` after each deploy.

## [0.8.3] - 2026-09-25

### Added
- **Optional HTTP Basic-Auth + host label for the Promtail Loki client**
  — lets Promtail push to an external Loki behind Basic-Auth (e.g. nginx),
  with several hosts writing into the same instance distinguishable by an
  `external_labels.host` value. New `stack.yaml` keys
  `monitoring.loki_basic_auth_user` / `monitoring.loki_host_label` (flat
  equivalents `monitoring_loki_basic_auth_user` / `monitoring_loki_host_label`,
  `roles/monitoring/defaults`), password in `secrets.yml` as
  `monitoring_loki_basic_auth_password` (`roles/secrets`) — never in
  `stack.yaml` or rendered config. The password is written to a dedicated
  file (`monitoring/promtail/loki-basic-auth-password`, mode `0600`) and
  referenced via Promtail's `basic_auth.password_file`
  (`roles/monitoring/templates/promtail-config.yml.j2`); no plaintext
  password ever appears in the rendered config or `docker-compose.yml`.
  Hard requirement, same idiom as `docker_daemon_extra` (v0.8.2): with both
  vars unset (the default), the rendered `promtail-config.yml` and the
  Promtail service block in `docker-compose.yml` stay byte-identical to
  v0.8.2 — Promtail force-recreates on any config change
  (`roles/monitoring/tasks/main.yml`), so drift here would restart Promtail
  on every existing Drayve host on its next deploy. Proven via render
  comparisons against the frozen v0.8.2 templates, unset and set
  (`tests/python/test_promtail_template.py`).

## [0.8.2] - 2026-09-25

### Added
- **`docker_daemon_extra` — host-specific `/etc/docker/daemon.json` overrides**
  — new `docker_registry_mirrors`-sibling var (`roles/docker/defaults`,
  empty dict by default) merged over the base daemon.json (`log-driver`,
  `log-opts`, optional `registry-mirrors`) via `combine`. Lets a consumer
  set host-specific Docker Engine settings (e.g. a non-default
  `data-root`, `runtimes.nvidia`) without forking the template. Hard
  requirement: with `docker_daemon_extra` empty (the default), the
  rendered daemon.json is byte-identical to the pre-existing output —
  the role restarts Docker whenever the file changes
  (`ansible/roles/docker/tasks/main.yml`), so any drift here would
  restart Docker on every existing Drayve host on its next deploy. Proven
  via a render comparison against the frozen v0.8.1 template, both
  without and with `registry-mirrors` set
  (`tests/python/test_docker_daemon_template.py`).

### Fixed
- **cadvisor scraped every cgroup on the host, not just Docker containers**
  (EWH-W-173 Folgebefund, 2026-09-22) — the cadvisor service had no
  `command:`, so its default flags applied and it exposed metrics for every
  cgroup on the host (systemd slices, kernel cgroups), not just Docker
  containers. On prod-cloud (`monitoring.profile: full`) this produced 75
  distinct `id` label values for ~15-18 actual containers and 4.463 active
  Prometheus series (57 % of the host's total), driving Prometheus memory
  from 233 MiB to 642 MiB within 40 h after the EWH-W-173 cadvisor OOM fix
  made cadvisor stable enough to be scraped continuously for the first
  time. Added `--docker_only=true` to the cadvisor command
  (`ansible/templates/docker-compose.yml.j2`) to scope it to Docker
  containers only. Checked all `stack.yaml` consumers before the template
  change: `drayve-prod-genua` has cadvisor explicitly off, `drayve-ticket`
  (`profile: none`), `drayve-greenfield` (`profile: light`) and
  `drayve-rezepte` (`profile: minimal`) never enable cadvisor — only
  `ewh-stack` (prod-cloud, `profile: full`) is affected.

## [0.8.1] - 2026-09-10

### Added
- **Optional Grafana Slack contact point for severity=critical alerts**
  (KIG-W-054 Schritt 2b) — new secret var `grafana_slack_token`
  (`roles/secrets`) and `grafana_alert_slack_channel` (plain var) enable
  `SLACK_TOKEN` on the Grafana container and deploy a `type: slack` contact
  point (`contact-points-slack-critical.yaml`, bot `reggi_woos_ki`), gated
  on `grafana_slack_token` — same off-by-default idiom as the webhook/SMTP
  contact points. Fails fast if the token is set but
  `grafana_alert_slack_channel` is empty. The AFKI-W-239 cross-tag
  delete-guard is extended to protect the new file.

### Fixed
- **`policies.yaml` root routing never followed a host off the retired
  `kigulls-api-webhook` path** — the only task that ever deployed
  `policies.yaml` was gated on the legacy `grafana_alert_api_token`. A host
  that migrated to SMTP-only (KIG-W-054, v0.8.0, token unset) got no
  `policies.yaml` at all and silently fell back to Grafana's built-in
  default receiver instead of `grafana-smtp-email`. A new, independent
  policy-deploy task now fires whenever `grafana_smtp_host` OR
  `grafana_slack_token` is set: root receiver is `grafana-smtp-email` when
  SMTP is configured, otherwise Grafana's own built-in default
  (`grafana-default-email` — never an undefined receiver); a
  severity=critical route to `slack-critical` with `continue: true` is
  added when Slack is configured. The webhook-path removal task is refined
  so it only deletes `policies.yaml` when webhook, SMTP and Slack are all
  unset, and the AFKI-W-239 guard is extended to match. Additive: the
  webhook contact point/policy and the SMTP/Slack contact points themselves
  are untouched.

## [0.8.0] - 2026-09-09

### Added
- **Optional Grafana alert email contact point via SMTP** (KIG-W-054) — new
  secret vars `grafana_smtp_host`/`grafana_smtp_port`/`grafana_smtp_user`/
  `grafana_smtp_password`/`grafana_smtp_from_address` (`roles/secrets`) and
  `grafana_alert_email_to` (plain var) enable `GF_SMTP_*` on the Grafana
  container and deploy a `type: email` contact point
  (`contact-points-email.yaml`), gated on `grafana_smtp_host` — same
  off-by-default idiom as the existing `kigulls-api-webhook` contact point
  (`grafana_alert_api_token`). Additive: the webhook contact point and
  `policies.yaml` routing are untouched, no host sees a behavior change
  without setting `grafana_smtp_host`. Fails fast (`ansible.builtin.assert`)
  if SMTP is enabled but `grafana_alert_email_to` is empty. The AFKI-W-239
  cross-tag delete-guard (oops-0096) is extended to also protect
  `contact-points-email.yaml` from being deleted by a `--tags monitoring`
  run that skipped `secrets`. Part of the KIgulls-Schwarm-Rueckbau — the
  webhook path itself is retired in a separate step.

## [0.7.5] - 2026-07-31

### Fixed
- **`playbooks/deploy.yml` never ran the `common` role** (EWH-W-139
  Nachtrag) — `common` (journald, SSH hardening, UFW, swap, drayve
  user/dirs) only ran during `make provision` (first deploy). Any
  template fix landing in `common` afterwards — including the 0.7.4
  `journald_forward_to_syslog` fix — silently never reached an
  already-provisioned host again on ordinary `make deploy-prod` runs.
  Live-verified on `prod-genua`: after a clean 0.7.4 deploy,
  `/etc/systemd/journald.conf` still had `ForwardToSyslog` commented out.
  `common` is now the first role in `deploy.yml`'s `Deploy` play — all its
  tasks are idempotent (apt present-state, guarded swap creation, UFW
  allow-rules, lineinfile), so re-applying on every deploy is a no-op once
  a host is in the desired state, and corrects real config drift when it
  isn't.

## [0.7.4] - 2026-07-31

### Fixed
- **journald no longer duplicates container logs into `/var/log/syslog`**
  (EWH-W-139) — the `docker` role fixes `log-driver=journald`, but nothing
  ever disabled journald's own `ForwardToSyslog` (systemd default is
  enabled when unset). Every container log line existed twice on disk: once
  in journald (with its own documented retention), once in an undocumented
  `/var/log/syslog` copy. New `journald_forward_to_syslog` var (default
  `false`) in the `common` role sets `ForwardToSyslog=no`. Verified on
  `prod-cloud`/`prod-genua` no consumer (CrowdSec, Promtail) reads
  container lines from `/var/log/syslog` — safe to disable.

## [0.7.3] - 2026-07-31

### Fixed
- **`backup_kedge_post_hook` wiring** (KEDGE-W-012) — the kedge backup role
  templated `BACKUP_PRE_HOOK` only; `BACKUP_POST_HOOK` (kedge's native
  post-backup hook, see `backup.sh`) had no matching Ansible var or template
  line. The 0.7.1 freshness dashboard and its `kedge_backup_last_success`
  metric assumed hosts would wire `tools/backup-freshness-write` via this
  hook — nothing actually could, since the slot didn't exist. Adds
  `backup_kedge_post_hook` (default `""`, same idiom as the pre-hook var).

## [0.7.2] - 2026-07-29

### Added
- **Optional per-host Grafana alert-rules override** (DRAYVE-W-014) —
  `deploy/<host>/grafana-alert-rules.yaml` under `ops_secrets_root`, copied
  if present, removed if not (same idiom as `compose.override.yml`). Closes
  the gap that made prod-cloud's alert rules a hand-maintained,
  never-versioned host artifact outside the deploy pipeline.

## [0.7.1] - 2026-07-29

### Changed
- **Backup freshness dashboard generalized** (partial pick from
  `ewh-cloud-dashboard-fix-v0.6.4`, DRAYVE-W-014): panels rewired from
  brittle Loki log-string matching to a Prometheus freshness metric
  (`kedge_backup_last_success`), generalized from the host-specific
  `cloud_` prefix so any host wiring kedge's new
  `tools/backup-freshness-write` via `BACKUP_POST_HOOK` gets working
  panels out of the box.

## [0.7.0] - 2026-07-29

### Fixed
- **Prometheus config changes never reached the running container**
  (DRAYVE-W-012) — the config is a single-file bind mount; an atomic
  write+rename (new inode) left the running container bound to the old
  one, and the prior `/-/reload` API call re-read the (from its view
  unchanged) old file. Scrape-job/alert changes silently never applied
  until someone manually force-recreated the container. Now
  force-recreates Prometheus on config change, same pattern as
  Traefik/Authelia/CrowdSec.

## [0.6.9] - 2026-07-29

First mainline release after retiring the per-host tag lines
(`genua-grafana-alert-*`, `ewh-cloud-*`) — consumers should pin release tags
from here on (DRAYVE-W-013).

### Added
- **Optional Grafana alert-webhook contact point** (`kigulls-api-webhook`) —
  wires the `grafana_alert_api_token` secret end to end so Grafana alerting
  can reach an external alert endpoint. Off by default: empty secret means
  no alerting files, no compose change. Previously hand-copied onto genua,
  cloud, hub and kakapo; now versioned.
- **Optional node_exporter textfile collector** —
  `monitoring_node_exporter_textfile: true` mounts a host-side textfile
  directory read-only into node_exporter for custom metrics (EWH-W-128).
  Off by default, no behavior change for existing consumers.
- **`host-update.yml` hardening** (AFKI-W-158) — docker packages held during
  apt-upgrade, external domain smokes with retry instead of blind sleep,
  on-failure recovery only on genuine failed exits.
- **`require-ticket-ref` commit-msg hook** (`.githooks/`, CW-W-198).

### Fixed
- **Tag-scoped deploys could silently skip the secrets role and destroy
  state** — four cross-tag guards (AFKI-W-233/238/239/240): `.env`
  templating now fails hard when secrets facts were never loaded;
  cadvisor/node Prometheus scrape jobs are gated on their `_mon_*` service
  flags; Grafana alert deletion and CrowdSec bouncer registration are gated
  on a secrets sentinel; Authelia DB deletion is guarded against the
  unloaded-secrets false-`changed` that would wipe 2FA registrations,
  sessions and tokens.
- **stack-overview dashboard query robustness** (partial pick from
  `ewh-cloud-dashboard-fix-v0.6.4`, DRAYVE-W-013): container-down stat via
  time-since-last-seen instead of flaky offset diff, memory panel
  `sum by (name)`, log-error regex with word boundary to cut false
  positives. (The backup dashboard rewire stays out until its host-specific
  metric is generalized — DRAYVE-W-014.)

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
