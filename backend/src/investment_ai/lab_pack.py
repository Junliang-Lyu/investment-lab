"""Evidence for the public Lab skeptic and its eval (one builder, so the eval tests what the site runs).

Per company (cached by the caller): financials and segment numbers for the latest LAB_PERIODS quarters, plus
all narrative paragraphs from the latest 10-K (business, risk factors, MD&A) and 10-Q (MD&A).
Per thesis: the most relevant paragraphs are retrieved (BM25) and attached, so the model can quote the
company's own words; every quote is checked verbatim by the validator.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from investment_core.filing_text import Passage, retrieve_diverse
from investment_core.financials import build_financials

from .evidence import EvidencePack, build_evidence
from .research import LAB_PERIODS

log = logging.getLogger("investment_ai.lab_pack")

LAB_PASSAGES = 12  # about 2-3k prompt tokens
BASE_TERMS = ["capital expenditures", "competition", "artificial intelligence", "regulatory", "demand",
              "depreciation", "operating loss"]
# Chinese theses cannot be matched against English filings directly; common investment words are mapped.
ZH_TERMS = {
    "云": "cloud", "广告": "advertising", "搜索": "search", "储能": "energy storage", "自动驾驶": "autonomous",
    "机器人": "robotaxi", "汽车": "automotive", "服务": "services", "订阅": "subscription", "硬件": "hardware",
    "芯片": "chips", "数据中心": "data center", "会员": "membership", "监管": "regulatory", "竞争": "competition",
    "支付": "payments", "内存": "memory", "存储": "storage", "关税": "tariffs", "中国": "China", "利润率": "margin",
    "资本开支": "capital expenditures", "现金流": "cash flow", "债务": "debt", "回购": "repurchase",
    "人工智能": "artificial intelligence", "AI": "AI", "游戏": "gaming", "视频": "video", "零售": "retail",
    "电商": "online stores", "物流": "fulfillment", "折旧": "depreciation", "需求": "demand", "定价": "pricing",
}
STOP = set("""a an and are as at be been but by can could does for from has have if in into is it its may more most
much next not of on or over should so than that the their them then there these this those to under was were
which while will with would company year years quarter growth keep""".split())


class LabCompany(BaseModel):
    ticker: str
    pack: EvidencePack           # numbers only, latest LAB_PERIODS quarters
    passages: list[Passage] = []  # every narrative paragraph from the latest 10-K and 10-Q
    members: list[str] = []       # segment names, used as retrieval terms


def load_lab_company(client, ticker: str) -> LabCompany:
    from investment_core.segments import member_label, segment_series
    from investment_data.filing_text import load_passages
    from investment_data.segments import load_segment_facts

    cik = client.cik_for(ticker)
    fin = build_financials(client.company_facts(cik), quarters=8)
    series = None
    try:
        series = segment_series(load_segment_facts(client, cik))
    except Exception as e:  # segments are optional
        log.info("%s: no segment data: %s", ticker, e)
    passages: list[Passage] = []
    try:
        passages = load_passages(client, cik)
    except Exception as e:  # filing text is optional; the answer then uses numbers only
        log.info("%s: no filing text: %s", ticker, e)
    members = sorted({member_label(m) for (_, m) in (series or {})})
    pack = build_evidence(ticker, fin, series).recent(LAB_PERIODS)
    return LabCompany(ticker=ticker.upper(), pack=pack, passages=passages, members=members)


def thesis_terms(thesis: str, members: list[str]) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9&\-]+", thesis) if w.lower() not in STOP and len(w) > 2]
    zh = [en for key, en in ZH_TERMS.items() if key in thesis]
    seen, out = set(), []
    for term in [*words, *zh, *members, *BASE_TERMS]:
        if term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    return out


def lab_pack(company: LabCompany, thesis: str) -> EvidencePack:
    """Numbers plus the paragraphs most relevant to this thesis (deterministic for a given thesis)."""
    chosen = retrieve_diverse(company.passages, thesis_terms(thesis, company.members), k=LAB_PASSAGES) \
        if company.passages else []
    return company.pack.model_copy(update={"passages": chosen})
