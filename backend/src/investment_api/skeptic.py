"""Public Lab AI skeptic (DESIGN §11.4): a visitor's one-line thesis, argued against with SEC evidence.

Guards, in order: kill switch (LAB_SKEPTIC_ENABLED=1 and an API key), SEC-registered US tickers (curated ones are the
ones the answers were tested on; any other is allowed but flagged, and bounded per visitor and per day), input length,
cached answers for repeated theses, per-visitor daily limit, a daily and a monthly spending cap, one model
call at a time, and the same output validation as the private workflow (fail closed). Visitor inputs are
kept for `retention_days` (30) and then deleted; IPs are stored only as salted hashes that rotate daily. Spending totals
contain no visitor data and are kept so the monthly cap stays correct after a purge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from investment_ai.angles import AngleError, normalize_angles
from investment_ai.evidence import ITEM_NAMES, label
from investment_ai.explain import EXPLAIN_VERSION, latest_period, run_explainer
from investment_ai.lab_pack import LabCompany, lab_pack, load_lab_company
from investment_ai.ledger import AIRun, BudgetExceeded
from investment_ai.research import LAB_ATTEMPTS, LAB_MAX_TOKENS, PROMPT_VERSION, run_research_skeptic

log = logging.getLogger("investment_api.skeptic")

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TICKER = re.compile(r"^[A-Z][A-Z\-]{0,9}$")
EXPLAIN_MARK = "__explain__"  # stored in `thesis` for quarter explanations: counted separately from skeptic requests


def norm_ticker(text: str) -> str:
    t = text.strip().upper().replace(".", "-")  # SEC lists class shares as BRK-B
    if not _TICKER.match(t):
        raise HTTPException(422, "invalid ticker")
    return t



def normalize_thesis(text: str) -> str:
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", text)).strip()


class LabStore:
    """SQLite store for the public skeptic. Also acts as the budget ledger for run_research_skeptic."""

    def __init__(self, path: str | Path, *, daily_budget: float, monthly_budget: float, per_ip_daily: int,
                 retention_days: int = 30, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.daily_budget, self.monthly_budget = daily_budget, monthly_budget
        self.per_ip_daily, self.retention_days = per_ip_daily, retention_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS spend (id INTEGER PRIMARY KEY, at TEXT NOT NULL, cost REAL NOT NULL,
                                                  status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, at TEXT NOT NULL, ip_hash TEXT NOT NULL, ticker TEXT NOT NULL,
                    lang TEXT NOT NULL, thesis TEXT NOT NULL, cache_key TEXT NOT NULL, cached INTEGER NOT NULL,
                    status TEXT NOT NULL, cost REAL NOT NULL, attempts INTEGER NOT NULL, model TEXT,
                    prompt_version TEXT, output TEXT, validation TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS requests_ip ON requests (ip_hash, at);
                CREATE INDEX IF NOT EXISTS requests_cache ON requests (cache_key, status);
            """)
            if db.execute("SELECT 1 FROM meta WHERE key='salt'").fetchone() is None:
                db.execute("INSERT INTO meta VALUES ('salt', ?)", (secrets.token_hex(16),))
            self._salt = db.execute("SELECT value FROM meta WHERE key='salt'").fetchone()[0]

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # -- identity and retention -------------------------------------------------------------------
    def ip_hash(self, ip: str) -> str:
        """Salted and rotated daily: requests can be counted per day but not linked across days."""
        return hashlib.sha256(f"{self._salt}:{self.clock().date()}:{ip}".encode()).hexdigest()[:20]

    def purge(self) -> int:
        cutoff = (self.clock() - timedelta(days=self.retention_days)).isoformat()
        with self._lock, self._db() as db:
            return db.execute("DELETE FROM requests WHERE at < ?", (cutoff,)).rowcount

    # -- budgets ------------------------------------------------------------------------------------
    def _spent_since(self, since: datetime) -> float:
        with self._db() as db:
            return db.execute("SELECT COALESCE(SUM(cost), 0) FROM spend WHERE at >= ?", (since.isoformat(),)).fetchone()[0]

    def spent_today(self) -> float:
        now = self.clock()
        return self._spent_since(now.replace(hour=0, minute=0, second=0, microsecond=0))

    def spent_this_month(self) -> float:
        now = self.clock()
        return self._spent_since(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))

    def check(self, estimated_cost: float) -> None:  # Ledger interface
        today, month = self.spent_today(), self.spent_this_month()
        if today + estimated_cost > self.daily_budget + 1e-9:
            raise BudgetExceeded(f"daily Lab budget ${self.daily_budget:.2f} reached")
        if month + estimated_cost > self.monthly_budget + 1e-9:
            raise BudgetExceeded(f"monthly LLM budget ${self.monthly_budget:.2f} reached")

    def amend(self, run: AIRun) -> None:  # Ledger interface: only spending is stored, which does not change
        return None

    def record(self, run: AIRun) -> AIRun:  # Ledger interface: spend only, no visitor data
        with self._lock, self._db() as db:
            db.execute("INSERT INTO spend (at, cost, status) VALUES (?, ?, ?)",
                       (run.at.isoformat(), run.cost_usd, run.status))
        return run

    def remaining_today(self) -> float:
        return max(0.0, min(self.daily_budget - self.spent_today(), self.monthly_budget - self.spent_this_month()))

    # -- visitor requests ---------------------------------------------------------------------------
    def requests_today(self, ip_hash: str, explain: bool = False) -> int:
        start = self.clock().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        op = "=" if explain else "!="
        with self._db() as db:
            return db.execute(f"SELECT COUNT(*) FROM requests WHERE ip_hash=? AND at>=? AND cached=0 AND thesis {op} ?",
                              (ip_hash, start, EXPLAIN_MARK)).fetchone()[0]

    def recently_failed(self, cache_key: str, hours: int = 6) -> bool:
        """A failed attempt for this key in the last hours (a company that fails is not retried by every visitor)."""
        since = (self.clock() - timedelta(hours=hours)).isoformat()
        with self._db() as db:
            return db.execute("SELECT 1 FROM requests WHERE cache_key=? AND at>=? AND cached=0 AND status='invalid' "
                              "LIMIT 1", (cache_key, since)).fetchone() is not None

    def cached(self, cache_key: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT output, model, prompt_version FROM requests WHERE cache_key=? AND status='ok' "
                             "ORDER BY at DESC LIMIT 1", (cache_key,)).fetchone()
        return None if row is None else {"output": json.loads(row[0]), "model": row[1], "prompt_version": row[2]}

    def add_request(self, **row) -> None:
        cols = ("id", "at", "ip_hash", "ticker", "lang", "thesis", "cache_key", "cached", "status", "cost",
                "attempts", "model", "prompt_version", "output", "validation", "error")
        values = [row.get(c) for c in cols]
        with self._lock, self._db() as db:
            db.execute(f"INSERT INTO requests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)


class SkepticRequest(BaseModel):
    ticker: str = Field(max_length=10)
    thesis: str = Field(min_length=10, max_length=400)
    lang: Literal["zh", "en"] = "en"
    stance: Literal["long", "short"] = "long"  # bullish or bearish thesis; the skeptic argues the other way
    angles: list[str] = Field(default_factory=list, max_length=4)  # optional focus angles (investment_ai.angles)


class ExplainRequest(BaseModel):
    ticker: str = Field(max_length=10)
    lang: Literal["zh", "en"] = "en"


def cache_key(ticker: str, lang: str, thesis: str, stance: str = "long", angles: list[str] | tuple = ()) -> str:
    chosen = "|".join(sorted(a.lower() for a in angles))
    return hashlib.sha256(f"{ticker}|{lang}|{stance}|{thesis.lower()}|{chosen}|{PROMPT_VERSION}".encode()).hexdigest()


def render(output: dict, pack, lang: str) -> dict:
    """Attach the cited evidence (label, period, value, filing link) so the page can show its sources."""
    by_id = {i.fact_id: i for i in pack.items} if pack is not None else {}
    cited = {}
    for c in [*output.get("bull_case", []), *output.get("bear_case", [])]:
        for ref in c.get("evidence_refs", []):
            item = by_id.get(ref)
            if item is not None:
                cited[ref] = {"label": label(item.metric, lang, item.member), "period": item.fiscal_label or item.period_end,
                              "display": item.display, "derived": item.derived, "note": item.note, "source": item.source}
    sources = {}
    for c in [*output.get("bull_case", []), *output.get("bear_case", [])]:
        for q in c.get("quotes", []):
            p = pack.passage(q.get("source_id", "")) if pack is not None else None
            if p is not None:
                sources[p.source_id] = {"document": ITEM_NAMES.get(p.item, p.item), "url": p.url}
    return {**output, "evidence": cited, "sources": sources}


def add_skeptic_routes(r: APIRouter, settings, client_factory, fetch_lock: threading.Lock, provider_factory) -> SimpleNamespace:
    store = LabStore(settings.lab_data_dir / "lab.sqlite3", daily_budget=settings.lab_daily_budget_usd,
                     monthly_budget=settings.monthly_budget_usd, per_ip_daily=settings.skeptic_per_ip_daily,
                     retention_days=settings.retention_days) if settings.skeptic_enabled else None
    companies: dict[str, tuple[datetime, LabCompany]] = {}
    model_lock = threading.Lock()
    if store is not None:
        store.purge()

    def enabled() -> tuple[bool, str | None]:
        if store is None:
            return False, "The AI skeptic is not enabled on this server."
        try:
            provider_factory()
        except Exception:
            return False, "The AI skeptic is not configured on this server."
        return True, None

    def visitor(request: Request) -> str:
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "?")
        return store.ip_hash(ip)

    custom_use: dict = {"day": "", "total": 0, "who": {}}
    custom_order: list[str] = []  # non-curated companies held in memory, oldest first

    def custom_allowed(who: str | None) -> bool:
        """Counts cold loads of non-curated companies (SEC traffic and memory). In memory only."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if custom_use["day"] != day:
            custom_use.update(day=day, total=0, who={})
        if custom_use["total"] >= settings.custom_global_daily:
            return False
        if who is not None and custom_use["who"].get(who, 0) >= settings.custom_per_ip_daily:
            return False
        custom_use["total"] += 1
        if who is not None:
            custom_use["who"][who] = custom_use["who"].get(who, 0) + 1
        return True

    def load_company(ticker: str, who: str | None = None) -> LabCompany:
        hit = companies.get(ticker)
        now = datetime.now(timezone.utc)
        if hit and (now - hit[0]).total_seconds() < settings.snapshot_ttl_seconds:
            return hit[1]
        custom = ticker not in settings.curated
        if custom and not custom_allowed(who):
            raise HTTPException(429, "Too many companies looked up today. Try one of the listed companies, "
                                     "or come back tomorrow.")
        with fetch_lock:
            try:
                company = load_lab_company(client_factory(), ticker)
            except KeyError:
                raise HTTPException(404, f"{ticker} was not found among SEC-registered tickers. The Lab covers "
                                         "US-listed companies that file with the SEC.")
        if not company.pack.items:
            raise HTTPException(422, "no financial data for this company")
        companies[ticker] = (now, company)
        if custom:
            if ticker in custom_order:
                custom_order.remove(ticker)
            custom_order.append(ticker)
            while len(custom_order) > settings.custom_cache_max:
                companies.pop(custom_order.pop(0), None)
            cik = company.pack.cik  # keep the disk cache for the curated list only: a visitor could fill it
            for f in settings.cache_dir.glob(f"*{cik}*"):
                try:
                    f.unlink()
                except OSError:
                    pass
        return company

    @r.get("/skeptic/status")
    def status(request: Request):
        ok, reason = enabled()
        if not ok:
            return {"enabled": False, "reason": reason}
        store.purge()
        used = store.requests_today(visitor(request))
        return {"enabled": True, "per_visitor_daily": store.per_ip_daily,
                "visitor_remaining": max(0, store.per_ip_daily - used),
                "budget_available": store.remaining_today() > 0.01, "prompt_version": PROMPT_VERSION}

    @r.post("/skeptic")
    def skeptic(req: SkepticRequest, request: Request):
        ok, reason = enabled()
        if not ok:
            raise HTTPException(503, reason)
        ticker = norm_ticker(req.ticker)
        thesis = normalize_thesis(req.thesis)
        if len(thesis) < 10:
            raise HTTPException(422, "thesis is too short")
        try:
            angles = normalize_angles(req.angles)
        except AngleError as e:
            raise HTTPException(422, str(e))
        ip_hash, key, now = visitor(request), cache_key(ticker, req.lang, thesis, req.stance, angles), store.clock()
        base = dict(id=uuid.uuid4().hex, at=now.isoformat(), ip_hash=ip_hash, ticker=ticker, lang=req.lang,
                    thesis=thesis, cache_key=key)

        hit = store.cached(key)
        if hit is not None:
            store.add_request(**base, cached=1, status="ok", cost=0.0, attempts=0, model=hit["model"],
                              prompt_version=hit["prompt_version"], output=json.dumps(hit["output"]))
            try:
                pack = lab_pack(load_company(ticker, ip_hash), thesis, angles)
            except Exception:  # the answer can still be shown without source links
                pack = None
            return {"ok": True, "cached": True, "evaluated": ticker in settings.curated, "result": render(hit["output"], pack, req.lang),
                    "model": hit["model"], "prompt_version": hit["prompt_version"]}

        if store.requests_today(ip_hash) >= store.per_ip_daily:
            raise HTTPException(429, f"Daily limit reached ({store.per_ip_daily} new theses per visitor). "
                                     "Please come back tomorrow.")
        if store.remaining_today() <= 0.01:
            raise HTTPException(503, "Today's AI budget for the Lab is used up. Please try again tomorrow.")

        try:
            pack = lab_pack(load_company(ticker, ip_hash), thesis, angles)
        except HTTPException:
            raise
        except Exception as e:
            log.warning("skeptic %s: SEC fetch failed: %s", ticker, e)
            raise HTTPException(503, "SEC data is temporarily unavailable. Please try again in a few minutes.") from e

        with model_lock:  # one model call at a time keeps spending and memory predictable
            result = run_research_skeptic(pack, thesis, provider_factory(), store, surface="lab", language=req.lang,
                                          stance=req.stance, plain=True, angles=angles,
                                          max_attempts=LAB_ATTEMPTS, max_tokens=LAB_MAX_TOKENS)
        cost = sum(run.cost_usd for run in result.runs)
        main_runs = [r for r in result.runs if r.task == "research_skeptic"]
        last = main_runs[-1] if main_runs else None
        status_ = ("ok" if result.ok else last.status if last and last.status in ("budget_blocked", "error")
                   else "invalid")
        store.add_request(**base, cached=0, status=status_, cost=cost, attempts=sum(1 for r in result.runs if r.task == "research_skeptic"),
                          model=last.model if last else None, prompt_version=PROMPT_VERSION,
                          output=json.dumps(result.output.model_dump()) if result.ok else None,
                          validation=result.report.model_dump_json() if result.report else None,
                          error=result.error)
        if status_ == "budget_blocked":
            raise HTTPException(503, "Today's AI budget for the Lab is used up. Please try again tomorrow.")
        if status_ == "error":
            log.warning("skeptic %s: model error: %s", ticker, result.error)
            raise HTTPException(502, "The AI model is unavailable right now. Please try again later.")
        if not result.ok:
            rep = result.report
            return {"ok": False, "cached": False, "attempts": sum(1 for r in result.runs if r.task == "research_skeptic"),
                    "checks": {"advice": len(rep.forbidden) if rep else 0,
                               "ungrounded_numbers": len(rep.ungrounded) if rep else 0,
                               "other": (len(rep.bad_refs) + len(rep.mislabeled) + len(rep.bad_quotes) + len(rep.errors)
                                         + len(rep.echoed))
                               if rep else 0}}
        return {"ok": True, "cached": False, "evaluated": ticker in settings.curated,
                "attempts": sum(1 for r in result.runs if r.task == "research_skeptic"),
                "result": {**render(result.output.model_dump(), pack, req.lang),
                           "computed": result.report.computed if result.report else []},
                "model": last.model if last else None, "prompt_version": PROMPT_VERSION}

    @r.post("/explain")
    def explain(req: ExplainRequest, request: Request):
        """Plain-language explanation of a company's latest quarter. Cached per company, language and quarter, so a
        page opened by many visitors costs one model call; only a cache miss uses the visitor's explain quota."""
        ok, reason = enabled()
        if not ok:
            raise HTTPException(503, reason)
        ticker, ip_hash = norm_ticker(req.ticker), visitor(request)
        try:
            company = load_company(ticker, ip_hash)
        except HTTPException:
            raise
        except Exception as e:
            log.warning("explain %s: SEC fetch failed: %s", ticker, e)
            raise HTTPException(503, "SEC data is temporarily unavailable. Please try again in a few minutes.") from e
        from investment_ai.explain import explain_pack
        pack = explain_pack(company)
        period = latest_period(pack)
        key = hashlib.sha256(f"explain|{ticker}|{req.lang}|{period}|{EXPLAIN_VERSION}".encode()).hexdigest()
        evaluated = ticker in settings.curated

        def shown(hit: dict, cached: bool) -> dict:
            out = {k: v for k, v in hit["output"].items() if k != "computed"}
            return {"ok": True, "cached": cached, "evaluated": evaluated, "period": period,
                    "result": {**render(out, pack, req.lang), "computed": hit["output"].get("computed", [])},
                    "model": hit["model"], "prompt_version": hit["prompt_version"]}

        hit = store.cached(key)
        if hit is not None:
            return shown(hit, True)
        if store.recently_failed(key):
            return {"ok": False, "reason": "unavailable", "evaluated": evaluated}
        if store.requests_today(ip_hash, explain=True) >= settings.explain_per_ip_daily:
            raise HTTPException(429, "Daily limit of new explanations reached. Please come back tomorrow.")
        if store.remaining_today() <= settings.explain_reserve_usd:  # the rest of the day is kept for the skeptic
            raise HTTPException(503, "Today's AI budget for explanations is used up. Please try again tomorrow.")
        base = dict(id=uuid.uuid4().hex, at=store.clock().isoformat(), ip_hash=ip_hash, ticker=ticker, lang=req.lang,
                    thesis=EXPLAIN_MARK, cache_key=key)
        with model_lock:
            hit = store.cached(key)  # another visitor may have just finished the same company
            if hit is not None:
                return shown(hit, True)
            _, result = run_explainer(company, provider_factory(), store, language=req.lang)
        cost = sum(run.cost_usd for run in result.runs)
        main_runs = [x for x in result.runs if x.task == "research_skeptic"]
        last = main_runs[-1] if main_runs else None
        status_ = ("ok" if result.ok else last.status if last and last.status in ("budget_blocked", "error")
                   else "invalid")
        output = ({**result.output.model_dump(), "computed": result.report.computed if result.report else []}
                  if result.ok else None)
        store.add_request(**base, cached=0, status=status_, cost=cost, attempts=len(main_runs),
                          model=last.model if last else None, prompt_version=EXPLAIN_VERSION,
                          output=json.dumps(output) if output else None,
                          validation=result.report.model_dump_json() if result.report else None, error=result.error)
        if status_ == "budget_blocked":
            raise HTTPException(503, "Today's AI budget for explanations is used up. Please try again tomorrow.")
        if status_ == "error":
            log.warning("explain %s: model error: %s", ticker, result.error)
            raise HTTPException(502, "The AI model is unavailable right now. Please try again later.")
        if not result.ok:
            return {"ok": False, "reason": "unavailable", "evaluated": evaluated}
        return shown({"output": output, "model": last.model if last else None, "prompt_version": EXPLAIN_VERSION}, False)

    # Shared with the memo workflow (memo_lab.py): same store, budget, visitor hashing and model lock.
    return SimpleNamespace(store=store, enabled=enabled, visitor=visitor, load_company=load_company,
                           model_lock=model_lock, provider_factory=provider_factory)
