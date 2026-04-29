# Secrets Management

Drayve supports two modes for managing secrets: **quickstart** (auto-generated, plaintext) and **sops** (encrypted, version-controlled).

## Quickstart mode (default)

Zero-config. Secrets are auto-generated during provisioning.

```yaml
# stack.yaml
secrets:
  mode: quickstart
```

On first `make provision`, the script `secrets-generate.sh` creates `deploy/<name>/secrets.yml` with random values for all services:

- `auth_basic_password` — HTTP basic auth
- `grafana_admin_pass` — Grafana admin
- `crowdsec_bouncer_key` / `crowdsec_lapi_key` — CrowdSec
- `lldap_jwt_secret` / `lldap_admin_password` — LLDAP
- `authelia_*` — Authelia session, storage, OIDC secrets
- `backup_restic_password` — Backup encryption

The file is **not encrypted** — it's gitignored and stays local. Good for development, testing, and single-admin setups.

To view your generated secrets:

```bash
cat deploy/myserver/secrets.yml
```

## SOPS mode

For production: secrets are encrypted with AGE and safe to commit.

```yaml
# stack.yaml
secrets:
  mode: sops
```

### Initial setup

```bash
# One-time: create AGE key and .sops.yaml config
make secrets-init

# Scaffold secrets file for a host
make secrets-template NAME=myserver
```

This creates:
- `deploy/.age-key.txt` — your private AGE key (gitignored, never commit this)
- `.sops.yaml` — SOPS config pointing to your public key
- `deploy/myserver/secrets.yml` — secrets file (encrypt after filling in values)

### Editing secrets

```bash
# Always use sops set or sops edit — never decrypt/edit/encrypt manually
sops edit deploy/myserver/secrets.yml

# Or set a single value:
sops set deploy/myserver/secrets.yml '["grafana_admin_pass"]' '"newpassword"'
```

Why not `sops -d` → edit → `sops -e`? Because the re-encrypted file produces a massive git diff (every field changes). `sops edit` and `sops set` only change what you touched.

### Back up your AGE key

> **WARNING: If you lose `deploy/.age-key.txt`, your encrypted secrets are gone. Permanently. There is no recovery, no reset, no backdoor. You would have to re-generate every secret for every host and re-deploy everything.**

Back it up immediately after creation. Store it somewhere safe and separate from your repo:

```bash
# Password manager (recommended)
# Copy the contents of deploy/.age-key.txt into your password manager

# Or a separate location
cp deploy/.age-key.txt /path/to/secure/backup/

# Or an encrypted USB drive
cp deploy/.age-key.txt /Volumes/SecureUSB/drayve-age-key.txt
```

Do not store the backup next to your repo. Do not put it in the same cloud storage. The whole point of SOPS is that the encrypted secrets can live in git safely — but only as long as the key exists somewhere else.

## Generated secrets reference

| Variable | Used by | Description |
|----------|---------|-------------|
| `auth_basic_password` | Traefik | Basic auth password (user: `admin`) |
| `grafana_admin_pass` | Grafana | Admin password |
| `crowdsec_bouncer_key` | Traefik plugin | Bouncer API key |
| `crowdsec_lapi_key` | CrowdSec | Local API key |
| `lldap_jwt_secret` | LLDAP | JWT signing secret |
| `lldap_admin_password` | LLDAP | Admin password |
| `authelia_jwt_secret` | Authelia | JWT signing |
| `authelia_session_secret` | Authelia | Session encryption |
| `authelia_storage_encryption_key` | Authelia | Database encryption |
| `authelia_ldap_password` | Authelia | LDAP bind password |
| `authelia_oidc_hmac_secret` | Authelia | OIDC HMAC signing |
| `authelia_oidc_grafana_secret` | Authelia | Grafana OIDC client secret |
| `authentik_secret_key` | Authentik | Internal encryption key |
| `authentik_pg_pass` | Authentik | PostgreSQL password |
| `authentik_bootstrap_password` | Authentik | Initial admin password |
| `authentik_bootstrap_token` | Authentik | API token for zero-touch bootstrap |
| `authentik_oidc_grafana_secret` | Authentik | Grafana OIDC client secret |
| `backup_restic_password` | Restic | Backup repository encryption |

## App-level secrets via `env.override`

Drayve `secrets.yml` only covers the framework: Traefik, CrowdSec,
Authelia/Authentik, Grafana, Backup. Application-level secrets — DB
passwords, API keys for tools drayve doesn't know about — belong in
`deploy/<host>/env.override`. The file is appended verbatim to the
deployed `.env` and exposed to compose containers via standard env-var
substitution.

Two modes are supported transparently:

- **Plaintext** (gitignored): write `key=value` pairs directly. Fine for
  local or dev hosts.
- **sops-encrypted dotenv**: encrypt once, then commit the file. Drayve
  detects encrypted content at deploy time and decrypts via the age key
  in `deploy/.age-key.txt`. The role falls back to a plain file read on
  unencrypted input, so both modes work without configuration.

```bash
sops --input-type dotenv --output-type dotenv -e -i deploy/<host>/env.override
```

This is the right place for third-party API tokens (Netcup, Mailgun,
Stripe). Don't park them as gitignored plaintext in `stacks/<host>/.env.shared`
or similar — a stray `git add -A` from a wrapper script will pick them
up the moment the gitignore drifts. Encrypt once; the file becomes
git-safe.

## Authentik Bootstrap Path

When using `auth.provider: authentik`, the bootstrap process works as follows:

1. `make provision` creates the Authentik containers with `AUTHENTIK_BOOTSTRAP_PASSWORD` and `AUTHENTIK_BOOTSTRAP_TOKEN` from secrets
2. On first start, Authentik creates the `akadmin` user with the bootstrap password and an API token with the bootstrap token value
3. The post-deploy bootstrap script (`bootstrap.sh`) uses this token to configure providers, applications, and the embedded outpost via the Authentik API
4. No manual login or `docker exec` needed — everything happens via API

**Manual recovery** (if bootstrap token is lost or invalidated):

```bash
# SSH into the server
ssh <host>

# Generate a recovery key
docker exec authentik-server ak create_recovery_key 10 akadmin

# Open the recovery URL in your browser, log in, then:
# Settings → API Tokens → Create Token
# Update your secrets.yml with the new token value
```
