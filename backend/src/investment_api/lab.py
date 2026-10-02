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

from .i18n import translate_findings, translate_gate
from .ratelimit import RateLimiter

log = logging.getLogger("investment_api.lab")

TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


class CustomPosition(BaseModel):
    symbol: str = Field(max_length=10)
    market_value: float = Field(gt=0, le=1e10)
    sleeve: Literal["core", "satellite"] = "satellite"


class CustomPortfolio(BaseModel):
    """A visitor's own holdings, typed in the browser. Used for this one check and never stored."""
    cash: float = Field(ge=0, le=1e10)
    positions: list[CustomPosition] = Field(default_factory=list, max_length=40)


class GateRequest(BaseModel):
    portfolio_id: str = Field(max_length=40)  # a demo portfolio id, or "custom" with `custom`
    custom: CustomPortfolio | None = None
    memo_id: str | None = Field(default=None, max_length=40)  # a Lab memo for this symbol (DESIGN §11.5)
    symbol: str = Field(max_length=10)
    side: Literal["buy", "sell"] = "buy"
    amount_usd: float = Field(gt=0, le=10_000_000)
    attestations: dict[str, bool] = Field(default_factory=dict)


def custom_snapshot(p: CustomPortfolio):
    """Snapshot and context from typed-in holdings (ETFs typed as core, stocks as satellite)."""
    from datetime import date
    from investment_core.models import Context, Position, Snapshot
    merged: dict[str, CustomPosition] = {}
    for pos in p.positions:
        sym = pos.symbol.strip().upper()
        if not TICKER.match(sym):
            raise HTTPException(422, f"invalid symbol: {pos.symbol}")
        if sym in merged:
            merged[sym] = merged[sym].model_copy(update={"market_value": merged[sym].market_value + pos.market_value})
        else:
            merged[sym] = pos.model_copy(update={"symbol": sym})
    nav = p.cash + sum(x.market_value for x in merged.values())
    if nav <= 0:
        raise HTTPException(422, "enter some cash or positions")
    snap = Snapshot(as_of=date.today(), net_liquidation=nav, cash=p.cash, source="lab-custom",
                    positions=[Position(symbol=s, quantity=0.0, market_value=x.market_value) for s, x in merged.items()])
    ctx = Context(assets={s: Asset(symbol=s, asset_type=AssetType.ETF if x.sleeve == "core" else AssetType.STOCK,
                                   sleeve=Sleeve(x.sleeve), exposure_tags=[]) for s, x in merged.items()})
    return snap, ctx


