"""Evidence pack: the only facts the model may cite as facts.

Built from investment_core.financials. Includes year-over-year and
quarter-over-quarter changes so the model does not compute its own numbers.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel

from investment_core.filing_text import Passage
from investment_core.financials import CompanyFinancials, source_url

GROWTH_METRICS = ["revenue", "operating_income", "net_income", "cfo", "capex", "fcf"]


LABELS = {
    "zh": {"revenue": "收入", "gross_profit": "毛利", "gross_margin": "毛利率", "operating_income": "营业利润",
           "operating_margin": "营业利润率", "net_income": "净利润", "cfo": "经营现金流", "capex": "资本开支",
           "fcf": "自由现金流", "capex_to_revenue": "资本开支占收入比", "capex_to_cfo": "资本开支占经营现金流比",
           "fcf_margin": "自由现金流利润率", "net_to_operating_income": "净利润/营业利润",
           "cash": "现金及等价物", "short_term_investments": "短期投资", "cash_and_investments": "现金与短期投资",
           "long_term_debt_noncurrent": "长期债务（非流动）", "debt_current": "一年内到期债务", "total_debt": "总债务",
           "net_cash": "净现金（现金与短期投资 − 总债务）", "segment_revenue": "分部收入",
           "segment_operating_income": "分部营业利润", "segment_operating_margin": "分部营业利润率",
           "product_revenue": "产品线收入",
           "gross_margin_chg": "毛利率变化（百分点）", "operating_margin_chg": "营业利润率变化（百分点）",
           "fcf_margin_chg": "自由现金流利润率变化（百分点）", "capex_to_revenue_chg": "资本开支占收入比变化（百分点）",
           "capex_to_cfo_chg": "资本开支占经营现金流比变化（百分点）"},
    "en": {"revenue": "revenue", "gross_profit": "gross profit", "gross_margin": "gross margin",
           "operating_income": "operating income", "operating_margin": "operating margin", "net_income": "net income",
           "cfo": "operating cash flow", "capex": "capital expenditures", "fcf": "free cash flow",
           "capex_to_revenue": "capex as % of revenue", "capex_to_cfo": "capex as % of operating cash flow",
           "fcf_margin": "free cash flow margin", "net_to_operating_income": "net income / operating income",
           "cash": "cash and equivalents", "short_term_investments": "short-term investments",
           "cash_and_investments": "cash and short-term investments", "long_term_debt_noncurrent": "long-term debt",
           "debt_current": "current debt", "total_debt": "total debt", "net_cash": "net cash",
           "segment_revenue": "segment revenue", "segment_operating_income": "segment operating income",
           "segment_operating_margin": "segment operating margin", "product_revenue": "product revenue",
           "gross_margin_chg": "gross margin change (pp)", "operating_margin_chg": "operating margin change (pp)",
           "fcf_margin_chg": "free cash flow margin change (pp)",
           "capex_to_revenue_chg": "capex as % of revenue change (pp)",
           "capex_to_cfo_chg": "capex as % of operating cash flow change (pp)"},
}
SUFFIX = {"zh": {"yoy": "同比", "qoq": "环比"}, "en": {"yoy": "YoY", "qoq": "QoQ"}}


def label(metric: str, language: str = "zh", member: str | None = None) -> str:
    base, _, tag = metric.rpartition("_") if metric.endswith(("_yoy", "_qoq")) else (metric, "", "")
    name = LABELS[language].get(base, base)
    if member:
        name = f"{name} · {member}"
    return f"{name}{SUFFIX[language][tag]}" if language == "zh" and tag else (f"{name} {SUFFIX[language][tag]}" if tag else name)


class EvidenceItem(BaseModel):
    fact_id: str
    metric: str
    member: str | None = None  # segment or product, e.g. "Google Cloud"
    period_end: str
    fiscal_label: str | None
    value: float
    unit: str  # "USD", "ratio" or "pp" (percentage-point change of a ratio)
    display: str
    derived: bool
    note: str | None = None
    source: str | None = None


ITEM_NAMES = {"item1": "10-K Item 1 Business", "item1a": "10-K Item 1A Risk Factors",
              "item7": "10-K Item 7 MD&A", "item2_10q": "10-Q Item 2 MD&A"}


class EvidencePack(BaseModel):
    ticker: str
    company: str
    cik: str
    items: list[EvidenceItem]
    passages: list[Passage] = []

    def passage(self, source_id: str) -> Passage | None:
        return next((p for p in self.passages if p.source_id == source_id), None)

    def to_prompt_passages(self) -> str:
        if not self.passages:
            return ""
        lines = ["PASSAGES (verbatim text from SEC filings; quote exactly):"]
        for p in self.passages:
            lines.append(f"[{p.source_id}] ({ITEM_NAMES.get(p.item, p.item)}) {p.text}")
        return "\n".join(lines)

    def ids(self) -> set[str]:
        return {i.fact_id for i in self.items}

    def recent(self, periods: int) -> "EvidencePack":
        """Only the latest `periods` period ends (growth rates were computed on the full history)."""
        ends = sorted({i.period_end for i in self.items})[-periods:]
        return self.model_copy(update={"items": [i for i in self.items if i.period_end in ends]})

    def short_id(self, fact_id: str) -> str:
        """The id shown to the model: without the repeated CIK prefix (saves about a fifth of the prompt)."""
        prefix = f"{self.cik}:"
        return fact_id[len(prefix):] if fact_id.startswith(prefix) else fact_id

    def to_prompt_table(self, language: str = "zh") -> str:
        lines = ["fact_id | period | label | value | derived"]
        for i in self.items:
            lines.append(f"{self.short_id(i.fact_id)} | {i.fiscal_label or i.period_end} | "
                         f"{label(i.metric, language, i.member)} | {i.display} | {'y' if i.derived else 'n'}")
        return "\n".join(lines)


def fmt_usd(v: float) -> str:
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.1f}M"
    return f"{sign}${a:,.0f}"


def fmt_ratio(v: float) -> str:
    return f"{v * 100:.1f}%"


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")


def add_segments(items: list[EvidenceItem], fin: CompanyFinancials, series: dict) -> None:
    """Segment/product quarters aligned with the company-total quarters, plus YoY and segment margin."""
    from investment_core.segments import member_label
    ends = {q.end: q for q in fin.quarters}
    for (metric, member), s in sorted(series.items()):
        name = member_label(member)
        for end, qv in s.items():
            if end not in ends:
                continue
            src = qv.sources[0].accn if qv.sources else None
            items.append(EvidenceItem(
                fact_id=f"{fin.cik}:{metric}:{_slug(name)}:{end}", metric=metric, member=name, period_end=str(end),
                fiscal_label=ends[end].fiscal_label, value=qv.value, unit="USD", display=fmt_usd(qv.value),
                derived=qv.derived, note=qv.derivation, source=source_url(fin.cik, src) if src else None))
            prior = next((v for e, v in s.items() if abs((end - timedelta(days=365) - e).days) <= 20), None)
            if prior and prior.value > 0 and qv.value > 0:  # skip sign changes (e.g. loss-making segments)
                ch = qv.value / prior.value - 1
                items.append(EvidenceItem(
                    fact_id=f"{fin.cik}:{metric}_yoy:{_slug(name)}:{end}", metric=f"{metric}_yoy", member=name,
                    period_end=str(end), fiscal_label=ends[end].fiscal_label, value=ch, unit="ratio",
                    display=fmt_ratio(ch), derived=True, note=f"{name} {end} vs {prior.end}"))
        if metric == "segment_revenue":
            opi = series.get(("segment_operating_income", member), {})
            for end, rev in s.items():
                if end in ends and end in opi and rev.value > 0:
                    m = opi[end].value / rev.value
                    items.append(EvidenceItem(
                        fact_id=f"{fin.cik}:segment_operating_margin:{_slug(name)}:{end}", metric="segment_operating_margin",
                        member=name, period_end=str(end), fiscal_label=ends[end].fiscal_label, value=m, unit="ratio",
                        display=fmt_ratio(m), derived=True, note="segment operating income / segment revenue"))


PP_METRICS = ("gross_margin", "operating_margin", "fcf_margin", "capex_to_revenue", "capex_to_cfo")


def build_evidence(ticker: str, fin: CompanyFinancials, segments: dict | None = None,
                   passages: list[Passage] | None = None) -> EvidencePack:
    items: list[EvidenceItem] = []
    rows = fin.quarters
    for q in rows:
        for m in q.metrics.values():
            src = m.sources[0].accn if m.sources else None
            items.append(EvidenceItem(
                fact_id=m.evidence_id, metric=m.metric, period_end=str(q.end), fiscal_label=q.fiscal_label,
                value=m.value, unit=m.unit, display=fmt_ratio(m.value) if m.unit == "ratio" else fmt_usd(m.value),
                derived=m.derived, note=m.derivation, source=source_url(fin.cik, src) if src else None))

    # Ratios computed in code so the model never divides numbers itself.
    for q in rows:
        for name, num, den in (("capex_to_revenue", "capex", "revenue"), ("capex_to_cfo", "capex", "cfo"),
                               ("fcf_margin", "fcf", "revenue"),
                               ("net_to_operating_income", "net_income", "operating_income")):
            a, b = q.metrics.get(num), q.metrics.get(den)
            if a and b and b.value > 0:
                v = a.value / b.value
                items.append(EvidenceItem(
                    fact_id=f"{fin.cik}:{name}:{q.end}", metric=name, period_end=str(q.end), fiscal_label=q.fiscal_label,
                    value=v, unit="ratio", display=fmt_ratio(v), derived=True, note=f"{num} / {den}"))

    by_end = {q.end: q for q in rows}
    for q in rows:
        prior_year = next((p for e, p in by_end.items() if abs((q.end - timedelta(days=365) - e).days) <= 20), None)
        prior_q = next((p for e, p in by_end.items() if 75 <= (q.end - e).days <= 105), None)
        for metric in GROWTH_METRICS:
            cur = q.metrics.get(metric)
            for tag, base_row in (("yoy", prior_year), ("qoq", prior_q)):
                base = base_row.metrics.get(metric) if base_row else None
                if not cur or not base or base.value <= 0 or cur.value <= 0:  # sign change: % is meaningless
                    continue
                change = cur.value / base.value - 1
                items.append(EvidenceItem(
                    fact_id=f"{fin.cik}:{metric}_{tag}:{q.end}", metric=f"{metric}_{tag}", period_end=str(q.end),
                    fiscal_label=q.fiscal_label, value=change, unit="ratio", display=fmt_ratio(change), derived=True,
                    note=f"{metric} {q.end} vs {base_row.end}"))
    # Percentage-point changes of margins and ratios, so "margin fell 14.1 points" can be cited, not computed.
    ratio_at = {(i.metric, i.period_end): i for i in items if i.unit == "ratio" and not i.member}
    for q in rows:
        prior_year = next((p for e, p in by_end.items() if abs((q.end - timedelta(days=365) - e).days) <= 20), None)
        prior_q = next((p for e, p in by_end.items() if 75 <= (q.end - e).days <= 105), None)
        for metric in PP_METRICS:
            cur = ratio_at.get((metric, str(q.end)))
            for tag, base_row in (("yoy", prior_year), ("qoq", prior_q)):
                base = ratio_at.get((metric, str(base_row.end))) if base_row else None
                if not cur or not base:
                    continue
                pts = round((cur.value - base.value) * 100, 1)
                items.append(EvidenceItem(
                    fact_id=f"{fin.cik}:{metric}_chg_{tag}:{q.end}", metric=f"{metric}_chg_{tag}", period_end=str(q.end),
                    fiscal_label=q.fiscal_label, value=pts, unit="pp", display=f"{pts:+.1f} pp", derived=True,
                    note=f"{metric} {q.end} minus {base_row.end}, in percentage points"))
    if segments:
        add_segments(items, fin, segments)
    return EvidencePack(ticker=ticker.upper(), company=fin.name, cik=fin.cik, items=items, passages=passages or [])
