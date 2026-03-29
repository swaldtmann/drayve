#!/usr/bin/env bash
# secrets-init.sh — Scaffold AGE key + .sops.yaml for SOPS mode.
#
# Usage: scripts/secrets-init.sh

set -euo pipefail

SOPS_CONFIG=".sops.yaml"
AGE_KEY_FILE="ansible/secrets/age-key.txt"

if [ -f "$AGE_KEY_FILE" ]; then
    echo "AGE key already exists: $AGE_KEY_FILE"
else
    if ! command -v age-keygen &>/dev/null; then
        echo "Error: age-keygen not found. Install: brew install age" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$AGE_KEY_FILE")"
    age-keygen -o "$AGE_KEY_FILE" 2>&1
    chmod 600 "$AGE_KEY_FILE"
    echo "==> Created AGE key: $AGE_KEY_FILE"
fi

AGE_PUBLIC=$(grep 'public key:' "$AGE_KEY_FILE" | awk '{print $NF}')

if [ -f "$SOPS_CONFIG" ]; then
    echo ".sops.yaml already exists"
else
    cat > "$SOPS_CONFIG" << EOF
creation_rules:
  - path_regex: ansible/secrets/.*\.sops\.yml$
    age: ${AGE_PUBLIC}
  - path_regex: ansible/secrets/.*\.sops\.json$
    age: ${AGE_PUBLIC}
EOF
    echo "==> Created $SOPS_CONFIG (AGE recipient: $AGE_PUBLIC)"
fi

echo ""
echo "Next steps:"
echo "  1. make secrets-template NAME=<host>"
echo "  2. Fill in values"
echo "  3. sops -e -i ansible/secrets/<host>.sops.yml"
