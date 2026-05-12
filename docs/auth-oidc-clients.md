# Declarative OIDC Clients (W-144)

Drayve renders the Authelia OIDC client list from `stack.yaml` instead of the
template hardcoding a single Grafana client. Adding Forgejo, Vaultwarden, or any
other SSO consumer no longer requires editing the rendered config on the host —
and no longer breaks silently when the next `make deploy-prod` overwrites the
manual edit.

## Schema

```yaml
auth:
  provider: authelia
  lldap: true

  oidc_clients:
    - id: grafana
      name: Grafana
      authorization_policy: one_factor
      consent_mode: implicit
      redirect_uris:
        - "https://grafana.example.com/login/generic_oauth"
      scopes: [openid, profile, email, groups]

    - id: forgejo
      name: Forgejo
      authorization_policy: forgejo_access
      consent_mode: implicit
      claims_policy: default
      redirect_uris:
        - "https://git.example.com/user/oauth2/Authelia/callback"
      scopes: [openid, profile, email, groups]

  authorization_policies:
    forgejo_access:
      default_policy: deny
      rules:
        - policy: one_factor
          subject: ["group:forgejo_users"]

  claims_policies:
    default:
      id_token: [groups, email, preferred_username, name]
```

### Client fields

| Field | Required | Default | Notes |
|---|---|---|---|
| `id` | yes | — | Lowercase identifier. Used as Authelia `client_id`, secret-var name (`authelia_oidc_<id>_secret`), and env-var name (`AUTHELIA_OIDC_<ID>_SECRET`). |
| `name` | no | `id` | Human-readable name shown in Authelia's consent screen. |
| `authorization_policy` | no | `one_factor` | Either a built-in (`one_factor` / `two_factor`) or a key from `authorization_policies`. |
| `redirect_uris` | yes | — | Whitelisted callback URLs. |
| `scopes` | no | `[openid, profile, email, groups]` | Scopes the client is allowed to request. |
| `consent_mode` | no | unset (Authelia default `explicit`) | Service-side clients without UI should set `implicit`. |
| `claims_policy` | no | unset | Reference into `claims_policies`. |
| `grant_types` | no | unset | e.g. `[authorization_code, refresh_token]`. |
| `response_types` | no | unset | e.g. `[code]`. |
| `token_endpoint_auth_method` | no | unset | e.g. `client_secret_basic`, `none`. |

### Secrets

For each client `<id>` the plain OIDC client secret must live in
`deploy/<name>/secrets.yml` under the key `authelia_oidc_<id>_secret`. Drayve
will:

1. PBKDF2-SHA512 hash the secret on the host (one-shot, file:
   `<authelia-data>/.oidc_<id>_hash`).
2. Reference the hash in Authelia's `clients[].client_secret`.
3. Emit the plain secret as `AUTHELIA_OIDC_<ID>_SECRET` in `.env` so the
   downstream service can read it.

The legacy variable name `authelia_oidc_grafana_secret` still works and feeds
the `id: grafana` client — existing v0.1.x deployments do not need to rename
their secret keys.

### Authorization policies / claims policies

Both are optional dicts. They render into Authelia's
`identity_providers.oidc.authorization_policies` and `claims_policies` blocks
respectively. Reference them from a client via `authorization_policy: <name>`
or `claims_policy: <name>`.

`subject` values are passed through verbatim — Authelia's group/oneOf syntax
(`["group:foo"]`, `[["group:a", "group:b"]]`) all works.

## Forgejo gotchas (S328-Folge12 anlass)

These caused a silent 8-day Forgejo-SSO outage and are now documented in
`examples/compose-overrides/forgejo.yml`:

1. **Forgejo's OAuth source auto-prepends `openid`.** When you run
   `forgejo admin auth add-oauth --scopes …` do *not* pass `--scopes openid`
   yourself. Drayve's schema lets you keep `openid` in `auth.oidc_clients[].scopes`
   for Authelia's side — that is correct. Only the Forgejo CLI flag is the
   pitfall.
2. **`[oauth2_client] USERNAME` in Forgejo 14 must be `email`** (or `nickname`
   / `userid`). The OIDC-spec value `preferred_username` is silently rejected.
3. **Authelia `consent_mode`** defaults to `explicit`. Service clients like
   Forgejo that don't have a "yes I agree" UI step need `implicit`.
4. **`make deploy-prod` re-templates `configuration.yml`.** Any manual edit to
   the rendered file is lost. Use the schema, not the rendered output.

## Migration from v0.1.x

Old `stack.yaml` (no `oidc_clients` block):

```yaml
auth:
  provider: authelia
  lldap: true
```

…keeps working unchanged. The role falls back to a single Grafana client.

To add Forgejo, drop the snippet above into `stack.yaml`, add
`authelia_oidc_forgejo_secret: <plain>` to `secrets.yml`, run `make deploy`.

For prod-genua specifically, see
`/Users/sw/claudes-welt/repos/drayve-prod-genua/deploy/prod-genua/authelia-restore.sh`
(obsolete after the W-144 stack.yaml migration — kept as a reference for the
four drift points discovered in S328-Folge12).
