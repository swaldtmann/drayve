#!/usr/bin/env bash
# Drayve Authelia log retention — daily prune of the sign-in log.
#
# Deletes rows from `authentication_logs` (username, remote_ip, request_uri, ...)
# in the Authelia SQLite DB that are older than RETENTION_DAYS. Authelia 4.39 has
# no retention option of its own, so this runs from /etc/cron.d (roles/authelia).
#
# Safety:
#   - RETENTION_DAYS must be an integer >= 1, else it falls back to 14 (never
#     "delete everything", never SQL built from an unchecked value).
#   - Only rows whose `time` is text are compared; datetime(time) parses Authelia's
#     "YYYY-MM-DD HH:MM:SS.fff+00:00" and converts to UTC. Unparsable values
#     (NULL result) are kept.
#   - Authelia keeps the DB open: the delete waits up to SQLITE_TIMEOUT_MS for locks.
#   - No VACUUM: freed pages are reused by later inserts, the file stops growing.
#     VACUUM would need an exclusive lock on the live DB (login stalls) and free
#     disk of about the DB size; the log is small, so the gain does not pay for it.
#
# Variables via environment (the cron.d file sets the first two):
#   AUTHELIA_DB        — path to db.sqlite3 (default /opt/drayve/deploy/stack/authelia/data/db.sqlite3)
#   RETENTION_DAYS     — keep this many days (default 14)
#   SQLITE_TIMEOUT_MS  — busy timeout (default 30000)
#   SQLITE_BIN         — for tests: alternative sqlite3 (default sqlite3)
#
# Exit codes: 0 pruned / nothing to do (DB or table missing); 1 error.
# Output: one line per run on stdout (cron appends it to the log file).

set -uo pipefail

AUTHELIA_DB="${AUTHELIA_DB:-/opt/drayve/deploy/stack/authelia/data/db.sqlite3}"
SQLITE_TIMEOUT_MS="${SQLITE_TIMEOUT_MS:-30000}"
SQLITE_BIN="${SQLITE_BIN:-sqlite3}"
DEFAULT_DAYS=14
RETENTION_DAYS="${RETENTION_DAYS-}"

log() { printf '%s drayve-authelia-retention: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if ! command -v "${SQLITE_BIN}" >/dev/null 2>&1; then
    log "ERROR: sqlite3 not found (apt install sqlite3)"
    exit 1
fi

if [[ ! "${RETENTION_DAYS}" =~ ^[0-9]{1,5}$ ]] || (( 10#${RETENTION_DAYS} < 1 )); then
    [[ -z "${RETENTION_DAYS}" ]] || log "invalid RETENTION_DAYS '${RETENTION_DAYS}', using ${DEFAULT_DAYS}"
    RETENTION_DAYS="${DEFAULT_DAYS}"
fi
RETENTION_DAYS=$((10#${RETENTION_DAYS}))

if [[ ! -f "${AUTHELIA_DB}" ]]; then
    log "DB ${AUTHELIA_DB} not found, nothing to do"
    exit 0
fi

has_table=$("${SQLITE_BIN}" -cmd ".timeout ${SQLITE_TIMEOUT_MS}" "${AUTHELIA_DB}" \
    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='authentication_logs';" 2>&1)
if [[ ! "${has_table}" =~ ^[0-9]+$ ]]; then
    log "ERROR: cannot read ${AUTHELIA_DB}: ${has_table}"
    exit 1
fi
if [[ "${has_table}" == "0" ]]; then
    log "no authentication_logs table in ${AUTHELIA_DB}, nothing to do"
    exit 0
fi

out=$("${SQLITE_BIN}" -cmd ".timeout ${SQLITE_TIMEOUT_MS}" "${AUTHELIA_DB}" \
    "DELETE FROM authentication_logs
       WHERE typeof(time) = 'text'
         AND datetime(time) < datetime('now', '-${RETENTION_DAYS} days');
     SELECT changes();" 2>&1)
rc=$?
deleted="${out##*$'\n'}"
if (( rc != 0 )) || [[ ! "${deleted}" =~ ^[0-9]+$ ]]; then
    log "ERROR: delete failed (rc=${rc}): ${out//$'\n'/ }"
    exit 1
fi

log "deleted ${deleted} rows from authentication_logs older than ${RETENTION_DAYS} days"
exit 0
