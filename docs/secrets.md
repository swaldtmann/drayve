# Secrets Management

Drayve supports two modes for managing secrets: **quickstart** (auto-generated, plaintext) and **sops** (encrypted, version-controlled).

## Quickstart mode (default)

Zero-config. Secrets are auto-generated during provisioning.

```yaml
# stack.yaml
secrets:
  mode: quickstart
```

On first `make provision`, the script `secrets-generate.sh` creates `ansible/secrets/<name>.sops.yml` with random values for all services:

- `auth_basic_password` — HTTP basic auth
- `grafana_admin_pass` — Grafana admin
- `crowdsec_bouncer_key` / `crowdsec_lapi_key` — CrowdSec
- `lldap_jwt_secret` / `lldap_admin_password` — LLDAP
- `authelia_*` — Authelia session, storage, OIDC secrets
- `backup_restic_password` — Backup encryption

The file is **not encrypted** — it's gitignored and stays local. Good for development, testing, and single-admin setups.

To view your generated secrets:

```bash
cat ansible/secrets/myserver.sops.yml
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
- `~/.config/sops/age/keys.txt` — your private AGE key (never commit this)
- `.sops.yaml` — SOPS config pointing to your public key
- `ansible/secrets/myserver.sops.yml` — encrypted secrets file

### Editing secrets

```bash
# Always use sops set or sops edit — never decrypt/edit/encrypt manually
sops edit ansible/secrets/myserver.sops.yml

# Or set a single value:
sops set ansible/secrets/myserver.sops.yml '["grafana_admin_pass"]' '"newpassword"'
```

Why not `sops -d` → edit → `sops -e`? Because the re-encrypted file produces a massive git diff (every field changes). `sops edit` and `sops set` only change what you touched.

### Backup your AGE key

Your AGE private key is the only way to decrypt secrets. Back it up:

```bash
cp ~/.config/sops/age/keys.txt <safe-location>
```

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
| `backup_restic_password` | Restic | Backup repository encryption |
