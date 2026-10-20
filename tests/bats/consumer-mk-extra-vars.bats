#!/usr/bin/env bats
# Tests for consumer.mk EXTRA_ANSIBLE_VARS pass-through.
#
# Before the fix, deploy-check/deploy-check-fast did not reference $(EXTRA_ANSIBLE_VARS)
# — a config override (e.g. auth:none) never reached the validate run,
# the fast check always validated the stack.yaml default. Only deploy-prod had
# the var already (local override in ewh-stack/Makefile).
#
# Builds a throwaway consumer fixture (Makefile + hosts.yaml + stack.yaml) and
# really calls deploy-check-fast locally (connection: local in validate.yml,
# no SSH/no secrets needed) — checks in the Ansible debug output whether
# EXTRA_ANSIBLE_VARS actually overrides auth.provider. For
# deploy-check/deploy-prod (which touch deploy.yml/--check, partly
# KNOWN-BROKEN in v0.4.x, see the consumer.mk comment) a `make -n`
# dry run is enough; it only checks that the variable is passed into the command line,
# without risking the playbook run itself.
#
# Override syntax is file-based (-e @override.yml), not inline JSON —
# that is the pattern actually proven in practice (inline
# `-e {"auth":{"provider":"none"}}` breaks on shell quoting as soon as Make
# hands the recipe line to /bin/sh; not a regression target of this ticket).
#
# Run: bats tests/bats/consumer-mk-extra-vars.bats

DRAYVE_ROOT="${BATS_TEST_DIRNAME}/../.."

setup() {
    TMP=$(mktemp -d)
    FIXTURE="${TMP}/consumer"
    mkdir -p "${FIXTURE}"

    ln -s "${DRAYVE_ROOT}/ansible/inventory/group_vars" "${FIXTURE}/group_vars"

    cat > "${FIXTURE}/hosts.yaml" <<EOF
all:
  children:
    drayve:
      hosts:
        test-target:
          drayve_domain: test.invalid
          drayve_hostname: test-target
EOF

    # authelia without lldap uses the file user backend (v0.8.12): validate aborts
    # without at least one auth.users entry and a matching argon2 hash in the
    # host secrets. Dummy values, never used against a real service.
    cat > "${FIXTURE}/stack.yaml" <<EOF
auth:
  provider: authelia
  users:
    - name: tester
      displayname: Tester
      email: tester@test.invalid
      groups: [admins]
EOF

    mkdir -p "${FIXTURE}/deploy/test-target"
    cat > "${FIXTURE}/deploy/test-target/secrets.yml" <<'EOF'
authelia_user_password_hashes:
  tester: "$argon2id$v=19$dummy"
EOF

    cat > "${FIXTURE}/override.yml" <<EOF
auth:
  provider: none
EOF

    cat > "${FIXTURE}/Makefile" <<EOF
NAME := test-target
DRAYVE_REF := test
DRAYVE_DIR := ${DRAYVE_ROOT}

vendor:
	@true

-include \$(DRAYVE_DIR)/ansible/consumer.mk
EOF
}

teardown() {
    rm -rf "${TMP}"
}

@test "deploy-check-fast without EXTRA_ANSIBLE_VARS validates against the stack.yaml default (regression)" {
    cd "${FIXTURE}"
    run make deploy-check-fast
    [ "$status" -eq 0 ]
    [[ "$output" == *"auth          = authelia"* ]]
}

@test "deploy-check-fast WITH EXTRA_ANSIBLE_VARS validates against the override, not stack.yaml" {
    cd "${FIXTURE}"
    run make deploy-check-fast EXTRA_ANSIBLE_VARS="-e @${FIXTURE}/override.yml"
    [ "$status" -eq 0 ]
    [[ "$output" == *"auth          = none"* ]]
    [[ "$output" != *"auth          = authelia"* ]]
}

@test "deploy-check passes EXTRA_ANSIBLE_VARS through to the Ansible command line" {
    cd "${FIXTURE}"
    run make -n deploy-check EXTRA_ANSIBLE_VARS="-e @${FIXTURE}/override.yml"
    [ "$status" -eq 0 ]
    [[ "$output" == *"-e @${FIXTURE}/override.yml"* ]]
}

@test "deploy-prod still passes EXTRA_ANSIBLE_VARS through (already worked before the fix)" {
    cd "${FIXTURE}"
    run make -n deploy-prod CONFIRM=y EXTRA_ANSIBLE_VARS="-e @${FIXTURE}/override.yml"
    [ "$status" -eq 0 ]
    [[ "$output" == *"-e @${FIXTURE}/override.yml"* ]]
}

# pre-snapshot: PROD_SNAPSHOT is an optional command, empty = no snapshot.

@test "pre-snapshot with empty PROD_SNAPSHOT prints a notice and exits 0" {
    cd "${FIXTURE}"
    run make pre-snapshot PROD_SNAPSHOT=
    [ "$status" -eq 0 ]
    [[ "$output" == *"PROD_SNAPSHOT not set"* ]]
}

@test "pre-snapshot calls PROD_SNAPSHOT as <cmd> <host> --reason <text>" {
    cd "${FIXTURE}"
    run make pre-snapshot PROD_SNAPSHOT="echo SNAP"
    [ "$status" -eq 0 ]
    [[ "$output" == *"SNAP test-target --reason drayve-deploy-test"* ]]
}

@test "pre-snapshot honours SKIP_SNAPSHOT" {
    cd "${FIXTURE}"
    run make pre-snapshot PROD_SNAPSHOT="echo SNAP" SKIP_SNAPSHOT=1
    [ "$status" -eq 0 ]
    [[ "$output" == *"SKIP_SNAPSHOT"* ]]
    [[ "$output" != *"SNAP test-target"* ]]
}

@test "consumer.mk has no hard-wired default for PROD_SNAPSHOT" {
    run grep -nE '^PROD_SNAPSHOT[[:space:]]*\?=[[:space:]]*$' "${DRAYVE_ROOT}/ansible/consumer.mk"
    [ "$status" -eq 0 ]
}
