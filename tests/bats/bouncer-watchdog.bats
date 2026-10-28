#!/usr/bin/env bats
# Tests for drayve-bouncer-watchdog.
# Covers all paths in main(), is_healthy_status(), probe(), restart_traefik().
#
# Run: bats tests/bats/bouncer-watchdog.bats

SCRIPT="${BATS_TEST_DIRNAME}/../../ansible/roles/traefik/files/drayve-bouncer-watchdog.sh"

setup() {
    TMP=$(mktemp -d)
    export FAIL_FILE="${TMP}/fail"
    export STACK_DIR="${TMP}/stack"
    export THRESHOLD=60
    export TIMEOUT=5
    export URL="https://test.invalid/"

    # Create mock bins — called instead of curl/logger/docker.
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
    # Marker 10s old, threshold 60s → no restart yet
    echo $(( $(date +%s) - 10 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-fail" # must not be called
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ -f "${FAIL_FILE}" ]
    ! grep -q "restarting traefik" "${TMP}/log.out"
}

@test "main: 403 + old marker (>= threshold) → restart traefik, marker gone" {
    # Marker 120s old, threshold 60s → restart
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-ok"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restarting traefik" "${TMP}/log.out"
    grep -q "traefik restart issued" "${TMP}/log.out"
}

@test "main: 403 + old marker + docker fail → log + marker gone" {
    # Restart attempt fails: marker is deleted anyway (one episode)
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-fail"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restart FAILED" "${TMP}/log.out"
}

@test "main: curl timeout + old marker → restart traefik" {
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-timeout"
    export DOCKER_BIN="${TMP}/bin/mock-docker-ok"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restarting traefik" "${TMP}/log.out"
}

# ===== Robustness =====

@test "main: corrupt marker (empty) → defensive restart on fail" {
    # Empty marker → fail_since=0 → elapsed=now (large) → restart triggers.
    # Defensive behaviour: better one restart too many than hanging forever.
    echo "" > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    export DOCKER_BIN="${TMP}/bin/mock-docker-ok"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "restarting traefik" "${TMP}/log.out"
}

@test "main: ENV defaults apply when nothing is set" {
    # No FAIL_FILE/THRESHOLD/...: curl against the default URL goes nowhere
    unset FAIL_FILE THRESHOLD TIMEOUT STACK_DIR URL
    export CURL_BIN="${TMP}/bin/mock-curl-200"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
}

# ===== probe(): local pinning via curl --resolve =====

@test "probe: RESOLVE_IP set → curl gets --resolve <host>:443:<ip> (https default port)" {
    cat > "${TMP}/bin/mock-curl-args" <<'EOS'
#!/usr/bin/env bash
echo "$*" > "${TMP}/curl.args"
echo -n "200"
EOS
    chmod +x "${TMP}/bin/mock-curl-args"
    export CURL_BIN="${TMP}/bin/mock-curl-args"
    export URL="https://example.test/"
    export RESOLVE_IP="127.0.0.1"
    source "${SCRIPT}"
    run probe
    [ "${status}" -eq 0 ]
    grep -q -- "--resolve example.test:443:127.0.0.1" "${TMP}/curl.args"
    grep -q -- "https://example.test/" "${TMP}/curl.args"
}

@test "probe: RESOLVE_IP set, explicit port and http → port taken from URL / 80 default" {
    cat > "${TMP}/bin/mock-curl-args" <<'EOS'
#!/usr/bin/env bash
echo "$*" > "${TMP}/curl.args"
echo -n "200"
EOS
    chmod +x "${TMP}/bin/mock-curl-args"
    export CURL_BIN="${TMP}/bin/mock-curl-args"
    export RESOLVE_IP="127.0.0.1"
    source "${SCRIPT}"
    URL="https://example.test:8443/x" run probe
    grep -q -- "--resolve example.test:8443:127.0.0.1" "${TMP}/curl.args"
    URL="http://example.test/x" run probe
    grep -q -- "--resolve example.test:80:127.0.0.1" "${TMP}/curl.args"
}

@test "probe: RESOLVE_IP empty → no --resolve (old behaviour)" {
    cat > "${TMP}/bin/mock-curl-args" <<'EOS'
#!/usr/bin/env bash
echo "$*" > "${TMP}/curl.args"
echo -n "200"
EOS
    chmod +x "${TMP}/bin/mock-curl-args"
    export CURL_BIN="${TMP}/bin/mock-curl-args"
    export URL="https://example.test/"
    export RESOLVE_IP=""
    source "${SCRIPT}"
    run probe
    [ "${status}" -eq 0 ]
    [ -f "${TMP}/curl.args" ]
    ! grep -q -- "--resolve" "${TMP}/curl.args"
    grep -q -- "https://example.test/" "${TMP}/curl.args"
}

# ===== 401 = alive: Basic-Auth sits behind the bouncer =====

@test "is_healthy_status: 401 is healthy (auth@file answers behind the bouncer)" {
    source "${SCRIPT}"
    is_healthy_status 401
}

@test "is_healthy_status: 402 and 403 stay NOT healthy" {
    source "${SCRIPT}"
    ! is_healthy_status 402
    ! is_healthy_status 403
}

@test "main: 401 + no marker → noop (no marker, no restart)" {
    cat > "${TMP}/bin/mock-curl-401" <<'EOS'
#!/usr/bin/env bash
echo -n "401"
exit 0
EOS
    chmod +x "${TMP}/bin/mock-curl-401"
    export CURL_BIN="${TMP}/bin/mock-curl-401"
    source "${SCRIPT}"
    run main
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
}

@test "main: 401 + existing marker → recovery, marker removed" {
    cat > "${TMP}/bin/mock-curl-401" <<'EOS'
#!/usr/bin/env bash
echo -n "401"
exit 0
EOS
    chmod +x "${TMP}/bin/mock-curl-401"
    export CURL_BIN="${TMP}/bin/mock-curl-401"
    date +%s > "${FAIL_FILE}"
    source "${SCRIPT}"
    run main
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "recovery" "${TMP}/log.out"
}

# ===== warm-up guard: never restart a Traefik container that was just (re)created =====
# Molecule integration idempotence flake: the watchdog timer starts in the
# traefik role, long before the stack exists. It fails the probe until the
# stack is up, and its restart tick (marker + 60 s) can land exactly while the
# deploy role runs `docker compose up -d --force-recreate traefik`. The race
# leaves the new container without a network, the next `compose up` recreates it.

# UTC timestamp N seconds ago in docker's StartedAt layout (GNU date or BSD date).
ts_ago() {
    local e=$(( $(date +%s) - $1 ))
    date -u -d "@${e}" +%Y-%m-%dT%H:%M:%S.123456789Z 2>/dev/null ||
        date -u -r "${e}" +%Y-%m-%dT%H:%M:%S.123456789Z
}

# Mock docker: `inspect` answers with $INSPECT_OUT / exits $INSPECT_RC,
# `compose ...` is recorded in $TMP/docker.calls.
make_mock_docker_warmup() {
    cat > "${TMP}/bin/mock-docker-warmup" <<'EOS'
#!/usr/bin/env bash
if [[ "$1" == "inspect" ]]; then
    [[ -n "${INSPECT_OUT:-}" ]] && echo "${INSPECT_OUT}"
    exit "${INSPECT_RC:-0}"
fi
echo "$*" >> "${TMP}/docker.calls"
exit 0
EOS
    chmod +x "${TMP}/bin/mock-docker-warmup"
    export DOCKER_BIN="${TMP}/bin/mock-docker-warmup"
}

@test "main: old marker + traefik running only 5 s → NO restart, marker reset" {
    make_mock_docker_warmup
    export INSPECT_OUT="running $(ts_ago 5)"
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    [ ! -f "${TMP}/docker.calls" ]
    ! grep -q "restarting traefik" "${TMP}/log.out"
    grep -q "no restart" "${TMP}/log.out"
}

@test "main: old marker + traefik just created (not started yet) → NO restart" {
    make_mock_docker_warmup
    export INSPECT_OUT="created 0001-01-01T00:00:00Z"
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${TMP}/docker.calls" ]
    ! grep -q "restarting traefik" "${TMP}/log.out"
}

@test "main: old marker + traefik running for 5 min → restart as before" {
    make_mock_docker_warmup
    export INSPECT_OUT="running $(ts_ago 300)"
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    run bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    [ ! -f "${FAIL_FILE}" ]
    grep -q "compose restart traefik" "${TMP}/docker.calls"
    grep -q "restarting traefik" "${TMP}/log.out"
}

@test "main: old marker + traefik exited → restart as before" {
    make_mock_docker_warmup
    export INSPECT_OUT="exited $(ts_ago 5)"
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    run bash "${SCRIPT}"
    grep -q "compose restart traefik" "${TMP}/docker.calls"
}

@test "main: old marker + no traefik container (inspect fails) → restart attempt as before" {
    make_mock_docker_warmup
    export INSPECT_RC=1
    echo $(( $(date +%s) - 120 )) > "${FAIL_FILE}"
    export CURL_BIN="${TMP}/bin/mock-curl-403"
    run bash "${SCRIPT}"
    grep -q "compose restart traefik" "${TMP}/docker.calls"
}
