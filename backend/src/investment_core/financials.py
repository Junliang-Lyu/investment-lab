"""Quarterly financials from SEC XBRL "companyfacts" data. Pure, no IO.

See docs/DESIGN.md §9. Input is the parsed JSON of
https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

Key points:
- Periods are classified by their start/end dates, never by the filing's fy/fp
  (a later filing repeats earlier periods as comparatives).
- The same period reported by several filings keeps the latest filed value
  (restatements win); every value keeps its accession number as evidence.
- 10-Q cash-flow statements are year-to-date. Quarterly values are derived by
  differencing cumulative values (Q2 = 6M - 3M, Q3 = 9M - 6M, Q4 = FY - 9M).
  Derived values are flagged and carry the formula.
- Direct 3-month values are preferred over derived ones.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}

# Concept fallbacks, in priority order. Chosen per period.
METRIC_CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}

# Balance-sheet (point-in-time) concepts, read at each quarter end.
INSTANT_CONCEPTS: dict[str, list[str]] = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["MarketableSecuritiesCurrent", "ShortTermInvestments",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "long_term_debt_noncurrent": ["LongTermDebtNoncurrent"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
}

DISPLAY_METRICS = ["revenue", "gross_profit", "gross_margin", "operating_income", "operating_margin",
                   "net_income", "cfo", "capex", "fcf", "cash_and_investments", "net_cash"]


class Fact(BaseModel):
    concept: str
    start: date
    end: date
    val: float
    accn: str
    form: str
    filed: date

    @property
    def months(self) -> int | None:
        days = (self.end - self.start).days
        for months, (lo, hi) in {3: (80, 100), 6: (170, 195), 9: (260, 285), 12: (350, 380)}.items():
            if lo <= days <= hi:
                return months
        return None


class QuarterValue(BaseModel):
    start: date
    end: date
    value: float
    concept: str
    derived: bool = False
    derivation: str | None = None
    sources: list[Fact] = Field(default_factory=list)


class MetricValue(BaseModel):
    evidence_id: str
    metric: str
    value: float
    unit: str = "USD"
    concept: str | None = None
    derived: bool = False
    derivation: str | None = None
    sources: list[Fact] = Field(default_factory=list)


class QuarterRow(BaseModel):
    start: date
    end: date
    fiscal_label: str | None = None
    metrics: dict[str, MetricValue] = Field(default_factory=dict)


class CompanyFinancials(BaseModel):
    cik: str
    name: str
    supported: bool
    reason: str | None = None
    quarters: list[QuarterRow] = Field(default_factory=list)


def source_url(cik: str | int, accn: str) -> str:
    """EDGAR filing index page for an accession number."""
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accn.replace('-', '')}/{accn}-index.htm")


# --- extraction -----------------------------------------------------------

def extract_facts(companyfacts: dict[str, Any], concept: str, unit: str = "USD") -> list[Fact]:
    """Duration facts for one concept from 10-Q/10-K filings, one per period (latest filed)."""
    raw = (companyfacts.get("facts", {}).get("us-gaap", {}).get(concept, {})
           .get("units", {}).get(unit, []))
    best: dict[tuple[date, date], Fact] = {}
    for r in raw:
        if r.get("form") not in FORMS or "start" not in r:
            continue
        f = Fact(concept=concept, start=r["start"], end=r["end"], val=r["val"],
                 accn=r["accn"], form=r["form"], filed=r["filed"])
        if f.months is None:
            continue
        key = (f.start, f.end)
        if key not in best or (f.filed, f.accn) > (best[key].filed, best[key].accn):
            best[key] = f
    return sorted(best.values(), key=lambda f: (f.start, f.end))


class InstantFact(BaseModel):
    concept: str
    end: date
    val: float
    accn: str
    form: str
    filed: date


def extract_instant(companyfacts: dict[str, Any], concept: str, unit: str = "USD") -> dict[date, InstantFact]:
    """Point-in-time facts (no start date) from 10-Q/10-K, one per date (latest filed)."""
    raw = (companyfacts.get("facts", {}).get("us-gaap", {}).get(concept, {})
           .get("units", {}).get(unit, []))
    best: dict[date, InstantFact] = {}
    for r in raw:
        if r.get("form") not in FORMS or "start" in r:
            continue
        f = InstantFact(concept=concept, end=r["end"], val=r["val"], accn=r["accn"], form=r["form"], filed=r["filed"])
        if f.end not in best or (f.filed, f.accn) > (best[f.end].filed, best[f.end].accn):
            best[f.end] = f
    return best


def _instant_series(companyfacts: dict[str, Any], metric: str) -> dict[date, InstantFact]:
    merged: dict[date, InstantFact] = {}
    for concept in INSTANT_CONCEPTS[metric]:
        for end, f in extract_instant(companyfacts, concept).items():
            merged.setdefault(end, f)
    return merged


def quarterly_series(facts: list[Fact]) -> dict[date, QuarterValue]:
    """Quarter values keyed by period end: direct 3-month facts, else derived from YTD."""
    out: dict[date, QuarterValue] = {}
    direct = {f.end: f for f in facts if f.months == 3}
    for end, f in direct.items():
        out[end] = QuarterValue(start=f.start, end=f.end, value=f.val, concept=f.concept, sources=[f])

    # Group cumulative facts by fiscal-year start (the start date shared by YTD facts).
    groups: dict[date, dict[date, Fact]] = defaultdict(dict)
    for f in facts:
        groups[f.start][f.end] = f

    def cumulative(start: date, end: date) -> tuple[float, list[Fact], str] | None:
        """Value from `start` through `end`: a reported YTD fact, or a sum of direct quarters."""
        f = groups.get(start, {}).get(end)
        if f is not None:
            return f.val, [f], f"{f.months}M YTD"
        total, used, cursor = 0.0, [], start
        while cursor <= end:
            q = next((d for d in direct.values() if d.start == cursor), None)
            if q is None or q.end > end:
                return None
            total, used, cursor = total + q.val, used + [q], q.end + timedelta(days=1)
        return (total, used, "sum of quarters") if used and used[-1].end == end else None

    for start, by_end in groups.items():
        ends = sorted(by_end)
        # Every YTD end in this fiscal year is a quarter end; walk them in order,
        # stepping back three months to find the previous cumulative point.
        for end in ends:
            f = by_end[end]
            if f.months in (None, 3) or end in out:
                continue
            prev_candidates = [e for e in _quarter_ends_before(start, end, facts)]
            for prev_end in prev_candidates:
                prev = cumulative(start, prev_end)
                if prev is None:
                    continue
                prev_val, prev_src, prev_desc = prev
                q_start = prev_end + timedelta(days=1)
                if not 80 <= (end - q_start).days <= 100:
                    continue
                out[end] = QuarterValue(
                    start=q_start, end=end, value=f.val - prev_val, concept=f.concept, derived=True,
                    derivation=f"{f.months}M YTD ({f.accn}) - {prev_desc} to {prev_end} "
                               f"({', '.join(s.accn for s in prev_src)})",
                    sources=[f, *prev_src],
                )
                break
    return out


def _quarter_ends_before(start: date, end: date, facts: list[Fact]) -> list[date]:
    """Known period ends within [start, end) that could be the previous quarter end,
    latest first."""
    ends = {f.end for f in facts if start < f.end < end}
    return sorted(ends, reverse=True)


def fiscal_label(start: date, end: date, facts: list[Fact]) -> str | None:
    """e.g. 'FY2026 Q2'. A fiscal-year start is the start date of any YTD (6/9/12M) fact.

    The fiscal year is named after the calendar year in which it ends, which is
    how companies with non-calendar years (e.g. Micron) label theirs.
    """
    fy_starts = {f.start for f in facts if f.months in (6, 9, 12)}
    candidates = [s for s in fy_starts if s <= start and (end - s).days <= 380]
    if not candidates:
        return None
    s = max(candidates)
    q = max(1, min(4, round((end - s).days / 91.3)))
    return f"FY{(s + timedelta(days=364)).year} Q{q}"


def _merged_series(companyfacts: dict[str, Any], metric: str) -> dict[date, QuarterValue]:
    merged: dict[date, QuarterValue] = {}
    for concept in METRIC_CONCEPTS[metric]:
        for end, qv in quarterly_series(extract_facts(companyfacts, concept)).items():
            merged.setdefault(end, qv)
    return merged


def build_financials(companyfacts: dict[str, Any], quarters: int = 8) -> CompanyFinancials:
    cik = str(companyfacts.get("cik", "")).zfill(10)
    name = companyfacts.get("entityName", "")
    series = {m: _merged_series(companyfacts, m) for m in METRIC_CONCEPTS}
    revenue = series["revenue"]
    if not revenue:
        return CompanyFinancials(cik=cik, name=name, supported=False,
                                 reason="No standard revenue concept in 10-Q/10-K filings "
                                        "(banks, insurers and similar are not supported in v1)")

    instants = {m: _instant_series(companyfacts, m) for m in INSTANT_CONCEPTS}
    label_facts = [f for c in METRIC_CONCEPTS["revenue"] for f in extract_facts(companyfacts, c)]
    rows: list[QuarterRow] = []
    for end in sorted(revenue)[-quarters:]:
        rq = revenue[end]
        row = QuarterRow(start=rq.start, end=end, fiscal_label=fiscal_label(rq.start, end, label_facts))

        def put(metric: str, qv: QuarterValue | None) -> None:
            if qv is not None:
                row.metrics[metric] = MetricValue(
                    evidence_id=f"{cik}:{metric}:{end}", metric=metric, value=qv.value,
                    concept=qv.concept, derived=qv.derived, derivation=qv.derivation, sources=qv.sources)

        for metric in ("revenue", "gross_profit", "operating_income", "net_income", "cfo", "capex"):
            put(metric, series[metric].get(end))

        rev = row.metrics["revenue"]
        cost = series["cost_of_revenue"].get(end)
        if "gross_profit" not in row.metrics and cost is not None:
            row.metrics["gross_profit"] = MetricValue(
                evidence_id=f"{cik}:gross_profit:{end}", metric="gross_profit",
                value=rev.value - cost.value, derived=True,
                derivation=f"revenue ({rev.concept}) - cost of revenue ({cost.concept})",
                sources=[*rev.sources, *cost.sources])
        cfo, capex = row.metrics.get("cfo"), row.metrics.get("capex")
        if cfo and capex:
            row.metrics["fcf"] = MetricValue(
                evidence_id=f"{cik}:fcf:{end}", metric="fcf", value=cfo.value - capex.value, derived=True,
                derivation="operating cash flow - capital expenditures", sources=[*cfo.sources, *capex.sources])
        # Balance sheet at quarter end. Total debt only when the non-current tag is reported for this
        # date; tags such as LongTermDebt are ambiguous (sometimes incl. the current portion), so we
        # do not guess.
        for metric, series_i in instants.items():
            f = series_i.get(end)
            if f is not None:
                row.metrics[metric] = MetricValue(
                    evidence_id=f"{cik}:{metric}:{end}", metric=metric, value=f.val, concept=f.concept,
                    sources=[Fact(concept=f.concept, start=end, end=end, val=f.val, accn=f.accn, form=f.form,
                                  filed=f.filed)])
        cash, sti = row.metrics.get("cash"), row.metrics.get("short_term_investments")
        if cash:
            total = cash.value + (sti.value if sti else 0.0)
            row.metrics["cash_and_investments"] = MetricValue(
                evidence_id=f"{cik}:cash_and_investments:{end}", metric="cash_and_investments", value=total,
                derived=True, derivation="cash and equivalents + short-term investments" if sti else "cash and equivalents",
                sources=[*cash.sources, *(sti.sources if sti else [])])
            ltd, cur = row.metrics.get("long_term_debt_noncurrent"), row.metrics.get("debt_current")
            if ltd:
                debt = ltd.value + (cur.value if cur else 0.0)
                row.metrics["total_debt"] = MetricValue(
                    evidence_id=f"{cik}:total_debt:{end}", metric="total_debt", value=debt, derived=True,
                    derivation="long-term debt (non-current) + current debt" if cur else "long-term debt (non-current)",
                    sources=[*ltd.sources, *(cur.sources if cur else [])])
                row.metrics["net_cash"] = MetricValue(
                    evidence_id=f"{cik}:net_cash:{end}", metric="net_cash", value=total - debt, derived=True,
                    derivation="cash and short-term investments - total debt",
                    sources=row.metrics["cash_and_investments"].sources + row.metrics["total_debt"].sources)
        for ratio, num in (("gross_margin", "gross_profit"), ("operating_margin", "operating_income")):
            n = row.metrics.get(num)
            if n and rev.value:
                row.metrics[ratio] = MetricValue(
                    evidence_id=f"{cik}:{ratio}:{end}", metric=ratio, value=n.value / rev.value, unit="ratio",
                    derived=True, derivation=f"{num} / revenue", sources=[])
        rows.append(row)
    return CompanyFinancials(cik=cik, name=name, supported=True, quarters=rows)
