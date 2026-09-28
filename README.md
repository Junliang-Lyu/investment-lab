# Investment Lab

A personal investment discipline system: a deterministic rule engine, a pre-trade gate, and an investment-memo workflow, with a public Lab demo on fictional portfolios.

It never recommends trades and never places orders. See `docs/DESIGN.md`.

## Status

- Done: rule engine, pre-trade gate, memo state machine, review report, CSV import, CLI (`investment_core`).
- Done: SEC EDGAR quarterly financials with evidence for every number (`investment_core.financials`, `investment_data`).
- Done: memo workflow (`investment_ai`): evidence-grounded draft (SOP Steps 3-4), review of the user's answers (Step 6), finalisation through the state machine (Step 7). Every model output is validated for grounding and advice before it is shown.
- Done: 13F institutional holdings (`python -m investment_data 13f BRK-B`), segment figures and filing-text evidence with verbatim quote checks.
- Done: public Lab web app: FastAPI (`investment_api`) + Vite/React (`frontend/`) with a financial snapshot page and the pre-trade gate on fictional portfolios. Deployment files in `deploy/`, runbook in `docs/DEPLOY.md`.
- Live: https://invest.jun-liang-lyu.com (first release 2026-09-28).
- Next: Lab card on the main site, the eval set, then the LLM skeptic on the Lab.

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

`powershell -ExecutionPolicy Bypass -File deploy\build-release.ps1` builds and checks a release (tests, frontend, Docker image). Server steps: `docs/DEPLOY.md`.

## Privacy

Real holdings live only in `private-data/` (git-ignored). All fixtures in this repository use fictional portfolios or fictional tickers.
