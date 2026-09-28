# Build an Investment Lab release on Windows (never on the server).
#   powershell -ExecutionPolicy Bypass -File deploy\build-release.ps1
# Output: deploy\release\<RELEASE_ID>\  (site\, invest.caddy, compose.invest.yaml, investment-api-<id>.tar)
# Requires: backend\.venv with the dev extras (pip install -e ".[dev]"), Node.js + npm, Docker Desktop.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$id = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$out = Join-Path $root "deploy\release\$id"

Write-Host "== 1/4 backend tests"
Push-Location backend
& .\.venv\Scripts\python.exe -m pytest -q
$ok = $LASTEXITCODE
Pop-Location
if ($ok -ne 0) { throw "tests failed" }

Write-Host "== 2/4 frontend build"
Push-Location frontend
npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
Pop-Location

Write-Host "== 3/4 API image investment-api:$id"
docker build -f deploy/api.Dockerfile -t "investment-api:$id" .
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
# The image must not contain private data.
docker run --rm "investment-api:$id" sh -c "test ! -e /app/private-data && test ! -e /app/.env && ! ls /app/fixtures/rules | grep -v '^demo.yaml$'"
if ($LASTEXITCODE -ne 0) { throw "image boundary check failed" }

Write-Host "== 4/4 package $out"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item -Recurse frontend\dist (Join-Path $out "site")
Copy-Item deploy\invest.caddy, deploy\compose.invest.yaml, deploy\check-release.sh $out
docker save "investment-api:$id" -o (Join-Path $out "investment-api-$id.tar")
Write-Host ""
Write-Host "Release $id ready. Upload deploy\release\$id to the server and follow docs\DEPLOY.md."
Write-Host "Set INVEST_API_IMAGE=investment-api:$id in the server's .env.production."
