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
| `provider` | string | `basic` | `none` — no auth. `basic` — HTTP basic auth. `authelia` — full SSO with web portal. `authelia_remote` — no Authelia on this host, use the one of another drayve host (see below). `authentik` — enterprise SSO (planned) |
| `lldap` | bool | `false` | Deploy LLDAP as user backend (authelia only) |
| `lldap_base_dn` | string | `DC=drayve,DC=local` | LDAP base DN (authelia + lldap only) |
| `access_control.default_policy` | string | `one_factor` | Authelia policy for requests no rule matches: exactly `deny`, `one_factor`, `two_factor`, `bypass` (authelia only). **Required as soon as `rules` are set** (recommended: `deny`) |
| `access_control.rules` | list | — | Authelia access rules, see below (authelia only) |
| `users` | list | — | File-backend users: `name`, `email` (required), `displayname`, `groups`. Authelia with `lldap: false` only; hashes go in `secrets.yml` |
| `cookie_domain` | string | `stack.domain` | Authelia session cookie domain (authelia only). Use a shared parent domain when other hosts below it use this Authelia |
| `portal_url` | string | `https://auth.<stack.domain>` | Authelia portal URL, rendered as `session.cookies[].authelia_url` (authelia only). `https://`, host must equal or sit below `cookie_domain` |
| `remote.forward_auth_url` | string | — | Full forward-auth endpoint of the other host's Authelia: `https://` and the path exactly `/api/authz/forward-auth` (a query is allowed). The portal root or `/api/health` is refused: they answer 200 and would let every request through. **Required** for `authelia_remote` |
| `remote.portal_url` | string | — | Portal URL of the other host's Authelia, `https://` only. **Required** for `authelia_remote`; used for the landing-page link |

#### Access control (`auth.access_control`)

Each rule has `domain` (string or list, `*.example.com` matches subdomains), `policy`, and optionally `groups` (matches members of any listed group), `resources` (path regexes) and `networks` (CIDR). `groups`, `resources` and `networks` are **lists**; a plain string is an error, not converted. **The first matching rule wins**, so put broad rules for privileged groups before narrow ones. A rule with `groups` cannot use `policy: bypass` (Authelia needs a login to know the group). Without `access_control`, Authelia keeps `default_policy: one_factor` and no rules, as before.

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

Checked by `make validate` and again before the first write of every deploy (types, policy names, group names):

- **`default_policy` is mandatory once `rules` exist.** A request that matches no rule gets the default; with the old implicit `one_factor`, a rule that failed to match (typo in a group, `*.example.com` does not cover `example.com` itself) silently dropped the second factor.
- **Groups must exist** (file backend, `lldap: false`): every group named in a rule must be in `groups` of at least one `auth.users` entry, spelled exactly the same (case-sensitive). With LLDAP the groups live outside `stack.yaml` and are not checked.
- **Anchor `resources` regexes.** `resources: ["/public"]` with `bypass` also matches `/secret?x=/public` and `/admin/public`. Write `resources: ["^/public/"]`. An unanchored `bypass` regex only produces a warning.
- Warnings (no abort): `bypass` on a wildcard domain without `groups`/`resources`; `*.example.com` without `example.com` in the same rule.
- Names (domains, groups, resources, user names) must not contain `{{` or `{%`.

Full example: `examples/stack-authelia-file.yaml`. Reference: <https://www.authelia.com/configuration/security/access-control/>.

#### File user backend (`lldap: false`)

`auth.users` plus `authelia_user_password_hashes` in `secrets.yml` (see `docs/secrets.md`) are the only source: each deploy rewrites `authelia/users/users_database.yml` (mode `0600`, the directory is mounted into the container), and Authelia reloads it (`watch: true`); a changed file also recreates the Authelia container, which ends all signed-in sessions (they are kept in memory), so a removed user is out after the deploy. Password change and password reset are switched off in Authelia, otherwise it would write the file back and the next deploy would overwrite the change. A missing user or hash, a value in `authelia_user_password_hashes` that does not start with `$argon2` (a plain password), duplicate user names (case-insensitive) or an unknown key under `auth.users[]` fails `make validate` and the deploy before anything is written. There is no `disabled` switch: to lock a user out, **remove the entry from `auth.users`**. Hash generation: `authelia crypto hash generate argon2` (command in `docs/secrets.md`). References: <https://www.authelia.com/configuration/first-factor/file/>, <https://www.authelia.com/configuration/first-factor/introduction/>.


