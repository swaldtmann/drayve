# Single-App Host

Drayve was designed for multi-app hosts: a stack with several services
(Mealie, Grafana, Forgejo, …), grouped behind one Traefik with an apex
landing page that lists everything. That's still the default and the
shape that gets the most polish.

But sometimes you want one service per host — a recipes server,
nothing else. This page collects the few flips that distinguish a
single-app deploy from the multi-app default.

## When to pick this layout

- One app owns the apex domain (e.g. `https://recipes.example.com`
  serves Mealie directly, no `/landing` redirect).
- You don't want a separate `https://recipes.example.com/landing`
  index page that only points at the one app.
- You may run drayve as a vendored submodule under a per-site repo
  (`<site-repo>/vendor/drayve`) instead of cloning drayve once and
  putting `deploy/` alongside the framework.

## Configuration

### 1. Disable the landing page

In `stack.yaml`:

```yaml
services:
  landing:
    enabled: false
```

This drops the nginx `landing` container from `docker-compose.yml`,
skips template rendering, and removes the `/landing` directory on
deploy. The app's own Traefik labels then own the apex Host rule.

The `apps:` list may be empty (or omitted) on a single-app host —
without `landing` there's nothing to render it into.

### 2. Apex routing in your app's compose override

The single app needs to claim the apex Host itself. In
`deploy/<host>/compose.override.yml`:

```yaml
services:
  mealie:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.mealie.rule=Host(`${DRAYVE_DOMAIN}`)"
      - "traefik.http.routers.mealie.entrypoints=websecure"
      - "traefik.http.routers.mealie.tls=true"
      - "traefik.http.routers.mealie.tls.certresolver=letsencrypt"
      - "traefik.http.services.mealie.loadbalancer.server.port=9000"
```

Use `${DRAYVE_DOMAIN}` (provided in `.env`) so the same override works
across hosts. See `examples/compose-overrides/` for service-specific
templates you can adapt.

### 3. Vendor-submodule layout (optional)

If you keep the drayve framework as `vendor/drayve` under a per-site
repo, drayve splits its root variables so secrets stay in the site
repo while shared assets stay in the framework:

```yaml
# inventory or via -e on the make command
ops_secrets_root: "{{ playbook_dir | regex_replace('/vendor/drayve/ansible(/playbooks)?$', '') }}"
# ops_deploy_root keeps its default and resolves to <site>/vendor/drayve
```

Or, more typically, set it from your site `Makefile`:

```make
ANSIBLE := cd $(DRAYVE_DIR)/ansible && ansible-playbook -i $(CURDIR)/hosts.yaml -i inventory/

provision: vendor age-key-link
	$(ANSIBLE) playbooks/provision.yml -l $(NAME) -e drayve_name=$(NAME) \
		-e domain=$(DOMAIN) -e @$(CURDIR)/stack.yaml \
		-e ops_secrets_root=$(CURDIR)
```

`ops_secrets_root` points at the site repo (where `deploy/<host>/secrets.yml`
and `deploy/.age-key.txt` live). `ops_deploy_root` keeps its default and
resolves to `vendor/drayve` (where `ansible/templates/`, `config/`, etc.
live). No symlinks needed.

For single-repo setups (drayve cloned once with `deploy/` next to
`ansible/`), both variables fall back to `ops_repo_root` and the
distinction is invisible.

### 4. Secrets that don't fit `secrets.yml`

App-level secrets — DB passwords, API keys for tools drayve doesn't
know about — go in `deploy/<host>/env.override`. The file is appended
to the deployed `.env` and exposed to compose containers via env-var
substitution. Both plaintext (gitignored) and sops-encrypted dotenv
modes work transparently; see [Secrets → App-level secrets](secrets.md#app-level-secrets-via-envoverride).

## Worked example

A single-app host running Mealie on `recipes.example.com`:

```yaml
# stack.yaml
drayve_version: "0.1.0"

stack:
  name: recipes
  domain: recipes.example.com

provider:
  type: manual

auth:
  provider: none

monitoring:
  profile: none

security:
  crowdsec: true

secrets:
  mode: sops

services:
  landing:
    enabled: false
```

```yaml
# deploy/recipes/compose.override.yml
services:
  mealie:
    image: ghcr.io/mealie-recipes/mealie:v3.0.2
    container_name: mealie
    restart: unless-stopped
    environment:
      DB_ENGINE: postgres
      POSTGRES_SERVER: mealie-db
      POSTGRES_USER: mealie
      POSTGRES_PASSWORD: ${MEALIE_DB_PASSWORD}
      ALLOW_SIGNUP: "false"
      BASE_URL: https://${DRAYVE_DOMAIN}
    depends_on:
      mealie-db:
        condition: service_healthy
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.mealie.rule=Host(`${DRAYVE_DOMAIN}`)"
      - "traefik.http.routers.mealie.entrypoints=websecure"
      - "traefik.http.routers.mealie.tls=true"
      - "traefik.http.routers.mealie.tls.certresolver=letsencrypt"
      - "traefik.http.services.mealie.loadbalancer.server.port=9000"

  mealie-db:
    image: postgres:16-alpine
    container_name: mealie-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: mealie
      POSTGRES_USER: mealie
      POSTGRES_PASSWORD: ${MEALIE_DB_PASSWORD}
    volumes:
      - mealie-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mealie"]
      interval: 10s
      timeout: 3s
      retries: 5

volumes:
  mealie-db-data:
```

```dotenv
# deploy/recipes/env.override (sops-encrypted)
MEALIE_DB_PASSWORD=…
```

Then:

```bash
make provision NAME=recipes
```

That's it. No landing page, no second domain, one app on the apex.

## Multi-network apps and the Traefik routing trap

If your override defines a service that joins more than one Docker
network — for example, app + isolated DB net — and you let Traefik
route to it, Traefik's docker provider picks one of the backend IPs at
random. When it picks an IP on the internal-only net, requests hang at
TCP and you get a "504 gateway timeout while the container is healthy"
mystery.

Drayve handles this for you. During deploy, drayve scans your
`compose.override.yml` and, for any service that joins ≥2 networks and
has `traefik.enable=true`, injects:

```yaml
labels:
  - "traefik.docker.network=drayve_default"
```

`drayve_default` is the implicit network of the main drayve stack — the
network Traefik itself runs on. If your topology differs (e.g. you
bring your own external proxy network), set
`multinet_primary_network: my_net` in your `stack.yaml` and the
injection will use that name instead.

If you want full control, set the label yourself in your override —
drayve never overwrites a label the user has already set.

## Related

- [Quickstart](quickstart.md) — the multi-app default flow.
- [Secrets](secrets.md) — `secrets.yml` vs `env.override`.
- [Architecture](architecture.md) — why apex-routing works the way it does.
- [DNS providers](dns-providers.md) — switching the ACME DNS-01 provider.
