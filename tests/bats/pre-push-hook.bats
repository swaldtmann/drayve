#!/usr/bin/env bats
# Tests for .githooks/pre-push: git exports GIT_DIR/GIT_INDEX_FILE/... into hooks;
# `make test` must not see them, or tests that create throwaway repos commit
# into the real one.
#
# Run: bats tests/bats/pre-push-hook.bats

HOOK="${BATS_TEST_DIRNAME}/../../.githooks/pre-push"

setup() {
    TMP=$(mktemp -d)
    mkdir -p "${TMP}/bin"
    # Fake make: record every GIT_* variable it inherits, succeed.
    cat > "${TMP}/bin/make" <<'EOS'
#!/usr/bin/env bash
env | grep '^GIT_' > "${SEEN}" || true
exit "${MAKE_RC:-0}"
EOS
    chmod +x "${TMP}/bin/make"
    export SEEN="${TMP}/seen"
    export PATH="${TMP}/bin:${PATH}"
}

teardown() {
    rm -rf "${TMP}"
}

@test "hook passes no git-set repo variables to make" {
    GIT_DIR=/some/real/.git GIT_INDEX_FILE=/some/real/.git/index \
        GIT_WORK_TREE=/some/real GIT_PREFIX= \
        GIT_OBJECT_DIRECTORY=/some/o GIT_COMMON_DIR=/some/c \
        run bash "${HOOK}"
    [ "$status" -eq 0 ]
    run grep -E '^GIT_(DIR|INDEX_FILE|WORK_TREE|PREFIX|OBJECT_DIRECTORY|COMMON_DIR)=' "${SEEN}"
    [ "$status" -eq 1 ]
}

@test "hook keeps unrelated GIT_ settings (author identity)" {
    GIT_DIR=/some/real/.git GIT_AUTHOR_NAME=keepme run bash "${HOOK}"
    [ "$status" -eq 0 ]
    grep -qx 'GIT_AUTHOR_NAME=keepme' "${SEEN}"
}

@test "hook propagates a failing make" {
    MAKE_RC=3 run bash "${HOOK}"
    [ "$status" -eq 3 ]
}
