# Investment Lab

A personal investment discipline system: a deterministic rule engine, a pre-trade gate, and an investment-memo workflow, with a public Lab demo on fictional portfolios.

It never recommends trades and never places orders. See `docs/DESIGN.md`.

## Status

- Done: rule engine, pre-trade gate, memo state machine, review report, CSV import, CLI (`investment_core`).
- Done: SEC EDGAR quarterly financials with evidence for every number (`investment_core.financials`, `investment_data`), segment figures, and filing-text evidence with verbatim quote checks.
- Done: memo workflow (`investment_ai`): evidence-grounded draft, review of the user's answers, finalisation through the state machine. Every model output is validated for grounding and advice before it is shown.
- Done: public Lab web app, FastAPI (`investment_api`) + Vite/React (`frontend/`), Chinese and English UI. Live: https://invest.jun-liang-lyu.com (first release 2026-09-28).
  - Pre-trade gate on fictional portfolios or holdings typed in the browser (not stored), plus sample portfolios built from an institution's latest 13F.
  - Financial snapshot for any SEC-registered US company (ten are tested one by one): eight quarters, segments, an AI "what happened last quarter" reading, an estimated next earnings date, a macro background strip, insider trades (Form 4) and well-known institutions' 13F holdings. The last three are background only and never reach the gate, the rules or the AI.
  - Reference portfolios: 13F profiles of eleven well-known institutions (or any 13F filer by CIK), a draft rule file copied mechanically from the filing, and an AI structure reading in which code fills in every number.
  - AI skeptic and memo flow: live, passed a 34-case adversarial eval set (`python -m investment_ai eval-skeptic`) with no advice shown. The quarterly reading and the 13F structure reading have no eval set yet.
- Release: every push to `main` runs the tests, builds a release, and a timer on the server pulls it; a failed check rolls back automatically.
- Not done: AI summaries of well-known investors' letters, index-ETF holdings (N-PORT), CUSIP-to-ticker matching beyond the gate sample.

## Run locally (Windows PowerShell)

PowerShell may block `Activate.ps1`, so call the venv's Python directly:

```powershell
cd investment-lab\backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
```

Try the CLI on a demo portfolio:

```powershell
.venv\Scripts\python.exe -m investment_core evaluate --portfolio ..\fixtures\demo_portfolios\concentrated-tech.json --rules ..\fixtures\rules\demo.yaml
.venv\Scripts\python.exe -m investment_core gate --portfolio ..\fixtures\demo_portfolios\concentrated-tech.json --rules ..\fixtures\rules\demo.yaml --symbol AMZN --amount 800
.venv\Scripts\python.exe -m investment_core review --portfolio ..\fixtures\demo_portfolios\concentrated-tech.json --rules ..\fixtures\rules\demo.yaml
```

Quarterly financials from SEC EDGAR (set a contact User-Agent first, as SEC requires):

```powershell
$env:SEC_USER_AGENT = "Your Name your@email.com"
.venv\Scripts\python.exe -m investment_data financials GOOG
```

## Run the Lab locally

```powershell
cd investment-lab\backend
.venv\Scripts\python.exe -m pip install -e ".[api,dev]"
.venv\Scripts\python.exe -m uvicorn investment_api.app:app --port 8081
# second terminal
cd investment-lab\frontend
npm.cmd ci
npm.cmd run dev          # http://localhost:5173/lab  (proxies /api to :8081)
```

Set `API_DOCS=1` to enable `/api/docs` locally; it is off in production.

## Deploy

Releases are pull-based. A push to `main` (except docs-only changes) runs `.github/workflows/release.yml`: tests, frontend build, image boundary checks, then a GitHub Release; the server timer (`deploy/server/`) pulls it, verifies the hash, switches over and runs `deploy/check-release.sh`, rolling back if the check fails. Put `[skip release]` in a commit message to skip one release, or set the repository variable `AUTO_RELEASE=off` to pause them. Server setup and rollback: `docs/DEPLOY.md`. `deploy\build-release.ps1` still builds and checks a release locally (tests, frontend, Docker image).

## Privacy

Real holdings live only in `private-data/` (git-ignored). All fixtures in this repository use fictional portfolios or fictional tickers.
