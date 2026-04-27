# compose.override.yml examples

Ready-to-adapt overrides for common self-hosted services you can layer on
top of a Drayve stack. Each file is a complete, working `docker-compose`
snippet that joins Drayve's `drayve_default` network, gets TLS via the
existing Traefik instance, and is picked up by `kedge` for backups.

## How to use one

1. Copy the example into your deploy directory, renaming it:

   ```bash
   cp examples/compose-overrides/forgejo.yml deploy/<name>/compose.override.yml
   ```

2. Add the referenced secrets to `deploy/<name>/env.override`. Each example
   lists which `FOO_BAR`-style variables it expects — generate strong random
   values for passwords.

3. If you want the app on the landing page, add an `apps:` entry in
   `deploy/<name>/stack.yaml` (see `examples/stack-full.yaml`).

4. `make deploy NAME=<name>`.

## What's in here

| File | App | DB | Extras |
|------|-----|----|--------|
| `nextcloud.yml` | Nextcloud 30 | MariaDB 11 | Redis cache, CalDAV/CardDAV redirects |
| `forgejo.yml` | Forgejo 14.0.4 | PostgreSQL 16 | Git-SSH on host port 2222 |

The default `deploy/_example/compose.override.yml` (which `make init`
copies into a new deploy directory) is the Nextcloud example. If you want
to start from something else, overwrite it with one of the files here
after `make init`.

## Combining multiple

A single `compose.override.yml` can contain several services. Merge the
`services:`, `volumes:` and `networks:` blocks from any of the examples
by hand — Docker Compose treats them as one file. Pay attention to host
port conflicts (Forgejo claims tcp/2222, for example).
