#!/usr/bin/env bats
# Tests fuer consumer.mk EXTRA_ANSIBLE_VARS-Durchreichung.
#
# Vor dem Fix referenzierten deploy-check/deploy-check-fast $(EXTRA_ANSIBLE_VARS)
# nicht — ein Config-Override (z.B. auth:none) landete nie im Validate-Lauf,
# der Fast-Check pruefte immer den stack.yaml-Default. Nur deploy-prod hatte
# das Var schon (lokaler Override in ewh-stack/Makefile).
#
# Baut eine Wegwerf-Consumer-Fixture (Makefile + hosts.yaml + stack.yaml) und
# ruft deploy-check-fast echt lokal auf (connection: local in validate.yml,
# kein SSH/keine Secrets noetig) — prueft im Ansible-Debug-Output, ob
# EXTRA_ANSIBLE_VARS tatsaechlich den auth.provider ueberschreibt. Fuer
# deploy-check/deploy-prod (die deploy.yml/--check anfassen, teils
# KNOWN-BROKEN in v0.4.x, s. consumer.mk-Kommentar) reicht ein `make -n`
# Dry-Run, der nur die Variable-Durchreichung in die Kommandozeile prueft,
# ohne den Playbook-Lauf selbst zu riskieren.
#
# Override-Syntax ist datei-basiert (-e @override.yml), nicht inline-JSON —
# das ist das real erprobte Muster (inline
# `-e {"auth":{"provider":"none"}}` zerbricht an Shell-Quoting sobald Make
# die Recipe-Zeile an /bin/sh uebergibt; kein Regressions-Ziel dieses Tickets).
#
# Lauf: bats tests/bats/consumer-mk-extra-vars.bats

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

@test "deploy-check-fast ohne EXTRA_ANSIBLE_VARS validiert gegen stack.yaml-Default (Regression)" {
    cd "${FIXTURE}"
    run make deploy-check-fast
    [ "$status" -eq 0 ]
    [[ "$output" == *"auth          = authelia"* ]]
}

@test "deploy-check-fast MIT EXTRA_ANSIBLE_VARS validiert gegen den Override, nicht gegen stack.yaml" {
    cd "${FIXTURE}"
    run make deploy-check-fast EXTRA_ANSIBLE_VARS="-e @${FIXTURE}/override.yml"
    [ "$status" -eq 0 ]
    [[ "$output" == *"auth          = none"* ]]
    [[ "$output" != *"auth          = authelia"* ]]
}

@test "deploy-check reicht EXTRA_ANSIBLE_VARS an die Ansible-Kommandozeile durch" {
    cd "${FIXTURE}"
    run make -n deploy-check EXTRA_ANSIBLE_VARS="-e @${FIXTURE}/override.yml"
    [ "$status" -eq 0 ]
    [[ "$output" == *"-e @${FIXTURE}/override.yml"* ]]
}

@test "deploy-prod reicht EXTRA_ANSIBLE_VARS weiterhin durch (bestand schon vor dem Fix)" {
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
