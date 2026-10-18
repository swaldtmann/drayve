#!/usr/bin/env bash
# notify-ci-failure.sh — post a Slack message when a CI run on main is red.
# Meant for the Forgejo Actions runner, which has no keychain access, so the
# Slack bot token comes in as a repo secret (SLACK_BOT_TOKEN).
#
# Only main + only failure (the gate lives in the workflow `if`, not here),
# otherwise the channel turns into noise.
#
# Env:
#   SLACK_BOT_TOKEN   (Pflicht ausser DRY_RUN)  Slack chat:write Bot-Token.
#   SLACK_CHANNEL     (optional)  Default C0BAJ39L0SH.
#   CI_REPO CI_WORKFLOW CI_RUN_URL CI_REF CI_SHA CI_RUN_NUMBER  Kontext (Workflow).
#   DRY_RUN=1         (optional)  Payload nach stdout statt echtem Slack-Post.
#
# Token nie auf stdout, nie in argv: curl liest den Auth-Header via `--config -`
# von stdin (analog persona-push, das den Token via env-Python haelt).
set -euo pipefail

CHANNEL="${SLACK_CHANNEL:-C0BAJ39L0SH}"
REPO="${CI_REPO:-?}"
WF="${CI_WORKFLOW:-CI}"
URL="${CI_RUN_URL:-?}"
REF="${CI_REF:-?}"
SHA="${CI_SHA:-}"
RUNNO="${CI_RUN_NUMBER:-?}"
SHORT="${SHA:0:10}"

TEXT="*byrd* › :red_circle: ${REPO} CI rot — Workflow *${WF}* auf \`${REF}\` (Run #${RUNNO}) failed. Commit \`${SHORT}\`. ${URL}"

# Payload via env+Python bauen (sauberes JSON-Escaping, kein argv-Leak des Texts).
PAYLOAD=$(CH="$CHANNEL" TX="$TEXT" python3 -c 'import json,os;print(json.dumps({"channel":os.environ["CH"],"text":os.environ["TX"]}))')

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  printf 'channel=%s\n%s\n' "$CHANNEL" "$TEXT"
  exit 0
fi

if [[ -z "${SLACK_BOT_TOKEN:-}" ]]; then
  echo "notify-ci-failure: SLACK_BOT_TOKEN fehlt (Repo-Secret)" >&2
  exit 1
fi

RESP=$(printf 'header = "Authorization: Bearer %s"\n' "$SLACK_BOT_TOKEN" \
  | curl -sS --config - -X POST https://slack.com/api/chat.postMessage \
      -H "Content-Type: application/json; charset=utf-8" \
      --data "$PAYLOAD")

if ! printf '%s' "$RESP" | grep -q '"ok":true'; then
  echo "notify-ci-failure: Slack-Post fehlgeschlagen: ${RESP}" >&2
  exit 1
fi
echo "notify-ci-failure: gepostet nach ${CHANNEL}"
