# DNS-01 ACME challenge providers

Drayve uses Traefik's DNS-01 challenge to obtain Let's Encrypt certificates
for wildcard / non-public hosts. The provider defaults to `hetzner`; switch
it when your DNS lives elsewhere.

## Configuration

Three settings under `stack:` in your `stack.yaml`:

```yaml
stack:
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

1. Update `stack.yaml` with the three new fields above.
2. Add the credentials to `env.override`.
3. `make deploy NAME=<host>` re-renders `traefik.yml` and the compose env
   passthrough; Traefik picks up the new resolver on restart.
4. Existing certificates remain valid until renewal; the new resolver only
   kicks in on next renewal (or for new hosts).

If you want to force a renewal immediately, delete `traefik/certs/acme-dns.json`
and restart traefik. Be mindful of Let's Encrypt rate limits.
