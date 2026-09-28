"""Public Lab endpoints. Only fictional portfolios and public SEC data; never private files."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from investment_ai.evidence import label
from investment_core import evaluate, evaluate_trade
from investment_core.financials import DISPLAY_METRICS, build_financials, source_url
from investment_core.importers import load_portfolio, load_rule_set
from investment_core.models import Asset, AssetType, Sleeve, TradeProposal
from investment_core.segments import member_label, segment_series

log = logging.getLogger("investment_api.lab")

TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


class GateRequest(BaseModel):
    portfolio_id: str = Field(max_length=40)
    symbol: str = Field(max_length=10)
    side: Literal["buy", "sell"] = "buy"
    amount_usd: float = Field(gt=0, le=10_000_000)
    attestations: dict[str, bool] = Field(default_factory=dict)


def build_router(settings, client_factory) -> APIRouter:
    r = APIRouter(prefix="/api/lab")
    demo_dir: Path = settings.fixtures_dir / "demo_portfolios"
    rules = load_rule_set(settings.fixtures_dir / "rules" / "demo.yaml")
    cache: dict[str, tuple[float, dict]] = {}
    fetch_lock = threading.Lock()

    def portfolios() -> dict[str, Path]:
        return {p.stem: p for p in sorted(demo_dir.glob("*.json"))}

    @r.get("/demo-portfolios")
    def demo_portfolios():
        out = []
        for pid, path in portfolios().items():
            snap, ctx, meta = load_portfolio(path)
            out.append({
                "id": pid, "name": meta["name"], "description": meta["description"], "fictional": True,
                "price_date": meta["price_date"], "net_liquidation": snap.net_liquidation, "cash": snap.cash,
                "rule_set": rules.version,
                "positions": [{"symbol": p.symbol, "name": ctx.asset(p.symbol).name, "market_value": p.market_value,
                               "weight": snap.weight_nav(p.market_value), "sleeve": ctx.asset(p.symbol).sleeve.value}
                              for p in snap.positions],
                "findings": [v.model_dump(mode="json") for v in evaluate(snap, rules, ctx)],
            })
        return out

    @r.post("/gate")
    def gate(req: GateRequest):
        path = portfolios().get(req.portfolio_id)
        if path is None:
            raise HTTPException(404, "unknown demo portfolio")
        symbol = req.symbol.upper()
        if not TICKER.match(symbol):
            raise HTTPException(422, "invalid symbol")
        snap, ctx, _ = load_portfolio(path)
        if symbol not in ctx.assets:  # unknown names in the demo are treated as satellite stocks
            ctx.assets[symbol] = Asset(symbol=symbol, asset_type=AssetType.STOCK, sleeve=Sleeve.SATELLITE)
        try:
            result = evaluate_trade(snap, TradeProposal(symbol=symbol, side=req.side, amount_usd=req.amount_usd,
                                                        attestations=req.attestations), rules, ctx)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return result.model_dump(mode="json")

    @r.get("/companies")
    def companies():
        return [{"ticker": t} for t in settings.curated]

    @r.get("/companies/{ticker}/snapshot")
    def snapshot(ticker: str, lang: Literal["zh", "en"] = Query("en")):
        t = ticker.upper()
        if t not in settings.curated:
            raise HTTPException(404, "not in the Lab company list")
        key = f"{t}:{lang}"
        hit = cache.get(key)
        if hit and time.monotonic() - hit[0] < settings.snapshot_ttl_seconds:
            return hit[1]
        with fetch_lock:  # one cold build at a time: bounds memory (~100 MB each) and SEC traffic
            hit = cache.get(key)
            if hit and time.monotonic() - hit[0] < settings.snapshot_ttl_seconds:
                return hit[1]
            return _build_snapshot(t, lang, key, hit)

    def _build_snapshot(t: str, lang: str, key: str, hit):
        try:
            client = client_factory()
            cik = client.cik_for(t)
            fin = build_financials(client.company_facts(cik), quarters=8)
        except Exception as e:  # SEC unreachable, rate-limited or misconfigured
            log.warning("snapshot %s: SEC fetch failed: %s", t, e)
            if hit:  # a stale snapshot beats an error page
                return hit[1]
            raise HTTPException(503, "SEC data is temporarily unavailable. Please try again in a few minutes.") from e
        if not fin.supported:
            raise HTTPException(422, fin.reason)
        quarters = []
        for q in fin.quarters:
            metrics = {}
            for m in DISPLAY_METRICS:
                mv = q.metrics.get(m)
                if mv is None:
                    continue
                src = mv.sources[0].accn if mv.sources else None
                metrics[m] = {"value": mv.value, "unit": mv.unit, "label": label(m, lang), "derived": mv.derived,
                              "derivation": mv.derivation, "source": source_url(fin.cik, src) if src else None}
            quarters.append({"end": str(q.end), "fiscal_label": q.fiscal_label, "metrics": metrics})
        segments = []
        try:
            from investment_data.segments import load_segment_facts
            ser = segment_series(load_segment_facts(client, cik))
            last_end = fin.quarters[-1].end
            for (metric, member), s in sorted(ser.items()):
                if last_end in s and metric != "product_revenue":
                    qv = s[last_end]
                    src = qv.sources[0].accn if qv.sources else None
                    segments.append({"metric": metric, "member": member_label(member), "value": qv.value,
                                     "label": label(metric, lang, member_label(member)), "period_end": str(last_end),
                                     "source": source_url(fin.cik, src) if src else None})
        except Exception:  # segment data is optional; the page still works without it
            segments = []
        body = {"ticker": t, "company": fin.name, "cik": fin.cik, "quarters": quarters, "segments": segments,
                "note": "Company totals from SEC XBRL filings. Derived values are computed from reported figures."}
        cache[key] = (time.monotonic(), body)
        return body

    @r.get("/theses/{ticker}")
    def theses(ticker: str, lang: Literal["zh", "en"] = Query("en")):
        from investment_ai.theses import examples
        t = ticker.upper()
        if not TICKER.match(t):
            raise HTTPException(422, "invalid ticker")
        return examples(t, t, lang, settings.fixtures_dir / "example_theses.yaml")

    return r
