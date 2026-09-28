"""Segment and product revenue from a filing's XBRL instance. Pure, no IO. See DESIGN §9.1.

companyfacts has company totals only; segment numbers are dimensional facts that
live in each filing's XBRL instance (for inline filings EDGAR publishes it as
*_htm.xml). We read:
- segment revenue / operating income on us-gaap:StatementBusinessSegmentsAxis
  (operating income may also carry srt:ConsolidationItemsAxis = OperatingSegmentsMember)
- product/service revenue on srt:ProductOrServiceAxis (optionally within a segment)
Quarterly values reuse financials.quarterly_series, so Q4 is derived as FY - 9M
exactly like the company totals.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

from pydantic import BaseModel

from .financials import Fact, QuarterValue, quarterly_series

SEG_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
PROD_AXIS = "srt:ProductOrServiceAxis"
CONS_AXIS = "srt:ConsolidationItemsAxis"
REVENUE = {"RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"}
OPINC = {"OperatingIncomeLoss"}


class DimFact(BaseModel):
    metric: str       # segment_revenue | segment_operating_income | product_revenue
    member: str       # e.g. goog:GoogleCloudMember
    fact: Fact


def member_label(member: str) -> str:
    name = member.split(":", 1)[-1]
    name = re.sub(r"Member$", "", name)
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).strip()
    for a, b in (("You Tube", "YouTube"), (" Revenue", ""), ("Search Other", "Search & other")):
        label = label.replace(a, b)
    return label


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_instance(xml: bytes, accn: str, form: str, filed: date) -> list[DimFact]:
    root = ET.fromstring(xml)
    ctx: dict[str, tuple[dict, dict]] = {}
    for c in root:
        if _local(c.tag) != "context":
            continue
        per, dims = {}, {}
        for el in c.iter():
            n = _local(el.tag)
            if n in ("startDate", "endDate"):
                per[n] = el.text.strip()
            elif n == "explicitMember":
                dims[el.get("dimension")] = el.text.strip()
        if "startDate" in per:
            ctx[c.get("id")] = (per, dims)

    out = []
    for el in root:
        concept, cref = _local(el.tag), el.get("contextRef")
        if cref not in ctx or el.text is None or concept not in REVENUE | OPINC:
            continue
        per, dims = ctx[cref]
        keys = set(dims)
        if concept in REVENUE and keys == {SEG_AXIS}:
            metric, member = "segment_revenue", dims[SEG_AXIS]
        elif concept in OPINC and (keys == {SEG_AXIS} or (keys == {SEG_AXIS, CONS_AXIS}
                                                          and dims[CONS_AXIS].endswith("OperatingSegmentsMember"))):
            metric, member = "segment_operating_income", dims[SEG_AXIS]
        elif concept in REVENUE and keys in ({PROD_AXIS}, {PROD_AXIS, SEG_AXIS}):
            metric, member = "product_revenue", dims[PROD_AXIS]
        else:
            continue
        try:
            val = float(el.text)
        except ValueError:
            continue
        f = Fact(concept=f"{concept}[{member_label(member)}]", start=per["startDate"], end=per["endDate"], val=val,
                 accn=accn, form=form, filed=filed)
        if f.months is not None:
            out.append(DimFact(metric=metric, member=member, fact=f))
    return out


def segment_series(facts: list[DimFact]) -> dict[tuple[str, str], dict[date, QuarterValue]]:
    """(metric, member) -> quarter end -> value. Same period in several filings: latest filed wins."""
    grouped: dict[tuple[str, str], dict[tuple[date, date], Fact]] = defaultdict(dict)
    for d in facts:
        key, per = (d.metric, d.member), (d.fact.start, d.fact.end)
        cur = grouped[key].get(per)
        if cur is None or (d.fact.filed, d.fact.accn) > (cur.filed, cur.accn):
            grouped[key][per] = d.fact
    return {k: quarterly_series(sorted(v.values(), key=lambda f: (f.start, f.end))) for k, v in grouped.items()}
