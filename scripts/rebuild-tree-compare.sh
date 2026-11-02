#!/usr/bin/env bash
# rebuild-tree-compare.sh — compare two file trees by relative path and sha256.
#
# Usage: rebuild-tree-compare.sh <expected_dir> <actual_dir> [expected_count]
#
# Used by the mockforge hook of `make rebuild` (ansible/tasks/rebuild_verify_mockforge.yml):
# expected = freshly generated mockforge tree, actual = restored volume.
# Prints one summary line `files=<N> mismatches=<M>` (N = files in expected),
# followed by one line per differing path. Exit 0 only if M=0, the tree is not
# empty and, when given, N equals expected_count.
set -euo pipefail

expected="${1:?Usage: $0 <expected_dir> <actual_dir> [expected_count]}"
actual="${2:?Usage: $0 <expected_dir> <actual_dir> [expected_count]}"
want="${3:-}"

for d in "$expected" "$actual"; do
    [ -d "$d" ] || { echo "not a directory: $d" >&2; exit 2; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Manifest: "<sha256>  <relative path>" sorted by path (NUL-safe, spaces allowed).
manifest() {
    (cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) | sed 's|  \./|  |'
}
manifest "$expected" > "$work/exp"
manifest "$actual" > "$work/act"

files="$(wc -l < "$work/exp" | tr -d ' ')"

# Paths whose manifest line is not identical on both sides (changed, missing, extra).
diff_paths="$( { diff "$work/exp" "$work/act" || true; } | { grep -E '^[<>] ' || true; } | sed -E 's/^[<>] [0-9a-f]{64}  //' | LC_ALL=C sort -u )"
mismatches=0
[ -z "$diff_paths" ] || mismatches="$(printf '%s\n' "$diff_paths" | wc -l | tr -d ' ')"

echo "files=$files mismatches=$mismatches"
[ "$mismatches" -eq 0 ] || printf '%s\n' "$diff_paths" | sed 's/^/differs: /'

rc=0
[ "$mismatches" -eq 0 ] || rc=1
if [ "$files" -eq 0 ]; then
    echo "expected tree is empty" >&2
    rc=1
fi
if [ -n "$want" ] && [ "$files" -ne "$want" ]; then
    echo "expected $want files, found $files" >&2
    rc=1
fi
exit "$rc"
