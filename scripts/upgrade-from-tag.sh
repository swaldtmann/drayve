#!/usr/bin/env bash
# upgrade-from-tag.sh — pick the baseline release for the `upgrade` Molecule scenario.
#
# Reads tag names (one per line) from stdin and prints one release tag (vX.Y.Z;
# pre-release and other tags are ignored):
#   no argument / argument is not a release tag  -> the highest release tag
#   argument is a release tag (e.g. a tag push)  -> the highest release tag
#                                                   strictly below it
# Exit 1 with a message on stderr if there is no such tag.
#
# Usage:
#   git tag -l 'v*' | scripts/upgrade-from-tag.sh [CURRENT_REF_NAME]
set -euo pipefail

re='^v[0-9]+\.[0-9]+\.[0-9]+$'
current="${1:-}"
[[ "$current" =~ $re ]] || current=""

# Numeric sort on the three dot-separated parts (BSD and GNU sort alike).
sorted=$( { cat; echo; [[ -n "$current" ]] && echo "$current"; true; } \
  | grep -E "$re" | sort -u -t. -k1.2,1n -k2,2n -k3,3n || true)

if [[ -n "$current" ]]; then
  pick=$(printf '%s\n' "$sorted" | awk -v c="$current" '$0 == c { print p; exit } { p = $0 }')
else
  pick=$(printf '%s\n' "$sorted" | tail -n 1)
fi

if [[ -z "$pick" ]]; then
  echo "upgrade-from-tag: no release tag found${current:+ below $current}" >&2
  exit 1
fi
printf '%s\n' "$pick"
