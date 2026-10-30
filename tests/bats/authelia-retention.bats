#!/usr/bin/env bats
# Tests for drayve-authelia-retention: deletes authentication_logs rows older
# than RETENTION_DAYS from the Authelia SQLite DB. Uses a real throw-away DB.
#
# Run: bats tests/bats/authelia-retention.bats

SCRIPT="${BATS_TEST_DIRNAME}/../../ansible/roles/authelia/files/drayve-authelia-retention.sh"

setup() {
    TMP=$(mktemp -d)
    export AUTHELIA_DB="${TMP}/db.sqlite3"
    unset RETENTION_DAYS SQLITE_BIN SQLITE_TIMEOUT_MS
    command -v sqlite3 >/dev/null || { echo "sqlite3 missing on test host" >&2; return 1; }
}

teardown() {
    rm -rf "${TMP}"
}

# Same column layout as Authelia 4.39 (time as DATETIME text).
make_db() {
    sqlite3 "${AUTHELIA_DB}" "CREATE TABLE authentication_logs (
        id INTEGER PRIMARY KEY, time DATETIME NOT NULL, successful BOOLEAN NOT NULL,
        username VARCHAR(100) NOT NULL, remote_ip VARCHAR(47) NULL, request_uri TEXT NULL);"
}

# add_row <age-in-days> [username]  — stored like Authelia: fractional seconds + UTC offset.
add_row() {
    sqlite3 "${AUTHELIA_DB}" "INSERT INTO authentication_logs (time, successful, username, remote_ip, request_uri)
        VALUES (strftime('%Y-%m-%d %H:%M:%f', 'now', '-$1 days') || '+00:00', 1, '${2:-alice}', '192.0.2.1', '/');"
}

count_rows() {
    sqlite3 "${AUTHELIA_DB}" "SELECT count(*) FROM authentication_logs;"
}

@test "deletes rows older than RETENTION_DAYS, keeps newer ones" {
    make_db
    add_row 30 old1; add_row 15 old2; add_row 13 new1; add_row 1 new2
    RETENTION_DAYS=14 run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"deleted 2 rows"* ]]
    [ "$(count_rows)" = "2" ]
    [ "$(sqlite3 "${AUTHELIA_DB}" "SELECT group_concat(username) FROM authentication_logs ORDER BY id;")" = "new1,new2" ]
}

@test "log line names the retention window" {
    make_db
    add_row 40
    RETENTION_DAYS=7 run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"older than 7 days"* ]]
}

@test "unset RETENTION_DAYS falls back to 14 days" {
    make_db
    add_row 20; add_row 10
    run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [ "$(count_rows)" = "1" ]
    [[ "$output" == *"older than 14 days"* ]]
}

@test "RETENTION_DAYS=0 is not 'delete everything': falls back to 14 days" {
    make_db
    add_row 20; add_row 1; add_row 0
    RETENTION_DAYS=0 run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [ "$(count_rows)" = "2" ]
    [[ "$output" == *"invalid RETENTION_DAYS"* ]]
}

@test "non-numeric / negative / empty RETENTION_DAYS fall back to 14 days" {
    make_db
    add_row 20; add_row 1
    for bad in abc -5 "" "1; DROP TABLE authentication_logs" 3.5; do
        RETENTION_DAYS="$bad" run bash "${SCRIPT}"
        [ "$status" -eq 0 ]
        [[ "$output" == *"older than 14 days"* ]]
    done
    [ "$(sqlite3 "${AUTHELIA_DB}" "SELECT count(*) FROM authentication_logs;")" = "1" ]
}

@test "second run deletes nothing and says so" {
    make_db
    add_row 30; add_row 1
    run bash "${SCRIPT}"
    run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"deleted 0 rows"* ]]
    [ "$(count_rows)" = "1" ]
}

@test "rows with a non-text time value are never deleted" {
    make_db
    sqlite3 "${AUTHELIA_DB}" "INSERT INTO authentication_logs (time, successful, username) VALUES (1000, 1, 'epoch');"
    sqlite3 "${AUTHELIA_DB}" "INSERT INTO authentication_logs (time, successful, username) VALUES ('garbage', 1, 'junk');"
    run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"deleted 0 rows"* ]]
    [ "$(count_rows)" = "2" ]
}

@test "timestamps with a non-UTC offset are compared in UTC" {
    make_db
    # 13d23h ago in UTC, written with +14:00 offset: must stay (not older than 14d)
    sqlite3 "${AUTHELIA_DB}" "INSERT INTO authentication_logs (time, successful, username)
        VALUES (strftime('%Y-%m-%d %H:%M:%S', 'now', '-13 days', '-23 hours', '+14 hours') || '+14:00', 1, 'edge');"
    RETENTION_DAYS=14 run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [ "$(count_rows)" = "1" ]
}

@test "missing DB file: exit 0, nothing to do" {
    run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"not found"* ]]
    [ ! -e "${AUTHELIA_DB}" ]
}

@test "DB without authentication_logs table: exit 0, nothing to do" {
    sqlite3 "${AUTHELIA_DB}" "CREATE TABLE other (x INTEGER);"
    run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"no authentication_logs table"* ]]
}

@test "other tables are untouched" {
    make_db
    sqlite3 "${AUTHELIA_DB}" "CREATE TABLE totp_configurations (id INTEGER PRIMARY KEY, username TEXT);
        INSERT INTO totp_configurations (username) VALUES ('alice');"
    add_row 30
    run bash "${SCRIPT}"
    [ "$status" -eq 0 ]
    [ "$(sqlite3 "${AUTHELIA_DB}" "SELECT count(*) FROM totp_configurations;")" = "1" ]
}

@test "corrupt DB: exit 1 with error on output" {
    echo "this is not a database" > "${AUTHELIA_DB}"
    run bash "${SCRIPT}"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR"* ]]
}

@test "DB locked longer than the busy timeout: exit 1, rows intact" {
    make_db
    add_row 30
    printf 'BEGIN EXCLUSIVE;\n.shell sleep 3\nROLLBACK;\n' | sqlite3 "${AUTHELIA_DB}" &
    LOCKER=$!
    sleep 1
    SQLITE_TIMEOUT_MS=200 run bash "${SCRIPT}"
    wait "$LOCKER" || true
    [ "$status" -eq 1 ]
    [[ "$output" == *"ERROR"* ]]
    [ "$(count_rows)" = "1" ]
}

@test "DB readable but not writable: delete fails, exit 1, rows intact" {
    make_db
    add_row 30
    chmod 444 "${AUTHELIA_DB}"
    run bash "${SCRIPT}"
    [ "$status" -eq 1 ]
    [[ "$output" == *"delete failed"* ]]
    [ "$(count_rows)" = "1" ]
}

@test "missing sqlite3 binary: exit 1 with hint" {
    make_db
    SQLITE_BIN="${TMP}/no-such-sqlite3" run bash "${SCRIPT}"
    [ "$status" -eq 1 ]
    [[ "$output" == *"sqlite3 not found"* ]]
}
