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

# The page served must be this release's build. Caddy bind-mounts /opt/investment/current/site, and Docker resolves
# that symlink when the container is created, so after switching releases Caddy must be recreated.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/site/index.html" ]; then
  want=$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' "$HERE/site/index.html" | head -1)
  got=$(curl -s --max-time 30 "$LAB/lab" | grep -o 'assets/index-[A-Za-z0-9_-]*\.js' | head -1)
  [ -n "$want" ] && [ "$want" = "$got" ] && pass "served frontend is this release ($want)" \
    || bad "served frontend is this release" "serving ${got:-?}, release has ${want:-?}; run: dc up -d --force-recreate caddy"
  # Features that must be in the shipped bundle (strings survive minification): focus-angle chips, other-company box.
  js=$(curl -s --max-time 30 "$LAB/${got:-assets/missing.js}")
  echo "$js" | grep -q "Look from these angles" && pass "bundle has focus-angle chips" || bad "bundle has focus-angle chips" "string not found in ${got:-?}"
  echo "$js" | grep -q "What happened last quarter" && pass "bundle has the quarter explainer" || bad "bundle has the quarter explainer" "string not found in ${got:-?}"
  echo "$js" | grep -q "Upcoming earnings" && pass "bundle has the market context page" || bad "bundle has the market context page" "string not found in ${got:-?}"
  echo "$js" | grep -q "Background only, not a signal" && pass "bundle has the market background strip" || bad "bundle has the market background strip" "string not found in ${got:-?}"
  echo "$js" | grep -q "How large investors allocate" && pass "bundle has the reference portfolios page" || bad "bundle has the reference portfolios page" "string not found in ${got:-?}"
  echo "$js" | grep -q "Estimate from past release dates" && pass "bundle has the next-earnings callout" || bad "bundle has the next-earnings callout" "string not found in ${got:-?}"
fi

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

c=$(code "$LAB/api/lab/companies/XYZQ/snapshot"); [ "$c" = 404 ] && pass "unknown ticker rejected" || bad "unknown ticker rejected" "got $c"
c=$(code "$LAB/api/lab/companies/ORCL/snapshot"); [ "$c" = 200 ] && pass "other US company snapshot (ORCL)" || bad "other US company snapshot (ORCL)" "got $c"

b=$(curl -s --max-time 30 -X POST -H 'Content-Type: application/json' --data "$body" "$LAB/api/lab/gate?lang=zh")
echo "$b" | grep -q '仓位与集中度' && pass "gate in Chinese" || bad "gate in Chinese" "$(echo "$b" | head -c 150)"
c=$(code "$LAB/lab/skeptic"); [ "$c" = 200 ] && pass "/lab/skeptic page" || bad "/lab/skeptic page" "got $c"

# AI skeptic: report its state; it must be off unless the operator enabled it after the eval passed.
b=$(curl -s --max-time 30 "$LAB/api/lab/skeptic/status")
if echo "$b" | grep -q '"enabled":true'; then
  pass "AI skeptic status: ENABLED ($b)"
  c=$(code -X POST -H 'Content-Type: application/json' --data '{"ticker":"GOOG","thesis":"short"}' "$LAB/api/lab/skeptic")
  [ "$c" = 422 ] && pass "skeptic rejects too-short thesis" || bad "skeptic rejects too-short thesis" "got $c"
  c=$(code -X POST -H 'Content-Type: application/json' --data '{"ticker":"XYZQ","lang":"en"}' "$LAB/api/lab/explain")
  [ "$c" = 404 ] && pass "explain: unknown ticker rejected (no model call)" || bad "explain: unknown ticker rejected" "got $c"
elif echo "$b" | grep -q '"enabled":false'; then
  pass "AI skeptic status: disabled"
else
  bad "AI skeptic status" "$(echo "$b" | head -c 150)"
fi
# Memo workflow: SPA route served; an unknown memo id is 404 (enabled) or 503 (skeptic off); a memo needs a skeptic result.
c=$(code "$LAB/lab/memos"); [ "$c" = 200 ] && pass "/lab/memos page" || bad "/lab/memos page" "got $c"
c=$(code "$LAB/lab/memo/AAAAAAAAAAAAAAAAAAAAAA"); [ "$c" = 200 ] && pass "/lab/memo/<id> page" || bad "/lab/memo/<id> page" "got $c"
c=$(code "$LAB/api/lab/memos/AAAAAAAAAAAAAAAAAAAAAA"); { [ "$c" = 404 ] || [ "$c" = 503 ]; } && pass "unknown memo ($c)" || bad "unknown memo" "got $c"
c=$(code "$LAB/api/lab/evals/latest"); { [ "$c" = 200 ] || [ "$c" = 404 ]; } && pass "eval results endpoint ($c)" || bad "eval results endpoint" "got $c"

# Market context: the page is a SPA route; the data endpoints depend on SEC / FRED, so an outage there (503) must not
# roll a release back. What is checked is that they exist and answer sensibly.
c=$(code "$LAB/lab/market"); [ "$c" = 200 ] && pass "/lab/market page" || bad "/lab/market page" "got $c"
b=$(curl -s --max-time 90 -w '\n%{http_code}' "$LAB/api/lab/calendar"); c=${b##*$'\n'}
if [ "$c" = 200 ]; then echo "$b" | grep -q '"items":\[{' && pass "earnings calendar (200)" || bad "earnings calendar" "200 without items"
elif [ "$c" = 503 ]; then pass "earnings calendar unavailable right now (503, SEC)"; else bad "earnings calendar" "got $c"; fi
b=$(curl -s --max-time 90 -w '\n%{http_code}' "$LAB/api/lab/macro"); c=${b##*$'\n'}
if [ "$c" = 200 ]; then echo "$b" | grep -q '"series":\[{' && pass "macro series from FRED (200)" || bad "macro series" "200 without series"
elif [ "$c" = 503 ]; then pass "macro series unavailable right now (503, FRED or switched off)"; else bad "macro series" "got $c"; fi

# 13F reference portfolios: the page is a SPA route; the profile comes from SEC (503 on an outage must not roll back).
c=$(code "$LAB/lab/reference"); [ "$c" = 200 ] && pass "/lab/reference page" || bad "/lab/reference page" "got $c"
b=$(curl -s --max-time 30 "$LAB/api/lab/reference"); echo "$b" | grep -q '"id":"berkshire"' && pass "reference list" || bad "reference list" "$(echo "$b" | head -c 150)"
c=$(code "$LAB/api/lab/reference/nobody"); [ "$c" = 404 ] && pass "unknown reference rejected" || bad "unknown reference rejected" "got $c"
b=$(curl -s --max-time 90 -w '\n%{http_code}' "$LAB/api/lab/reference/berkshire"); c=${b##*$'\n'}
if [ "$c" = 200 ]; then echo "$b" | grep -q '"rule_draft"' && pass "Berkshire 13F profile (200)" || bad "Berkshire 13F profile" "200 without rule_draft"
elif [ "$c" = 503 ]; then pass "13F profile unavailable right now (503, SEC)"; else bad "Berkshire 13F profile" "got $c"; fi

# The main site must be unaffected.
c=$(code "https://${DOMAIN}/en/"); [ "$c" = 200 ] && pass "main site /en/" || bad "main site /en/" "got $c"
c=$(code "https://${DOMAIN}/api/health"); [ "$c" = 200 ] && pass "main site /api/health" || bad "main site /api/health" "got $c"

echo
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' 2>/dev/null || true
echo
[ "$fail" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$fail"
