#!/usr/bin/env bash
# secrets-template.sh — Scaffold a per-host SOPS secrets file (empty template).
#
# Usage: scripts/secrets-template.sh <name>

set -euo pipefail

name="${1:?Usage: $0 <name>}"
target="deploy/${name}/secrets.yml"

if [ -f "$target" ]; then
    echo "!!! $target already exists — not overwriting" >&2
    exit 1
fi

if [ ! -d "deploy/${name}" ]; then
    echo "!!! deploy/${name}/ does not exist. Run: make init NAME=${name} DOMAIN=<domain>" >&2
    exit 1
fi

cat > "$target" << EOF
# Per-host secrets for ${name}
# Fill in values, then encrypt: sops -e -i ${target}

# --- Auth (basic) ---
auth_basic_password: ""

# --- Grafana ---
grafana_admin_pass: ""

# --- CrowdSec ---
crowdsec_bouncer_key: ""
crowdsec_lapi_key: ""

# --- LLDAP (only for auth.provider: authelia + lldap: true) ---
lldap_jwt_secret: ""
lldap_admin_password: ""

# --- Authelia (only for auth.provider: authelia) ---
authelia_jwt_secret: ""
authelia_session_secret: ""
authelia_storage_encryption_key: ""
authelia_ldap_password: ""
authelia_oidc_hmac_secret: ""
authelia_oidc_grafana_secret: ""

# --- Authentik (only for auth.provider: authentik) ---
authentik_secret_key: ""
authentik_pg_pass: ""
authentik_bootstrap_password: ""
authentik_bootstrap_token: ""
authentik_oidc_grafana_secret: ""

# --- Backup ---
backup_restic_password: ""
# Required when backup.target: kedge — restic repository URI
backup_kedge_restic_repository: ""
EOF

echo "==> Created $target"
echo "    1. Fill in values"
echo "    2. Encrypt: sops -e -i $target"
