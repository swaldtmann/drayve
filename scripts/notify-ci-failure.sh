#!/usr/bin/env bash
# notify-ci-failure.sh — post a Slack message when a CI run on main is red.
# Meant for the Forgejo Actions runner, which has no keychain access, so the
# Slack bot token comes in as a repo secret (SLACK_BOT_TOKEN).
#
# Only main + only failure (the gate lives in the workflow `if`, not here),
# otherwise the channel turns into noise.
#
# Env:
#   SLACK_BOT_TOKEN   (required except DRY_RUN)  Slack chat:write bot token.
#   SLACK_CHANNEL     (required)  Slack channel ID to post to. No default.
#   CI_REPO CI_WORKFLOW CI_RUN_URL CI_REF CI_SHA CI_RUN_NUMBER  Context (workflow).
#   DRY_RUN=1         (optional)  Payload to stdout instead of a real Slack post.
#
# Token never on stdout, never in argv: curl reads the auth header via `--config -`
# from stdin (like persona-push, which holds the token via env Python).
set -euo pipefail

CHANNEL="${SLACK_CHANNEL:-}"
if [[ -z "$CHANNEL" ]]; then
  echo "notify-ci-failure: SLACK_CHANNEL is not set (Slack channel ID to post to)" >&2
  exit 1
fi
REPO="${CI_REPO:-?}"
WF="${CI_WORKFLOW:-CI}"
URL="${CI_RUN_URL:-?}"
REF="${CI_REF:-?}"
SHA="${CI_SHA:-}"
RUNNO="${CI_RUN_NUMBER:-?}"
SHORT="${SHA:0:10}"

TEXT=":red_circle: CI failed on main: ${REPO} — workflow *${WF}* on \`${REF}\` (run #${RUNNO}). Commit \`${SHORT}\`. ${URL}"

# Build the payload via env+Python (clean JSON escaping, no argv leak of the text).
PAYLOAD=$(CH="$CHANNEL" TX="$TEXT" python3 -c 'import json,os;print(json.dumps({"channel":os.environ["CH"],"text":os.environ["TX"]}))')

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  printf 'channel=%s\n%s\n' "$CHANNEL" "$TEXT"
  exit 0
fi

if [[ -z "${SLACK_BOT_TOKEN:-}" ]]; then
  echo "notify-ci-failure: SLACK_BOT_TOKEN is not set (repo secret)" >&2
  exit 1
fi

RESP=$(printf 'header = "Authorization: Bearer %s"\n' "$SLACK_BOT_TOKEN" \
  | curl -sS --config - -X POST https://slack.com/api/chat.postMessage \
      -H "Content-Type: application/json; charset=utf-8" \
      --data "$PAYLOAD")

if ! printf '%s' "$RESP" | grep -q '"ok":true'; then
  echo "notify-ci-failure: Slack post failed: ${RESP}" >&2
  exit 1
fi
echo "notify-ci-failure: posted to ${CHANNEL}"
