#!/usr/bin/env bash
# Post-release checks for the Investment Lab. Run ON THE SERVER (no local quoting issues):
#   bash /opt/investment/current/check-release.sh            # or: bash check-release.sh example.com
# Prints PASS/FAIL per check and exits non-zero if any check fails.
set -u
DOMAIN="${1:-jun-liang-lyu.com}"
LAB="https://invest.${DOMAIN}"
fail=0
pass() { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s  (%s)\n' "$1" "$2"; fail=1; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$@"; }

c=$(code "$LAB/"); loc=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 30 "$LAB/")
[ "$c" = 308 ] && [ "${loc%/lab}" != "$loc" ] && pass "/ redirects to /lab" || bad "/ redirects to /lab" "got $c $loc"

c=$(code "$LAB/lab"); [ "$c" = 200 ] && pass "/lab page" || bad "/lab page" "got $c"
c=$(code "$LAB/lab/gate"); [ "$c" = 200 ] && pass "/lab/gate deep link" || bad "/lab/gate deep link" "got $c"

h=$(curl -s -D - -o /dev/null --max-time 30 "$LAB/lab" | tr -d '\r')
echo "$h" | grep -qi "^content-security-policy: default-src 'self'" && pass "CSP header" || bad "CSP header" "missing"
echo "$h" | grep -qi "^x-frame-options: DENY" && pass "X-Frame-Options" || bad "X-Frame-Options" "missing"

b=$(curl -s --max-time 30 "$LAB/api/health"); [ "$b" = '{"status":"ok"}' ] && pass "/api/health" || bad "/api/health" "$b"
c=$(code "$LAB/api/docs"); [ "$c" = 404 ] && pass "/api/docs blocked" || bad "/api/docs blocked" "got $c"
c=$(code "$LAB/api/openapi.json"); [ "$c" = 404 ] && pass "/api/openapi.json blocked" || bad "/api/openapi.json blocked" "got $c"

b=$(curl -s --max-time 60 "$LAB/api/lab/companies/GOOG/snapshot?lang=en")
echo "$b" | grep -q '"ticker":"GOOG"' && echo "$b" | grep -q '"quarters"' && pass "GOOG snapshot from SEC" || bad "GOOG snapshot from SEC" "$(echo "$b" | head -c 150)"

body='{"portfolio_id":"concentrated-tech","symbol":"AMZN","side":"buy","amount_usd":800}'
b=$(curl -s --max-time 30 -X POST -H 'Content-Type: application/json' --data "$body" "$LAB/api/lab/gate")
echo "$b" | grep -q '"overall":"rule_breaks"' && pass "gate POST" || bad "gate POST" "$(echo "$b" | head -c 150)"

c=$(code "$LAB/api/lab/companies/IBKR/snapshot"); [ "$c" = 404 ] && pass "non-curated ticker rejected" || bad "non-curated ticker rejected" "got $c"

# The main site must be unaffected.
c=$(code "https://${DOMAIN}/en/"); [ "$c" = 200 ] && pass "main site /en/" || bad "main site /en/" "got $c"
c=$(code "https://${DOMAIN}/api/health"); [ "$c" = 200 ] && pass "main site /api/health" || bad "main site /api/health" "got $c"

echo
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' 2>/dev/null || true
echo
[ "$fail" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$fail"
