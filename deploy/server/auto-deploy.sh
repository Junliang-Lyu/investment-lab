#!/usr/bin/env bash
# Pull-based release for the Investment Lab. Runs ON THE SERVER from a systemd timer (every 5 minutes).
# It looks for a newer GitHub Release of the repo, verifies and installs it, runs deploy/check-release.sh, and
# rolls back to the previous release if the checks fail. Nothing connects in from outside: the server only fetches
# https://api.github.com and the release files (public repository, no token on the server).
set -u
REPO="${INVEST_REPO:-Junliang-Lyu/investment-lab}"
AUTHORS="${INVEST_RELEASE_AUTHORS:-github-actions[bot],Junliang-Lyu}"   # who may have published the release
BASE="${INVEST_BASE:-/opt/investment}"
STACK="${INVEST_STACK:-/opt/portfolio/current/deploy}"                   # self_web deploy dir (compose.yaml, .env.production)
DOMAIN="${INVEST_DOMAIN:-jun-liang-lyu.com}"
STATE="$BASE/state"
mkdir -p "$STATE" "$BASE/releases"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

exec 9>"$STATE/lock"
flock -n 9 || exit 0   # another run is still busy

json=$(curl -fsS --max-time 30 -H 'Accept: application/vnd.github+json' -H 'User-Agent: invest-auto-deploy' \
  "https://api.github.com/repos/$REPO/releases/latest") || { log "cannot read the latest release (network or rate limit)"; exit 0; }
info=$(printf '%s' "$json" | python3 -c '
import json, re, sys
d = json.load(sys.stdin)
tag = d.get("tag_name", "")
author = (d.get("author") or {}).get("login", "")
assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
m = re.fullmatch(r"release-(\d{8}T\d{6}Z)", tag)
if not m:
    sys.exit(2)
rid = m.group(1)
a, s = f"lab-release-{rid}.tar.gz", f"lab-release-{rid}.tar.gz.sha256"
if a not in assets or s not in assets:
    sys.exit(3)
print(tag, rid, author, assets[a], assets[s])
') || { log "latest release is not a lab release or its files are missing; skipping"; exit 0; }
read -r TAG ID AUTHOR URL_TAR URL_SHA <<<"$info"

[ "$TAG" = "$(cat "$STATE/last_good" 2>/dev/null)" ] && exit 0      # already running this one
[ "$TAG" = "$(cat "$STATE/last_failed" 2>/dev/null)" ] && exit 0    # failed before: wait for a newer release
case ",$AUTHORS," in *",$AUTHOR,"*) ;; *) log "release $TAG was published by '$AUTHOR', not an allowed publisher; skipping"; exit 0;; esac

log "new release $TAG (published by $AUTHOR)"
REL="$BASE/releases/$ID"
[ -e "$REL" ] && { log "$REL already exists; skipping"; exit 0; }
tmp=$(mktemp -d "$BASE/releases/.incoming.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
curl -fsSL --max-time 600 -o "$tmp/r.tar.gz" "$URL_TAR" && curl -fsSL --max-time 60 -o "$tmp/r.sha256" "$URL_SHA" \
  || { log "download failed"; exit 0; }
expected=$(awk '{print $1}' "$tmp/r.sha256"); actual=$(sha256sum "$tmp/r.tar.gz" | awk '{print $1}')
[ -n "$expected" ] && [ "$expected" = "$actual" ] || { log "checksum mismatch; not installing"; echo "$TAG" >"$STATE/last_failed"; exit 0; }
# Only paths inside <ID>/ are allowed in the archive.
if tar tzf "$tmp/r.tar.gz" | grep -Ev "^$ID(/|$)" | grep -q . || tar tzf "$tmp/r.tar.gz" | grep -q '\.\.'; then
  log "archive has unexpected paths; not installing"; echo "$TAG" >"$STATE/last_failed"; exit 0
fi
tar xzf "$tmp/r.tar.gz" -C "$BASE/releases" || { log "extract failed"; rm -rf "$REL"; exit 0; }
docker load -i "$REL/investment-api-$ID.tar" >/dev/null && rm -f "$REL/investment-api-$ID.tar" \
  || { log "docker load failed"; echo "$TAG" >"$STATE/last_failed"; exit 0; }

ENVF="$STACK/.env.production"
prev_link=$(readlink -f "$BASE/current" 2>/dev/null || true)
prev_line=$(grep '^INVEST_API_IMAGE=' "$ENVF" 2>/dev/null || true)
cp "$ENVF" "$STATE/env.production.bak"
dc() { (cd "$STACK" && docker compose --env-file .env.production -f compose.yaml -f "$BASE/current/compose.invest.yaml" "$@"); }
switch_to() {   # $1 release dir, $2 INVEST_API_IMAGE line
  ln -sfn "$1" "$BASE/current"
  if grep -q '^INVEST_API_IMAGE=' "$ENVF"; then sed -i "s|^INVEST_API_IMAGE=.*|$2|" "$ENVF"; else printf '\n%s\n' "$2" >>"$ENVF"; fi
  dc config >/dev/null && dc up -d && dc up -d --force-recreate caddy   # Caddy must be recreated: it mounts current/site
}

log "switching to $TAG"
if switch_to "$REL" "INVEST_API_IMAGE=investment-api:$ID"; then
  ok=1; out=""
  for i in 1 2 3; do
    sleep 20
    out=$(bash "$REL/check-release.sh" "$DOMAIN" 2>&1) && { ok=0; break; }
  done
else
  ok=1; out="compose failed"
fi
printf '%s\n' "$out" | tail -n 40

if [ "$ok" = 0 ]; then
  echo "$TAG" >"$STATE/last_good"; rm -f "$STATE/last_failed"
  printf '%s deployed %s\n' "$(date -u +%FT%TZ)" "$TAG" >"$STATE/last-deploy.txt"
  log "release $TAG is live"
  # keep the 5 newest release folders and 3 newest API images
  ls -1dt "$BASE"/releases/2* 2>/dev/null | tail -n +6 | xargs -r rm -rf
  docker images investment-api --format '{{.Tag}}' | sort -r | tail -n +4 | xargs -r -I{} docker rmi "investment-api:{}" >/dev/null 2>&1 || true
else
  log "checks failed; rolling back"
  echo "$TAG" >"$STATE/last_failed"
  printf '%s FAILED %s (rolled back)\n' "$(date -u +%FT%TZ)" "$TAG" >"$STATE/last-deploy.txt"
  if [ -n "$prev_link" ]; then
    switch_to "$prev_link" "${prev_line:-INVEST_API_IMAGE=}" || log "rollback compose failed: check the server by hand"
  fi
fi