#### One Authelia for two hosts

`authelia_remote` puts the Traefik middleware `auth@file` on a host without Authelia
against the forward-auth endpoint of an Authelia on another host. No `authelia` or
`lldap` container runs there and no Authelia secrets are needed. Users, groups, second
factor and access rules live on the Authelia host only.

Host with Authelia (`node01.stream.example.org`):

```yaml
stack:
  domain: node01.stream.example.org
auth:
  provider: authelia
  cookie_domain: stream.example.org          # shared parent of both hosts
  portal_url: https://auth.node01.stream.example.org
security:
  trusted_proxies:
    - 203.0.113.20/32                        # public IP of the host without Authelia
```

Host without Authelia (`mon.stream.example.org`):

```yaml
stack:
  domain: mon.stream.example.org
auth:
  provider: authelia_remote
  remote:
    forward_auth_url: https://auth.node01.stream.example.org/api/authz/forward-auth
    portal_url: https://auth.node01.stream.example.org
```

Notes:

- The browser session cookie is set for `cookie_domain`, so both host names must lie below it.
  The login redirect target comes from the Authelia host's `session.cookies[].authelia_url`
  (Authelia ForwardAuth takes the "Authelia URL" from the session cookie configuration,
  see [proxy authorization](https://www.authelia.com/reference/guides/proxy-authorization/));
  `remote.portal_url` on the other host is only the link on its landing page.
- Authelia only serves cookie domains it is configured for, and its access rules must
  cover the protected host names (rules are managed on the Authelia host).
- Grafana on a `authelia_remote` host gets no OIDC login: it keeps its own login form and
  admin account behind `auth@file`.
- Reachability: the Traefik of the `authelia_remote` host calls `forward_auth_url` over HTTPS,
  so that host must reach the Authelia host's Traefik.
- **The Authelia host needs the other host in `security.trusted_proxies`.** Without it its
  Traefik does not trust the public IP of the `authelia_remote` host, deletes the
  `X-Forwarded-*` headers of the forward-auth call and Authelia answers 400, so every
  visitor of the other host gets a 400 (fail-closed, but unusable). List the public IP of
  that host as `/32` (IPv6: `/128` if it calls over IPv6). Nothing is needed on the
  `authelia_remote` host. The entries are added to the always-trusted Docker range
  `172.16.0.0/12`; the setting is read at Traefik start, the deploy recreates Traefik when
  it changes. `0.0.0.0/0`, `::/0` and any prefix length 0 are rejected, as are IPv4 networks
  shorter than `/8`, IPv6 networks shorter than `/16` and IPv4-mapped IPv6 (`::ffff:0:0/96`
  and anything inside it; write such an address as plain IPv4), and
  `forwardedHeaders.insecure` cannot be set from `stack.yaml` (it would let any client
  forge the forwarded host and URI and pick the access rule Authelia evaluates).
  Residual risk: every process **on** the other host can choose which rule Authelia
  evaluates (it is trusted by its IP); a visitor of that host cannot. That includes every
  app on the other host that can be made to fetch foreign URLs (SSRF): it calls the
  Authelia host from the trusted IP and can pick the host Authelia evaluates.
- CrowdSec on the Authelia host sees the address of the other host for every forward-auth
  call (the plugin trusts only `172.16.0.0/12`, not `security.trusted_proxies`). A ban of
  that address on the Authelia host locks out all visitors of the other host.
- The `authelia_remote` host must trust the TLS certificate of the Authelia host: it calls
  `forward_auth_url` over HTTPS from inside the Traefik container, so use a public domain
  with a valid certificate (e.g. Let's Encrypt) and make sure the name resolves publicly
  (an `/etc/hosts` entry on the VM is not seen by the container). A private CA or the
  default Traefik certificate makes the call fail with a 500.
- On both providers that set `auth@file` (`authelia`, `authelia_remote`) the middleware first
  removes the request headers `Remote_User`, `Remote_Groups`, `Remote_Email` and
  `Remote_Name` (underscore) and then runs forwardAuth, so a client cannot forge them next
  to the real `Remote-User` etc. set by Authelia.
- `make validate`, `deploy-check-fast` and `deploy.yml` abort when a URL is missing, when
  `forward_auth_url` is not `https://`, when its path is not `/api/authz/forward-auth`, or when `portal_url` lies outside `cookie_domain`.

#### Probing the login page (Blackbox exporter)

With `authelia` and `authelia_remote`, Authelia answers a request **without** an
`Accept: text/html` header with `401` instead of the `302` redirect to the portal (seen on
Authelia v4.39.x, `getAuthzRedirectStatusCode` in `internal/handlers/handler_authz_util.go`;
not re-verified against every release). The Blackbox exporter sends no `Accept` header, so
a probe of a protected URL sees `401`; `curl` sends `Accept: */*` and gets the `302`. If you
probe login pages with the Blackbox exporter, set the header in the module:

```yaml
modules:
  http_login_redirect:
    prober: http
    http:
      headers:
        Accept: text/html
      no_follow_redirects: true
      valid_status_codes: [302]
```

#### Behaviour when Authelia is unavailable

The `auth@file` forwardAuth middleware is fail-closed by design: if Authelia does not
answer, Traefik returns an error and every router behind `auth@file` is unreachable,
including for users with a valid session. With `auth_provider: authelia_remote` the second
host depends on the Authelia of the first host, so it is locked out too when that host or
the link between the hosts is down. This is deliberate: fail-open would expose the protected
services. Monitor Authelia from outside (e.g. a Blackbox probe; `grafana_health_allowlist`
below gives an unauthenticated health endpoint for such a probe). A host that must stay
reachable on its own needs its own Authelia.


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

#### Grafana alert delivery (Ansible variables)

Alert delivery is off by default. These are plain Ansible variables (set them in `stack.yaml` or `hosts.yaml`; secrets in `secrets.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `grafana_alert_email_to` | — | Recipient of the alert mail. Required when `grafana_smtp_host` (secret) is set |
| `grafana_alert_slack_channel` | — | Slack channel (ID or `#name`) for severity=critical alerts. Required when `grafana_slack_token` (secret) is set |
| `grafana_alert_slack_username` | `drayve` | Display name the Slack bot posts under |

#### Loki limits and Grafana health endpoint (Ansible variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `monitoring_loki_max_entries_limit_per_query` | `5000` | Loki `limits_config.max_entries_limit_per_query`. Default = Loki upstream default of the pinned version (3.3.2). Lower it if queries over long log lines exhaust `mem_loki` |
| `monitoring_loki_max_query_series` | `500` | Loki `limits_config.max_query_series`. Default = Loki upstream default of the pinned version (3.3.2) |
| `mem_loki` | `1g` | Memory limit of the Loki container (was `512m` until 0.8.13, where one query with `limit=5000` over 16 KiB lines got Loki OOM-killed) |
| `grafana_health_allowlist` | `[]` | List of IPs/CIDRs allowed to call Grafana `/api/health` **without** `auth@file`, e.g. for a monitoring probe. Empty: no extra router, nothing changes. Set: an extra Traefik router (`grafana-health`, priority 1000) matches exactly `Host(grafana.<domain>) && Path(/api/health)` (no prefix) with an `ipAllowList` middleware; all other paths stay behind `auth@file`. Behind a proxy or CDN Traefik sees the proxy address, not the client |

### `security`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `crowdsec` | bool | `true` | Deploy CrowdSec + Traefik bouncer plugin |
| `trusted_proxies` | list of strings | `[]` | IPv4/IPv6 addresses or networks added to Traefik's `forwardedHeaders.trustedIPs` next to `172.16.0.0/12`. For the Authelia host of an `authelia_remote` pair: the other host's public IP as `/32` (see "One Authelia for two hosts"). Prefix length 0, IPv4 networks shorter than `/8`, IPv6 networks shorter than `/16` and IPv4-mapped IPv6 (`::ffff:0:0/96`, write it as IPv4) are rejected |
| `crowdsec_whitelist_expressions` | list of strings | `[]` | CrowdSec whitelist expressions (`whitelist.expression`), rendered next to the IP list `crowdsec_whitelist` into `crowdsec/whitelist.yaml`. See "CrowdSec whitelist expressions" below |

#### CrowdSec whitelist expressions

An expression is evaluated per log event; if it is true, the event never reaches
the scenarios. With Traefik's common log format (what this stack configures)
the event has the router name (`evt.Meta.traefik_router_name`), method
(`evt.Meta.http_verb`), path (`evt.Meta.http_path`) and status, but **no host
name** (`target_fqdn` is only filled from the JSON access log). Pin the router
name instead, and keep the expression as narrow as the problem.

Example: the Nextcloud frontend polls `user_status` for every user shown in the
UI (dozens of GETs per second, partly 404) and trips
`crowdsecurity/http-crawl-non_statics` and `crowdsecurity/http-probing`:

```yaml
security:
  crowdsec_whitelist_expressions:
    - >-
      evt.Meta.traefik_router_name startsWith 'nextcloud@'
      && evt.Meta.http_verb == 'GET'
      && evt.Meta.http_status in ['200','404']
      && evt.Meta.http_path matches '^/ocs/v2\\.php/apps/user_status/api/v1/statuses/[A-Za-z0-9._@-]+$'
```

Why so narrow: `evt.Meta.http_path` is the raw request path including the query
string, and an event that is whitelisted is invisible to *all* scenarios,
including the CVE and path-traversal ones. The anchored pattern therefore allows
only a plain user name after the prefix (no `?`, no `%`, no further `/`), and the
status is limited to 200/404 so 401/403 floods keep counting. User names with
other characters fall outside the exception and are counted as before (the safe
direction).

Entries must be non-empty single-line strings (the schema rejects anything
else). Before a changed whitelist file replaces the live one, the deploy
compiles it with the stack's CrowdSec image in a throw-away container
(`crowdsec -t`); a syntax or type error aborts the run with CrowdSec's message
and leaves the running container untouched. Always compare against a literal on
the right-hand side: `startsWith` with a missing field on both sides is true.

The router name is whatever your compose labels call the router
(`traefik.http.routers.<name>`; the Docker provider appends `@docker`).
A functional check against real CrowdSec lives in
`tests/crowdsec/test-whitelist-expression.sh`.

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
| `mem_*` | varies | Memory limits per container (e.g. `mem_grafana: 256m`; `mem_loki` is `1g`) |
| `authelia_reset_db_on_key_change` | `false` | Authelia: a config change never deletes `db.sqlite3` (2FA registrations, sessions). A changed `storage_encryption_key` is detected via `.storage_encryption_key.sha256` next to the DB and aborts the deploy. Set to `true` to delete the DB instead (all users must re-register their second factor) |
| `authelia_auth_log_retention_days` | `14` | Authelia (`auth.provider: authelia`): a daily `/etc/cron.d/drayve-authelia-retention` job deletes `authentication_logs` rows (username, remote IP, request URI of every sign-in attempt) older than this many days from `authelia/data/db.sqlite3`. Integer ≥ 1; any other value (`0`, text) makes the script use `14`, never "delete everything". Needs the `sqlite3` package (installed by the role). One line per run in `authelia_auth_log_retention_log_file` (default `/var/log/drayve-authelia-retention.log`); schedule via `authelia_auth_log_retention_schedule` (default `17 3 * * *`). The file is not vacuumed: freed pages are reused, the DB stops growing. Authelia 4.39 has no retention option of its own |

## Pinned images and how to bump them

Every container image drayve deploys is pinned to exact versions, not to floating tags. A floating tag lets a breaking change in unannounced: Authelia 4.39.19 arrived through the `4.39` line in 2026-05 and rejected the password hashes drayve generated at the time; a floating `lldap:stable` was involved in an auth outage in 2026-07.

| Image | Pinned to | Where the version lives | Scenario to run when bumping |
|-------|-----------|-------------------------|------------------------------|
| Traefik | `traefik:v3.7.16` | `traefik_image` in `ansible/roles/traefik/defaults/main.yml`; fallback in `ansible/templates/docker-compose.yml.j2` | `basic` |
| CrowdSec | `crowdsecurity/crowdsec:v1.8.1` | `crowdsec_image` in `ansible/inventory/group_vars/all/vars.yml`; fallback in `ansible/templates/docker-compose.yml.j2` | `basic` |
| Landing page (nginx) | `nginx:1.31.6-alpine` | `landing_image` in `ansible/inventory/group_vars/all/vars.yml`; fallback in `ansible/templates/docker-compose.yml.j2` | `basic` |
| Authelia | `authelia/authelia:4.39.28` | `authelia_image` in `ansible/roles/auth/defaults/main.yml` and `ansible/roles/authelia/defaults/main.yml`; fallback in `ansible/templates/docker-compose.yml.j2` | `authelia` |
| lldap | `lldap/lldap:v0.6.3` | `lldap_image` in `ansible/inventory/group_vars/all/vars.yml`; fallback in `ansible/templates/docker-compose.yml.j2`; `molecule/authentik-ldap/molecule.yml` | `authentik-ldap` |
| Authentik | `ghcr.io/goauthentik/server:2025.10.4` | `authentik_image` in `ansible/inventory/group_vars/all/vars.yml`; fallback in `ansible/templates/docker-compose.yml.j2`; `molecule/authentik*/molecule.yml` | `authentik` |
| Authentik Postgres | `postgres:16.15-alpine` | `authentik_pg_image` in `ansible/inventory/group_vars/all/vars.yml`; fallback in `ansible/templates/docker-compose.yml.j2`; `molecule/authentik*/molecule.yml` | `authentik` |
| Authentik Redis | `redis:7.4.11-alpine` | `authentik_redis_image` in `ansible/inventory/group_vars/all/vars.yml`; fallback in `ansible/templates/docker-compose.yml.j2`; `molecule/authentik*/molecule.yml` | `authentik` |
| Grafana | `grafana/grafana:12.4.12` | `grafana_image` in `ansible/inventory/group_vars/all/vars.yml` and every `molecule/*/molecule.yml` | `authelia` (login) |
| Prometheus | `prom/prometheus:v3.15.1` | `prometheus_image` in `ansible/inventory/group_vars/all/vars.yml` and every `molecule/*/molecule.yml` | `basic` |
| Loki | `grafana/loki:3.7.9` | `loki_image` in `ansible/inventory/group_vars/all/vars.yml` and every `molecule/*/molecule.yml` | `basic` |
| Promtail | `grafana/promtail:3.6.11` | `promtail_image` in `ansible/inventory/group_vars/all/vars.yml` and every `molecule/*/molecule.yml` | `basic` |
| node-exporter | `prom/node-exporter:v1.12.1` | `node_exporter_image` in `ansible/inventory/group_vars/all/vars.yml` and every `molecule/*/molecule.yml` | `basic` |
| cAdvisor | `ghcr.io/google/cadvisor:0.60.6` | `cadvisor_image` in `ansible/inventory/group_vars/all/vars.yml` and every `molecule/*/molecule.yml` | `basic` |

Postgres and Redis belong to Authentik: bump them only inside their major line (16.x, 7.x). A Postgres major change needs a data migration and is not a plain version bump.

To bump one:

1. Change the version in every place listed above (default, compose fallback, molecule scenarios).
2. Check the tag exists exactly like that, for `linux/amd64` and `linux/arm64`, e.g. `https://hub.docker.com/v2/repositories/<repo>/tags/<tag>` (`images[].architecture`; official images live under `library/<name>`). If one architecture is missing, do not bump.
3. Run the scenario named in the table — see `docs/testing.md`.
4. Add a line to `CHANGELOG.md` including the upgrade note for existing hosts.
5. `pytest tests/python/test_pinned_images.py` rejects `latest`, `stable`, bare `alpine`, bare major/minor lines (`4.39`, `v3.6`, `16-alpine`), literal image references in the compose template and default/fallback mismatches. A new `*_image` variable must be registered in that test.

Consequence: security fixes in these images no longer arrive by themselves. Watching upstream releases and bumping is now a manual job.

## Examples

- `deploy/_example/stack.yaml` — default template (used by `make init`)
- `examples/stack-minimal.yaml` — smallest useful config
- `examples/stack-full.yaml` — enterprise setup with Authelia, LLDAP, SOPS, storagebox backup
