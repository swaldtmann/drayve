#!/usr/bin/env bash
# Drayve Bouncer-Watchdog — Self-Heal CrowdSec-Bouncer-Plugin .
#
# Faengt zwei Plugin-Failure-Modi:
#   (a) fail-closed nach LAPI-Restart    → HTTP 4xx/5xx ohne Decision-Bezug
#   (b) Hold-Mode nach Stream-State-Drift → HTTP-Stream haengt, kein Response
#
# Strategie: pro Tick ein curl gegen ${URL} mit ${TIMEOUT}s. Bei Misserfolg
# (HTTP nicht 2xx/3xx oder curl-Timeout) wird ein Marker mit Erst-Fehlzeit
# geschrieben. Dauert der Failure ueber ${THRESHOLD}s an, restartet das Skript
# Traefik via `docker compose restart traefik` und loescht den Marker. Erfolg
# loescht den Marker.
#
# Idempotent — ein Restart pro Failure-Episode, nicht pro Tick.
# Logs nach syslog (tag drayve-bouncer-watchdog), Exit immer 0 (Timer-Driver).
#
# Variablen via Environment (systemd-Service-Unit setzt sie):
#   URL        — Health-Check-URL (Apex-Domain ueblich)
#   THRESHOLD  — Sekunden bis Restart-Trigger (Default 60)
#   TIMEOUT    — curl --max-time (Default 10)
#   STACK_DIR  — docker compose Pfad (Default /opt/drayve/deploy/stack)
#   FAIL_FILE  — Pfad fuer Fail-Marker (Default /var/run/drayve-bouncer-watchdog.fail)
#   LOGGER_BIN — fuer Tests: alternative Logger-Implementation (Default logger)
#   CURL_BIN   — fuer Tests: alternative curl-Implementation (Default curl)
#   DOCKER_BIN — fuer Tests: alternative docker-Implementation (Default docker)
#
# Exit-Codes:
#   0 — alles ok / Marker gesetzt / Restart angestossen
# (kein non-zero — der Timer soll nie fehlschlagen)

set -uo pipefail

URL="${URL:-https://localhost/}"
THRESHOLD="${THRESHOLD:-60}"
TIMEOUT="${TIMEOUT:-10}"
STACK_DIR="${STACK_DIR:-/opt/drayve/deploy/stack}"
FAIL_FILE="${FAIL_FILE:-/var/run/drayve-bouncer-watchdog.fail}"
LOGGER_BIN="${LOGGER_BIN:-logger}"
CURL_BIN="${CURL_BIN:-curl}"
DOCKER_BIN="${DOCKER_BIN:-docker}"

log() {
    "${LOGGER_BIN}" -t drayve-bouncer-watchdog "$1"
}

is_healthy_status() {
    local code="$1"
    [[ "${code}" =~ ^[23][0-9][0-9]$ ]]
}

probe() {
    "${CURL_BIN}" -sk --max-time "${TIMEOUT}" -o /dev/null -w "%{http_code}" "${URL}" 2>/dev/null
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

# Wenn als Library gesourced (BATS), nicht direkt ausfuehren.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main
    exit 0
fi
