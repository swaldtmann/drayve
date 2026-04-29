#!/usr/bin/env bats
# Tests fuer drayve-bouncer-watchdog.
# Deckt alle Pfade in main(), is_healthy_status(), probe(), restart_traefik().
#
# Lauf: bats tests/bats/bouncer-watchdog.bats

SCRIPT="${BATS_TEST_DIRNAME}/../../ansible/roles/traefik/files/drayve-bouncer-watchdog.sh"

setup() {
    TMP=$(mktemp -d)
    export FAIL_FILE="${TMP}/fail"
    export STACK_DIR="${TMP}/stack"
    export THRESHOLD=60
    export TIMEOUT=5
    export URL="https://test.invalid/"

    # Mock-Bins erzeugen — werden statt curl/logger/docker aufgerufen.
    mkdir -p "${TMP}/bin"
    cat > "${TMP}/bin/mock-logger" <<'EOF'
#!/usr/bin/env bash
echo "LOG: $*" >> "${TMP}/log.out"
EOF
    cat > "${TMP}/bin/mock-curl-200" <<'EOF'
#!/usr/bin/env bash
echo -n "200"
exit 0
EOF
    cat > "${TMP}/bin/mock-curl-403" <<'EOF'
#!/usr/bin/env bash
echo -n "403"
exit 0
EOF
    cat > "${TMP}/bin/mock-curl-301" <<'EOF'
#!/usr/bin/env bash
echo -n "301"
exit 0
EOF
    cat > "${TMP}/bin/mock-curl-timeout" <<'EOF'
#!/usr/bin/env bash
echo -n "000"
exit 28
EOF
    cat > "${TMP}/bin/mock-docker-ok" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    cat > "${TMP}/bin/mock-docker-fail" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "${TMP}/bin/"*
    export TMP
    export LOGGER_BIN="${TMP}/bin/mock-logger"
    mkdir -p "${STACK_DIR}"
}

teardown() {
    rm -rf "${TMP}"
}

# ===== probe() / is_healthy_status() =====

@test "is_healthy_status: 200 is healthy" {
    source "${SCRIPT}"
    is_healthy_status 200
}

@test "is_healthy_status: 299 is healthy" {
    source "${SCRIPT}"
    is_healthy_status 299
}

@test "is_healthy_status: 301 is healthy (redirect)" {
    source "${SCRIPT}"
    is_healthy_status 301
}

@test "is_healthy_status: 399 is healthy" {
    source "${SCRIPT}"
    is_healthy_status 399
}

@test "is_healthy_status: 100 is NOT healthy" {
    source "${SCRIPT}"
    ! is_healthy_status 100
}

@test "is_healthy_status: 400 is NOT healthy" {
    source "${SCRIPT}"
    ! is_healthy_status 400
}

@test "is_healthy_status: 403 is NOT healthy" {
    source "${SCRIPT}"
    ! is_healthy_status 403
}

@test "is_healthy_status: 500 is NOT healthy" {
    source "${SCRIPT}"
    ! is_healthy_status 500
}

@test "is_healthy_status: 000 (curl timeout) is NOT healthy" {
    source "${SCRIPT}"
    ! is_healthy_status 000
}

# ===== main(): healthy paths =====

@test "main: healthy + no marker → noop" {
    export CURL_BIN="${TMP}/bin/mock-curl-200"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
}

@test "main: healthy 301 + no marker → noop" {
    export CURL_BIN="${TMP}/bin/mock-curl-301"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
}

@test "main: healthy + existing marker → marker removed, recovery log" {
    echo "1000" > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-200"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "recovery" "${TMP}/log.out"
}

# ===== main(): unhealthy paths =====

@test "main: 403 fail-closed + no marker → marker created" {
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ -f "${FAIL_FILE}" ]
    grep -q "first fail" "${TMP}/log.out"
    [ "$(cat "${FAIL_FILE}")" -gt 0 ]
}

@test "main: curl timeout + no marker → marker created" {
    export CURL_BIN="${TMP}/bin/mock-curl-timeout"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ -f "${FAIL_FILE}" ]
    grep -q "first fail" "${TMP}/log.out"
}

@test "main: 403 + recent marker (< threshold) → no restart" {
    # Marker 10s alt, threshold 60s → noch nicht restart
    echo $(( $(date +%s) - 10 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-fail" # darf nicht aufgerufen werden
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ -f "${FAIL_FILE}" ]
    ! grep -q "restarting traefik" "${TMP}/log.out"
}

@test "main: 403 + alter marker (>= threshold) → restart traefik, marker weg" {
    # Marker 120s alt, threshold 60s → restart
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-ok"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restarting traefik" "${TMP}/log.out"
    grep -q "traefik restart issued" "${TMP}/log.out"
}

@test "main: 403 + alter marker + docker fail → log + marker weg" {
    # Restart-Versuch scheitert: trotzdem Marker geloescht (eine Episode)
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-fail"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restart FAILED" "${TMP}/log.out"
}

@test "main: curl timeout + alter marker → restart traefik" {
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-timeout"
    export DOCKER_BIN="${TMP}/bin/mock-docker-ok"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restarting traefik" "${TMP}/log.out"
}

# ===== Robustheit =====

@test "main: korrupter Marker (leer) → defensiv restart bei fail" {
    # Leerer Marker → fail_since=0 → elapsed=now (gross) → restart triggert.
    # Defensives Verhalten: lieber einmal zuviel restarten als ewig haengen.
    echo "" > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-ok"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restarting traefik" "${TMP}/log.out"
}

@test "main: ENV-Defaults greifen wenn nichts gesetzt" {
    # Kein FAIL_FILE/THRESHOLD/...: curl ueber default URL geht ins leere
    unset FAIL_FILE THRESHOLD TIMEOUT STACK_DIR URL
    export CURL_BIN="${TMP}/bin/mock-curl-200"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
}