def build_router(settings, client_factory, provider_factory=None) -> APIRouter:
    r = APIRouter(prefix="/api/lab")
    demo_dir: Path = settings.fixtures_dir / "demo_portfolios"
    rules = load_rule_set(settings.fixtures_dir / "rules" / "demo.yaml")
    cache: dict[str, tuple[float, dict]] = {}
    fetch_lock = threading.Lock()

    def portfolios() -> dict[str, Path]:
        return {p.stem: p for p in sorted(demo_dir.glob("*.json"))}

    @r.get("/demo-portfolios")
    def demo_portfolios(lang: Literal["zh", "en"] = Query("en")):
        out = []
        for pid, path in portfolios().items():
            snap, ctx, meta = load_portfolio(path)
            out.append({
                "id": pid, "name": meta.get("name_zh") if lang == "zh" and meta.get("name_zh") else meta["name"],
                "description": meta.get("description_zh") if lang == "zh" and meta.get("description_zh")
                else meta["description"], "fictional": True,
                "price_date": meta["price_date"], "net_liquidation": snap.net_liquidation, "cash": snap.cash,
                "rule_set": rules.version,
                "positions": [{"symbol": p.symbol, "name": ctx.asset(p.symbol).name, "market_value": p.market_value,
                               "weight": snap.weight_nav(p.market_value), "sleeve": ctx.asset(p.symbol).sleeve.value}
                              for p in snap.positions],
                "findings": (translate_findings if lang == "zh" else list)(
                    [v.model_dump(mode="json") for v in evaluate(snap, rules, ctx)]),
            })
        return out

    memos: dict = {"store": None}  # set below once the memo routes exist

    @r.post("/gate")
    def gate(req: GateRequest, lang: Literal["zh", "en"] = Query("en")):
        symbol = req.symbol.upper()
        if not TICKER.match(symbol):
            raise HTTPException(422, "invalid symbol")
        if req.portfolio_id == "custom":
            if req.custom is None:
                raise HTTPException(422, "custom portfolio is missing")
            snap, ctx = custom_snapshot(req.custom)
        else:
            path = portfolios().get(req.portfolio_id)
            if path is None:
                raise HTTPException(404, "unknown demo portfolio")
            snap, ctx, _ = load_portfolio(path)
        if symbol not in ctx.assets:  # unknown names are treated as satellite stocks
            ctx.assets[symbol] = Asset(symbol=symbol, asset_type=AssetType.STOCK, sleeve=Sleeve.SATELLITE)
        memo_note = None
        if req.memo_id:
            from .memo_lab import gate_context
            store = memos["store"]
            rec = store.get(req.memo_id) if store is not None else None
            if rec is None:
                raise HTTPException(404, "memo not found")
            if rec["ticker"] != symbol:
                raise HTTPException(422, f"this memo is about {rec['ticker']}, not {symbol}")
            ctx.memos.pop(symbol, None)  # the visitor's memo replaces any demo memo for this symbol
            ctx.watchlist.pop(symbol, None)
            memo_note = gate_context(rec, symbol, ctx)
        try:
            result = evaluate_trade(snap, TradeProposal(symbol=symbol, side=req.side, amount_usd=req.amount_usd,
                                                        attestations=req.attestations), rules, ctx)
        except ValueError as e:
            raise HTTPException(422, str(e))
        body = result.model_dump(mode="json")
        body["memo_status"] = memo_note
        return translate_gate(body) if lang == "zh" else body

    @r.get("/companies")
    def companies():
        return [{"ticker": t} for t in settings.curated]

    custom_use: dict = {"day": "", "total": 0, "ips": {}}
    custom_keys: list[str] = []  # snapshot cache keys of non-curated companies, oldest first

    def _custom_allowed(ip: str) -> bool:
        """Counts cold builds of non-curated companies (each costs SEC traffic and memory). In memory only."""
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if custom_use["day"] != day:
            custom_use.update(day=day, total=0, ips={})
        who = RateLimiter.key(f"{day}:{ip}")
        if custom_use["total"] >= settings.custom_global_daily or custom_use["ips"].get(who, 0) >= settings.custom_per_ip_daily:
            return False
        custom_use["total"] += 1
        custom_use["ips"][who] = custom_use["ips"].get(who, 0) + 1
        return True

    @r.get("/companies/{ticker}/snapshot")
    def snapshot(ticker: str, request: Request, lang: Literal["zh", "en"] = Query("en")):
        t = ticker.upper().replace(".", "-")  # SEC lists class shares as BRK-B
        if not TICKER.match(t):
            raise HTTPException(422, "invalid ticker")
        custom = t not in settings.curated
        key = f"{t}:{lang}"
        hit = cache.get(key)
        if hit and time.monotonic() - hit[0] < settings.snapshot_ttl_seconds:
            return hit[1]
        if custom:
            ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
                request.client.host if request.client else "?")
            if not _custom_allowed(ip):
                raise HTTPException(429, "Too many companies looked up today. Try one of the listed companies, or come back tomorrow.")
        with fetch_lock:  # one cold build at a time: bounds memory (~100 MB each) and SEC traffic
            hit = cache.get(key)
            if hit and time.monotonic() - hit[0] < settings.snapshot_ttl_seconds:
                return hit[1]
            try:
                return _build_snapshot(t, lang, key, hit, custom)
            finally:
                if custom:
                    if key in cache and key not in custom_keys:
                        custom_keys.append(key)
                    while len(custom_keys) > settings.custom_cache_max:
                        cache.pop(custom_keys.pop(0), None)

    def _build_snapshot(t: str, lang: str, key: str, hit, custom: bool = False):
        cik = None
        try:
            client = client_factory()
            try:
                cik = client.cik_for(t)
            except KeyError:
                raise HTTPException(404, f"{t} was not found among SEC-registered tickers. The Lab covers US-listed "
                                         "companies that file with the SEC.")
            fin = build_financials(client.company_facts(cik), quarters=8)
        except HTTPException:
            raise
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
                "note": "公司合计数来自 SEC XBRL 申报文件；推导值由已披露数字计算得出。" if lang == "zh" else
                "Company totals from SEC XBRL filings. Derived values are computed from reported figures."}
        cache[key] = (time.monotonic(), body)
        if custom and cik:  # keep the disk cache for the curated list only: a visitor could otherwise fill it
            for f in settings.cache_dir.glob(f"*{cik}*"):
                try:
                    f.unlink()
                except OSError:
                    pass
        return body

    @r.get("/theses/{ticker}")
    def theses(ticker: str, lang: Literal["zh", "en"] = Query("en")):
        from investment_ai.theses import examples
        t = ticker.upper()
        if not TICKER.match(t):
            raise HTTPException(422, "invalid ticker")
        return examples(t, t, lang, settings.fixtures_dir / "example_theses.yaml")

    @r.get("/evals/latest")
    def evals_latest():
        """Latest adversarial eval of the AI skeptic (summary and per-case scores, without full answers)."""
        import json as _json
        path = settings.fixtures_dir / "evals" / "results" / "latest.json"
        if not path.exists():
            raise HTTPException(404, "no eval results yet")
        report = _json.loads(path.read_text(encoding="utf-8"))
        report["cases"] = [{k: v for k, v in c.items() if k != "output"} for c in report.get("cases", [])]
        return report

    from .skeptic import add_skeptic_routes
    if provider_factory is None:
        from investment_ai.providers import AnthropicProvider
        provider_factory = AnthropicProvider
    sk = add_skeptic_routes(r, settings, client_factory, fetch_lock, provider_factory)
    from .memo_lab import add_memo_routes
    memos["store"] = add_memo_routes(r, settings, sk)
    return r
