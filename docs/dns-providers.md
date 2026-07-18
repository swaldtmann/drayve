# DNS-01 ACME challenge providers

Drayve's default is Traefik's **HTTP-01** challenge (`acme_challenge: http`,
the default) — needs port 80 reachable from the internet, no DNS-provider
secrets. Switch to **DNS-01** (`acme_challenge: dns`) for wildcard certs or
hosts where port 80 can't be exposed. The DNS provider defaults to `hetzner`;
switch it when your DNS lives elsewhere.

> **DRAYVE-W-011 (2026-07-18):** `acme_dns_provider` alone used to do
> nothing — no `stack.yaml`-to-Ansible-var resolve task existed for it, and
> even if it had, every Traefik router label hardcoded
> `certresolver=letsencrypt` (the HTTP-01 resolver), so the DNS-01 resolver
> (`letsencrypt-dns`, correctly configured in `traefik.yml.j2` with the
> chosen provider) was defined but never referenced anywhere. Both gaps are
> fixed — `acme_challenge: dns` is now the actual switch.

## Configuration

Four settings under `stack:` in your `stack.yaml`:

```yaml
stack:
  acme_challenge: dns                   # "http" (default) or "dns"
  acme_dns_provider: cloudflare         # any provider name lego/Traefik supports
  acme_dns_propagation_delay: 60        # seconds Traefik waits before checking
  acme_dns_env_vars:                    # env vars passed through to traefik
    - CF_DNS_API_TOKEN
```

Drayve passes the listed env vars from the host environment (loaded via
`env.override`) into the traefik container. The values themselves never
appear in `stack.yaml`.

## Common providers

| Provider     | `acme_dns_provider` | Required env vars                                          |
|--------------|---------------------|------------------------------------------------------------|
| Hetzner DNS  | `hetzner`           | `HETZNER_API_TOKEN`                                        |
| Cloudflare   | `cloudflare`        | `CF_DNS_API_TOKEN` (or `CF_API_EMAIL` + `CF_API_KEY`)      |
| Netcup       | `netcup`            | `NETCUP_CUSTOMER_NUMBER`, `NETCUP_API_KEY`, `NETCUP_API_PASSWORD` |
| AWS Route 53 | `route53`           | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| Google Cloud | `gcloud`            | `GCE_PROJECT`, `GCE_SERVICE_ACCOUNT_FILE`                  |
| DigitalOcean | `digitalocean`      | `DO_AUTH_TOKEN`                                            |
| INWX         | `inwx`              | `INWX_USERNAME`, `INWX_PASSWORD`, `INWX_SHARED_SECRET`     |

The full list with all parameters is in the upstream lego documentation:
<https://go-acme.github.io/lego/dns/>

## Propagation delay

Default is 30 seconds. Bump it when the provider is slow to publish:

| Provider | Suggested `acme_dns_propagation_delay` |
|----------|----------------------------------------|
| hetzner, cloudflare, route53 | `30` (default) |
| netcup, godaddy              | `300`          |

If you see Traefik retrying the challenge with `"propagation: time limit exceeded"`,
increase the delay.

## Putting credentials into env.override

`env.override` is loaded before `docker compose up` and can be either
plaintext or sops-encrypted (see [`secrets.md`](./secrets.md)). Drayve
detects the mode automatically.

Example for Cloudflare:

```bash
# env.override
CF_DNS_API_TOKEN=<token-with-Zone:DNS:Edit-permission>
```

Restart traefik after changing it: `make deploy NAME=<host>`.

## Switching providers on an existing stack

1. Update `stack.yaml` with the four fields above (`acme_challenge: dns` is
   the one that actually matters — the other three are inert without it).
2. Add the credentials to `env.override`.
3. `make deploy NAME=<host>` re-renders `traefik.yml` and every router's
   `certresolver` label; Traefik picks up the new resolver on restart.
4. Existing certificates remain valid until renewal; the new resolver only
   kicks in on next renewal (or for new hosts).

If you want to force a renewal immediately, delete `traefik/certs/acme-dns.json`
and restart traefik. Be mindful of Let's Encrypt rate limits.

## lego / Hetzner Cloud DNS API (DRAYVE-W-011 Fund 2 — verified, no action needed)

Hetzner retired the legacy DNS API (`dns.hetzner.com/api/v1`) in favor of the
Hetzner Cloud DNS API (`api.hetzner.cloud/v1/zones`, GA 2025-11-10). lego
added support for the new API + `HETZNER_API_TOKEN` in
[PR #2663](https://github.com/go-acme/lego/pull/2663), merged 2025-10-14,
released in **lego v4.27**. Drayve pins `traefik_image: traefik:v3.6`, which
bundles **lego v4.35.x** — well past v4.27 — and the `hetzner` env-var default
above is already `HETZNER_API_TOKEN` (not the deprecated `HETZNER_API_KEY`).
**No fix needed here**; this was a live risk at ticket-creation time
(2026-07-14) that upstream had already resolved by the time it was
investigated (2026-07-18).
