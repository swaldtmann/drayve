#!/usr/bin/env bash
# Functional test: the rendered CrowdSec whitelist expression suppresses the
# Nextcloud user_status burst, and only that (real CrowdSec, log replay).
#
# Run:  bash tests/crowdsec/test-whitelist-expression.sh
# Needs: docker, python3 with jinja2, network (hub download at container start).
# Exit 0 = all assertions hold, 1 = at least one failed.
#
# The whitelist file is rendered by the repo template. Hostnames are not in the
# Traefik common log format (only the JSON format carries RequestHost), so the
# expression pins the Traefik router name (evt.Meta.traefik_router_name) instead.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
IMAGE="${CROWDSEC_IMAGE:-crowdsecurity/crowdsec:v1.8.1}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/drayve-cs-test.XXXXXX")"
NAME="drayve-cs-test-$$"
FAILS=0

# Narrow on purpose: anchored path with a plain user-name charset (no ?, %, /),
# only GET, only 200/404. The doubled backslash is expr-lang string escaping.
EXPR="$(cat <<'EXPR_END'
evt.Meta.traefik_router_name startsWith 'nextcloud@' && evt.Meta.http_verb == 'GET' && evt.Meta.http_status in ['200','404'] && evt.Meta.http_path matches '^/ocs/v2\\.php/apps/user_status/api/v1/statuses/[A-Za-z0-9._@-]+$'
EXPR_END
)"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1; rm -rf "${WORK:?}"; }
trap cleanup EXIT

# --- synthetic Traefik access logs (common log format, one second, 203.0.113.0/24)
"$PYTHON" - "$WORK" <<'PY'
import random, sys
out = sys.argv[1]
random.seed(1)
def line(ip, path, status, n, router):
    return (f'{ip} - - [13/Oct/2026:10:00:00 +0000] "GET {path} HTTP/1.1" {status} 123 '
            f'"-" "-" {n} "{router}" "http://172.18.0.5:80" 5ms')
PFX = "/ocs/v2.php/apps/user_status/api/v1/statuses/"
def status_paths(ip, router):
    return [line(ip, f"{PFX}user{i:02d}",
                 404 if i % 3 == 0 else 200, i, router) for i in range(1, 61)]
def variant(ip, suffix, statuses):
    # status-path look-alikes that the narrowed expression must NOT whitelist
    return [line(ip, PFX + suffix(i), statuses[i % len(statuses)], i, "nextcloud@docker")
            for i in range(1, 61)]
sets = {
    # A: the real-world burst, on the nextcloud router
    "A_status_nextcloud": status_paths("203.0.113.11", "nextcloud@docker"),
    # B: 60 random 404 paths on the nextcloud router
    "B_random404_nextcloud": [line("203.0.113.12", f"/x{random.randrange(10**9)}/{i}.php", 404, i, "nextcloud@docker")
                              for i in range(1, 61)],
    # C: same status paths on another router
    "C_status_other": status_paths("203.0.113.13", "other@docker"),
    # D-G: look-alikes on the nextcloud router that must keep counting
    "D_query_string": variant("203.0.113.14", lambda i: f"user{i:02d}?x={i}", [404]),
    "E_encoded_slash": variant("203.0.113.15", lambda i: f"..%2Fadmin{i}.php", [404]),
    "F_extra_segment": variant("203.0.113.16", lambda i: f"user{i:02d}/extra{i}", [404]),
    "G_status_401_403": variant("203.0.113.17", lambda i: f"user{i:02d}", [401, 403]),
}
for name, lines in sets.items():
    open(f"{out}/{name}.log", "w").write("\n".join(lines) + "\n")
PY

SETS=(A_status_nextcloud B_random404_nextcloud C_status_other D_query_string E_encoded_slash F_extra_segment G_status_401_403)

run_mode() { # $1 = without|with
  local mode="$1" mount=()
  if [ "$mode" = with ]; then
    "$PYTHON" "$ROOT/tests/crowdsec/render_whitelist.py" \
      "$("$PYTHON" -c 'import json,sys; print(json.dumps({"crowdsec_whitelist": ["127.0.0.1"], "crowdsec_whitelist_expressions": [sys.argv[1]]}))' "$EXPR")" \
      >"$WORK/whitelist.yaml" || { echo "FAIL: render"; FAILS=$((FAILS+1)); return; }
    mount=(-v "$WORK/whitelist.yaml:/etc/crowdsec/parsers/s02-enrich/whitelist.yaml:ro")
  fi
  docker rm -f "$NAME" >/dev/null 2>&1
  docker run -d --name "$NAME" -e CROWDSEC_BYPASS_DB_VOLUME_CHECK=true -e DISABLE_ONLINE_API=true \
    -e COLLECTIONS="crowdsecurity/traefik crowdsecurity/http-cve crowdsecurity/whitelist-good-actors" \
    -v "$WORK:/replay:ro" "${mount[@]}" "$IMAGE" >/dev/null || { echo "FAIL: docker run"; FAILS=$((FAILS+1)); return; }
  for _ in $(seq 1 60); do
    docker exec "$NAME" test -f /etc/crowdsec/scenarios/http-probing.yaml 2>/dev/null \
      && docker exec "$NAME" test -f /etc/crowdsec/parsers/s01-parse/traefik-logs.yaml 2>/dev/null && break
    sleep 3
  done
  for set in "${SETS[@]}"; do
    local out
    out="$(docker exec "$NAME" crowdsec -c /etc/crowdsec/config.yaml \
      -dsn "file:///replay/$set.log" -type traefik -no-api 2>&1)"
    if grep -q "Acquisition is finished" <<<"$out"; then
      OVERFLOWS[$mode,$set]="$(grep -c " performed '" <<<"$out")"
    else
      OVERFLOWS[$mode,$set]="ERR"   # crowdsec did not complete the replay (bad config?)
      echo "$out" | tail -3 >&2
    fi
  done
}

declare -A OVERFLOWS
run_mode without
run_mode with

check() { # $1 label, $2 mode, $3 set, $4 expect: over|quiet
  local n="${OVERFLOWS[$2,$3]:-}" ok=0
  if [ "$4" = over ] && [ "${n:-0}" -ge 1 ]; then ok=1; fi
  if [ "$4" = quiet ] && [ "$n" = 0 ]; then ok=1; fi
  if [ "$ok" = 1 ]; then echo "ok   $1 (overflows=$n)"; else echo "FAIL $1 (overflows=${n:-?}, expected $4)"; FAILS=$((FAILS+1)); fi
}

check "without exception: status burst overflows"          without A_status_nextcloud   over
check "without exception: (i) query string overflows"       without D_query_string       over
check "without exception: (iv) status 401/403 overflows"    without G_status_401_403     over
check "with exception: status burst is quiet"              with    A_status_nextcloud   quiet
check "with exception: random 404 paths still overflow"    with    B_random404_nextcloud over
check "with exception: other router still overflows"       with    C_status_other       over
check "with exception: (i) query string still overflows"    with    D_query_string       over
check "with exception: (ii) ..%2F suffix still overflows"   with    E_encoded_slash      over
check "with exception: (iii) extra segment still overflows" with    F_extra_segment      over
check "with exception: (iv) status 401/403 still overflows" with    G_status_401_403     over

[ "$FAILS" = 0 ] && echo "PASS" || echo "$FAILS check(s) failed"
[ "$FAILS" = 0 ]
