#!/usr/bin/env bash
# notify-ci-failure.sh — Push an Stephans Messenger-Flaeche (#00_wooki) wenn ein
# CI-Run auf main rot ist. Gegenstueck zu claudes-welt tools/persona-push, aber
# fuer den Forgejo-Actions-Runner: der hat keinen macOS-Keychain-Zugriff, also
# kommt der Slack-Bot-Token ueber ein Repo-Secret (SLACK_BOT_TOKEN).
#
# Push-statt-Pull (Stephan-Vorgabe W08): CI-Rot auf main ist muss-wissen und
# qualifiziert fuer die Messenger-Flaeche. Nur main + nur failure (das Gate
# sitzt im Workflow-`if`, nicht hier) — sonst stirbt die Flaeche (SB-Tod-Lehre).
# AFKI-W-197.
#
# Env:
#   SLACK_BOT_TOKEN   (Pflicht ausser DRY_RUN)  Slack chat:write Bot-Token.
#   SLACK_CHANNEL     (optional)  Default C0BAJ39L0SH (#00_wooki).
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
  echo "notify-ci-failure: SLACK_BOT_TOKEN fehlt (Codeberg-Repo-Secret)" >&2
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
