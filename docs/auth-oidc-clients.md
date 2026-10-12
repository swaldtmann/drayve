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

> **A custom `auth.oidc_clients` list replaces the default, it does not extend
> it.** The default list contains the `grafana` client. Grafana is always
> deployed with Authelia sign-in (login form off) when `auth.provider: authelia`,
> so once you set your own list you must list `grafana` yourself — otherwise
> nobody can sign in to Grafana. `make validate` and the deploy pre-flight abort
> with an error if the client is missing (unless Grafana is not deployed, i.e.
> `monitoring.profile` is `light`/`none`). Copy this entry into your list
> (replace `example.com` with your `stack.domain`; the secret is not part of the
> list, it comes from `authelia_oidc_grafana_secret` in `secrets.yml`):
>
> ```yaml
> - id: grafana
>   name: Grafana
>   authorization_policy: one_factor
>   consent_mode: implicit
>   claims_policy: grafana
>   redirect_uris:
>     - "https://grafana.example.com/login/generic_oauth"
>   scopes: [openid, profile, email, groups]
> ```
>
> **Keep `claims_policy: grafana` in that entry.** Without it Authelia's ID
> token carries no `groups` claim, Grafana cannot see the `lldap_admin`
> membership, and every admin silently becomes Viewer (Grafana's role mapping
> falls back to `Viewer`; no error appears). The pre-flights only warn about
> this (they do not abort), because the login itself still works.

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

1. Argon2id hash the secret on the host (file:
   `<authelia-data>/.oidc_<id>_hash`). A sha256 checksum of the plain secret
   is kept next to it (`.oidc_<id>_secret.sha256`, mode `0600`; never the
   secret itself) to detect a changed secret.
2. Reference the hash in Authelia's `clients[].client_secret`.
3. Emit the plain secret as `AUTHELIA_OIDC_<ID>_SECRET` in `.env` so the
   downstream service can read it.

**Rotating a secret:** change `authelia_oidc_<id>_secret` in `secrets.yml` and
run a normal deploy. The checksum no longer matches, so the hash is regenerated
and both sides pick up the new secret. An unchanged secret keeps its hash, so
the rendered Authelia configuration (and the container) stay untouched. No
manual deletion of `.oidc_<id>_hash` is needed any more. On the first deploy
after upgrading, the hash is regenerated once because no checksum exists yet.

The legacy variable name `authelia_oidc_grafana_secret` still works and feeds
the `id: grafana` client — existing v0.1.x deployments do not need to rename
their secret keys.

### Authorization policies / claims policies

Both are optional dicts. They render into Authelia's
`identity_providers.oidc.authorization_policies` and `claims_policies` blocks
respectively. Reference them from a client via `authorization_policy: <name>`
or `claims_policy: <name>`.

**Built-in `grafana` claims policy.** The default Grafana client references
`claims_policy: grafana`, and the policy `grafana` is always rendered:

```yaml
claims_policies:
  grafana:
    id_token: [email, name, groups, preferred_username]
```

(claim list from the official Grafana guide,
<https://www.authelia.com/integration/openid-connect/clients/grafana/>).
Merge rule: the built-in policies are merged **under** your
`auth.claims_policies`. Your own policies are added next to the built-in one;
a policy of the same name in `stack.yaml` replaces the built-in one entirely
(it is not merged claim by claim). A client therefore never references a policy
that is not rendered. Your own `auth.oidc_clients` list does **not** get
`claims_policy` automatically — set it on your `grafana` entry (see above).

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
