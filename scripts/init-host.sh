#!/usr/bin/env bash
# init-host.sh — Scaffold a new host directory in deploy/.
#
# Usage: scripts/init-host.sh <name> <domain> [host_ip]
#
# Creates:
#   deploy/<name>/stack.yaml   (from example)
#   deploy/hosts.yaml          (creates or appends)

set -euo pipefail

name="${1:?Usage: $0 <name> <domain> [host_ip]}"
domain="${2:?Usage: $0 <name> <domain> [host_ip]}"
host_ip="${3:-}"

deploy_dir="deploy/${name}"
hosts_file="deploy/hosts.yaml"

# --- Guard: don't overwrite ---
if [ -d "$deploy_dir" ]; then
    echo "!!! deploy/${name}/ already exists — not overwriting" >&2
    exit 1
fi

# --- Create host directory ---
mkdir -p "$deploy_dir"

# --- Copy and customize stack.yaml ---
# Use minimal example as default (safe). Full example in deploy/_example/.
sed -e "s/name: myserver/name: ${name}/" \
    -e "s/domain: example.com/domain: ${domain}/" \
    examples/stack-minimal.yaml > "${deploy_dir}/stack.yaml"

echo "==> Created ${deploy_dir}/stack.yaml"

# --- Create or update hosts.yaml ---
if [ ! -f "$hosts_file" ]; then
    cp deploy/_example/hosts.yaml "$hosts_file"
    echo "==> Created ${hosts_file}"
fi

# Determine ansible_host value
if [ -n "$host_ip" ]; then
    ah="$host_ip"
else
    ah="<IP>"
fi

# Append host entry (before the final empty line or at end)
# Check if host already exists
if grep -q "^        ${name}:" "$hosts_file" 2>/dev/null; then
    echo "==> Host ${name} already in ${hosts_file} — skipping"
else
    cat >> "$hosts_file" << EOF
        ${name}:
          ansible_host: ${ah}
          ansible_user: root
          drayve_domain: ${domain}
          drayve_hostname: ${name}
EOF
    echo "==> Added ${name} to ${hosts_file}"
fi

# --- Summary ---
echo ""
echo "Next steps:"
if [ "$ah" = "<IP>" ]; then
    echo "  1. Set ansible_host in ${hosts_file}"
fi
echo "  2. Edit ${deploy_dir}/stack.yaml to match your needs"
echo "  3. make provision NAME=${name}"
