#!/usr/bin/env bash
# Drayve bouncer watchdog — self-heal for the CrowdSec-Bouncer plugin.
#
# Catches two plugin failure modes:
#   (a) fail-closed after a LAPI restart    → HTTP 4xx/5xx unrelated to any decision
#   (b) hold mode after stream-state drift  → HTTP stream hangs, no response
#
# Strategy: one curl against ${URL} per tick with ${TIMEOUT}s. On failure
# (HTTP not 2xx/3xx/401 or curl timeout) a marker with the first-failure time
# is written. If the failure lasts longer than ${THRESHOLD}s, the script restarts
# Traefik via `docker compose restart traefik` and deletes the marker. Success
# deletes the marker.
#
# Idempotent — one restart per failure episode, not per tick.
# Logs to syslog (tag drayve-bouncer-watchdog), always exits 0 (timer driver).
#
# Variables via environment (the systemd service unit sets them):
#   URL        — health-check URL (apex domain is typical)
#   RESOLVE_IP — pin the probe to this IP via curl --resolve <host>:<port>:<ip>
#                (host/port derived from URL; SNI + Host header stay intact).
#                Makes the probe hit this host's Traefik even if the public DNS
#                name points elsewhere. Empty/unset = plain curl, no pinning.
#   THRESHOLD  — seconds until the restart trigger (default 60)
#   TIMEOUT    — curl --max-time (default 10)
#   STACK_DIR  — docker compose path (default /opt/drayve/deploy/stack)
#   FAIL_FILE  — path of the fail marker (default /var/run/drayve-bouncer-watchdog.fail)
#   LOGGER_BIN — for tests: alternative logger implementation (default logger)
#   CURL_BIN   — for tests: alternative curl implementation (default curl)
#   DOCKER_BIN — for tests: alternative docker implementation (default docker)
#
# Exit codes:
#   0 — all ok / marker set / restart triggered
# (never non-zero — the timer must never fail)

set -uo pipefail

URL="${URL:-https://localhost/}"
THRESHOLD="${THRESHOLD:-60}"
TIMEOUT="${TIMEOUT:-10}"
RESOLVE_IP="${RESOLVE_IP:-}"
STACK_DIR="${STACK_DIR:-/opt/drayve/deploy/stack}"
FAIL_FILE="${FAIL_FILE:-/var/run/drayve-bouncer-watchdog.fail}"
LOGGER_BIN="${LOGGER_BIN:-logger}"
CURL_BIN="${CURL_BIN:-curl}"
DOCKER_BIN="${DOCKER_BIN:-docker}"

log() {
    "${LOGGER_BIN}" -t drayve-bouncer-watchdog "$1"
}

# 2xx/3xx, plus 401: the landing router carries auth@file (Basic-Auth), which
# answers 401 to an unauthenticated probe. The bouncer plugin is a global
# entrypoint middleware and runs before router middlewares, so a 401 proves
# Traefik is up and the bouncer let the request through. 403 (bouncer
# fail-closed) and timeouts stay unhealthy.
is_healthy_status() {
    local code="$1"
    [[ "${code}" =~ ^[23][0-9][0-9]$ || "${code}" == "401" ]]
}

probe() {
    local args=(-sk --max-time "${TIMEOUT}" -o /dev/null -w "%{http_code}")
    if [[ -n "${RESOLVE_IP}" ]]; then
        local scheme="${URL%%://*}" hostport="${URL#*://}" host port
        hostport="${hostport%%/*}"
        hostport="${hostport##*@}"
        if [[ "${hostport}" =~ ^(.+):([0-9]+)$ ]]; then
            host="${BASH_REMATCH[1]}"
            port="${BASH_REMATCH[2]}"
        else
            host="${hostport}"
            if [[ "${scheme}" == "http" ]]; then port=80; else port=443; fi
        fi
        args+=(--resolve "${host}:${port}:${RESOLVE_IP}")
    fi
    "${CURL_BIN}" "${args[@]}" "${URL}" 2>/dev/null
}

restart_traefik() {
    if ( cd "${STACK_DIR}" && "${DOCKER_BIN}" compose restart traefik >/dev/null 2>&1 ); then
        log "traefik restart issued"
        return 0
    fi
    log "traefik restart FAILED — manual intervention needed"
    return 1
}

main() {
    local status curl_exit now fail_since elapsed

    status=$(probe)
    curl_exit=$?

    if [[ "${curl_exit}" -eq 0 ]] && is_healthy_status "${status}"; then
        if [[ -f "${FAIL_FILE}" ]]; then
            rm -f "${FAIL_FILE}"
            log "recovery — health restored (status=${status})"
        fi
        return 0
    fi

    now=$(date +%s)

    if [[ ! -f "${FAIL_FILE}" ]]; then
        echo "${now}" > "${FAIL_FILE}"
        log "first fail (status=${status} curl_exit=${curl_exit}) — marker set"
        return 0
    fi

    fail_since=$(cat "${FAIL_FILE}" 2>/dev/null || echo "${now}")
    elapsed=$(( now - fail_since ))

    if (( elapsed >= THRESHOLD )); then
        log "fail >${THRESHOLD}s (elapsed=${elapsed}s status=${status} curl_exit=${curl_exit}) — restarting traefik"
        restart_traefik || true
        rm -f "${FAIL_FILE}"
    fi

    return 0
}

# When sourced as a library (BATS), do not execute directly.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main
    exit 0
fi
