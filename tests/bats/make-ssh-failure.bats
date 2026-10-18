#!/usr/bin/env bats
# `make status`, `make bans` and `make unban` must exit non-zero when SSH to the
# host fails. They used to print "SSH failed" and exit 0, so scripts and CI
# could not tell a dead host from a healthy one.
#
# ssh is replaced by a stub on PATH that always exits 255 (the code ssh itself
# uses for connection errors); no real host is ever contacted. The inventory
# points at the documentation address 203.0.113.10 (RFC 5737).
#
# Run: bats tests/bats/make-ssh-failure.bats

DRAYVE_ROOT="${BATS_TEST_DIRNAME}/../.."

setup() {
    TMP=$(mktemp -d)
    mkdir -p "${TMP}/bin"
    cat > "${TMP}/bin/ssh" <<'EOF'
#!/usr/bin/env bash
exit 255
EOF
    chmod +x "${TMP}/bin/ssh"
    cat > "${TMP}/hosts.yaml" <<'EOF'
all:
  children:
    drayve:
      hosts:
        demo:
          ansible_host: 203.0.113.10
EOF
    PATH="${TMP}/bin:${PATH}"
}

teardown() {
    rm -rf "${TMP}"
}

@test "make status exits non-zero and says so when SSH fails" {
    run make -C "${DRAYVE_ROOT}" status NAME=demo INVENTORY="${TMP}/hosts.yaml"
    [ "$status" -ne 0 ]
    [[ "$output" == *"SSH failed"* ]]
}

@test "make bans exits non-zero and says so when SSH fails" {
    run make -C "${DRAYVE_ROOT}" bans NAME=demo INVENTORY="${TMP}/hosts.yaml"
    [ "$status" -ne 0 ]
    [[ "$output" == *"SSH failed"* ]]
}

@test "make unban exits non-zero and says so when SSH fails" {
    run make -C "${DRAYVE_ROOT}" unban NAME=demo IP=198.51.100.7 INVENTORY="${TMP}/hosts.yaml"
    [ "$status" -ne 0 ]
    [[ "$output" == *"SSH failed"* ]]
}
