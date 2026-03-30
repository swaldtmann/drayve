#!/usr/bin/env bash
# secrets-generate.sh — Generate random quickstart secrets for a Drayve host.
#
# Usage: scripts/secrets-generate.sh <name>
#
# Generates deploy/<name>/secrets.yml with random values.
# Does NOT encrypt — quickstart mode keeps secrets in plaintext.

set -euo pipefail

name="${1:?Usage: $0 <name>}"
target="deploy/${name}/secrets.yml"

if [ -f "$target" ]; then
    echo "!!! $target already exists — not overwriting" >&2
    exit 0
fi

if [ ! -d "deploy/${name}" ]; then
    echo "!!! deploy/${name}/ does not exist. Run: make init NAME=${name} DOMAIN=<domain>" >&2
    exit 1
fi

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
crowdsec_lapi_key: "$(_rand 32)"

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

# --- LLDAP user passwords (from stack.yaml lldap_users[].password_var) ---
stack_file="deploy/${name}/stack.yaml"
if [ -f "$stack_file" ]; then
    password_vars=$(grep 'password_var:' "$stack_file" | sed 's/.*password_var:[[:space:]]*//' | tr -d '"'"'" || true)
    if [ -n "$password_vars" ]; then
        echo "" >> "$target"
        echo "# --- LLDAP Users ---" >> "$target"
        while IFS= read -r var; do
            echo "${var}: \"$(_rand 20)\"" >> "$target"
        done <<< "$password_vars"
    fi
fi

echo "==> Created $target (quickstart secrets, not encrypted)"
