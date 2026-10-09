"""13F holdings: parse information tables, aggregate, compare quarters. Pure, no IO.

13F-HR is filed quarterly by institutional managers with >$100M in US-listed
equities, up to 45 days after quarter end. It shows long positions in 13(f)
securities only: no shorts, cash, bonds or non-US holdings. It tells you how a
manager allocates, not when they traded.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

VALUE_IN_DOLLARS_FROM = date(2023, 1, 3)  # SEC switched <value> from $ thousands to dollars


class Holding13F(BaseModel):
    cusip: str
    issuer: str
    title_class: str
    value_usd: float
    shares: float
    share_type: str = "SH"
    put_call: str | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.cusip, self.put_call)


class Portfolio13F(BaseModel):
    filer: str
    cik: str
    period: date
    filed: date
    accession: str
    holdings: list[Holding13F] = Field(default_factory=list)

    @property
    def total_value(self) -> float:
        return sum(h.value_usd for h in self.holdings)

    def weight(self, h: Holding13F) -> float:
        total = self.total_value
        return h.value_usd / total if total else 0.0

    def top(self, n: int = 15) -> list[Holding13F]:
        return sorted(self.holdings, key=lambda h: h.value_usd, reverse=True)[:n]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el: ET.Element | None, name: str, default: str = "") -> str:
    c = _child(el, name) if el is not None else None
    return (c.text or "").strip() if c is not None and c.text else default


def parse_info_table(xml: bytes | str, filed: date) -> list[Holding13F]:
    """One Holding13F per <infoTable> row (not yet aggregated)."""
    root = ET.fromstring(xml)
    scale = 1.0 if filed >= VALUE_IN_DOLLARS_FROM else 1000.0
    rows = []
    for it in root.iter():
        if _local(it.tag) != "infoTable":
            continue
        amt = _child(it, "shrsOrPrnAmt")
        rows.append(Holding13F(
            cusip=_text(it, "cusip").upper(),
            issuer=_text(it, "nameOfIssuer"),
            title_class=_text(it, "titleOfClass"),
            value_usd=float(_text(it, "value", "0").replace(",", "")) * scale,
            shares=float(_text(amt, "sshPrnamt", "0").replace(",", "")),
            share_type=_text(amt, "sshPrnamtType", "SH"),
            put_call=_text(it, "putCall") or None,
        ))
    return rows


def aggregate(rows: list[Holding13F]) -> list[Holding13F]:
    """Managers often report one security on several lines (different sub-managers). Merge by CUSIP and put/call."""
    merged: dict[tuple[str, str | None], Holding13F] = {}
    for r in rows:
        if r.key in merged:
            m = merged[r.key]
            merged[r.key] = m.model_copy(update={"value_usd": m.value_usd + r.value_usd, "shares": m.shares + r.shares})
        else:
            merged[r.key] = r
    return sorted(merged.values(), key=lambda h: h.value_usd, reverse=True)


ChangeKind = Literal["new", "exited", "increased", "decreased", "unchanged"]


class Change(BaseModel):
    cusip: str
    issuer: str
    put_call: str | None
    kind: ChangeKind
    shares_before: float
    shares_after: float
    value_after: float
    weight_after: float
    weight_before: float

    @property
    def shares_change_pct(self) -> float | None:
        return None if self.shares_before == 0 else self.shares_after / self.shares_before - 1


def diff(prev: Portfolio13F, cur: Portfolio13F) -> list[Change]:
    """Compare share counts (not values, which also move with prices)."""
    before = {h.key: h for h in prev.holdings}
    after = {h.key: h for h in cur.holdings}
    out = []
    for key in before.keys() | after.keys():
        b, a = before.get(key), after.get(key)
        sb, sa = (b.shares if b else 0.0), (a.shares if a else 0.0)
        if b is None:
            kind: ChangeKind = "new"
        elif a is None:
            kind = "exited"
        elif sa > sb:
            kind = "increased"
        elif sa < sb:
            kind = "decreased"
        else:
            kind = "unchanged"
        ref = a or b
        out.append(Change(cusip=key[0], issuer=ref.issuer, put_call=key[1], kind=kind, shares_before=sb, shares_after=sa,
                          value_after=a.value_usd if a else 0.0, weight_after=cur.weight(a) if a else 0.0,
                          weight_before=prev.weight(b) if b else 0.0))
    order = {"new": 0, "exited": 1, "increased": 2, "decreased": 3, "unchanged": 4}
    return sorted(out, key=lambda c: (order[c.kind], -max(c.weight_after, c.weight_before)))


def concentration(p: Portfolio13F) -> dict[str, float]:
    ws = sorted((p.weight(h) for h in p.holdings), reverse=True)
    return {"positions": float(len(ws)), "top1": sum(ws[:1]), "top5": sum(ws[:5]), "top10": sum(ws[:10])}


_CLASS_TAIL = re.compile(r"\s+(CLASS|CL)\s+[A-Z0-9]{1,2}\b.*$")


def issuer_key(name: str) -> str:
    """Same company across share classes: 'ALPHABET INC CL A' and 'ALPHABET INC CL C' are one issuer."""
    n = _CLASS_TAIL.sub("", re.sub(r"[.,]", "", name.upper()))
    return " ".join(n.split())


def equity_only(p: Portfolio13F) -> tuple[Portfolio13F, int]:
    """Without put/call lines: those are reported at the value of the underlying shares, which is not money invested.
    Returns the portfolio and how many option lines were dropped."""
    keep = [h for h in p.holdings if not h.put_call]
    return p.model_copy(update={"holdings": keep}), len(p.holdings) - len(keep)


def by_issuer(p: Portfolio13F) -> Portfolio13F:
    """Merge share classes of one company, so that 'top 5' counts companies, not tickers. Use on equity_only()."""
    merged: dict[str, Holding13F] = {}
    for h in sorted(p.holdings, key=lambda x: x.value_usd, reverse=True):
        k = issuer_key(h.issuer)
        if k in merged:
            m = merged[k]
            merged[k] = m.model_copy(update={"value_usd": m.value_usd + h.value_usd, "shares": m.shares + h.shares})
        else:
            merged[k] = h  # the largest line names the issuer
    return p.model_copy(update={"holdings": sorted(merged.values(), key=lambda x: x.value_usd, reverse=True)})
