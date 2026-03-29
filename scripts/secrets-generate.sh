#!/usr/bin/env bash
# secrets-generate.sh — Generate random quickstart secrets for a Drayve host.
#
# Usage: scripts/secrets-generate.sh <name>
#
# Generates ansible/secrets/<name>.sops.yml with random values.
# Does NOT encrypt — quickstart mode keeps secrets in plaintext.

set -euo pipefail

name="${1:?Usage: $0 <name>}"
target="ansible/secrets/${name}.sops.yml"

if [ -f "$target" ]; then
    echo "!!! $target already exists — not overwriting" >&2
    exit 0
fi

mkdir -p "$(dirname "$target")"

_rand() { openssl rand -base64 32 | tr -d '/+=' | head -c "$1"; }

cat > "$target" << EOF
# Quickstart secrets for ${name} (auto-generated)
# These are NOT encrypted — for production use secrets.mode: sops

# --- Auth (basic) ---
auth_basic_password: "$(_rand 20)"

# --- Grafana ---
grafana_admin_pass: "$(_rand 20)"

# --- CrowdSec ---
crowdsec_bouncer_key: "$(_rand 32)"

# --- LLDAP ---
lldap_jwt_secret: "$(_rand 48)"
lldap_admin_password: "$(_rand 20)"

# --- Authelia ---
authelia_jwt_secret: "$(_rand 48)"
authelia_session_secret: "$(_rand 48)"
authelia_storage_encryption_key: "$(_rand 48)"
authelia_ldap_password: "$(_rand 24)"
authelia_oidc_hmac_secret: "$(_rand 48)"
authelia_oidc_grafana_secret: "$(_rand 32)"

# --- Backup ---
backup_restic_password: "$(_rand 32)"
EOF

echo "==> Created $target (quickstart secrets, not encrypted)"
