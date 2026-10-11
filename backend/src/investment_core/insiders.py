"""Form 4 (insider transactions) parsing and summary. Pure functions: no network, no model.

A Form 4 is filed by a company's officers, directors and holders of more than 10% of a class of its stock within
two business days of a trade in the company's own shares. Only open-market purchases (code P) and sales (code S) are
treated as trades here. Grants (A), option exercises (M), tax withholding (F), gifts (G) and the rest are mostly
mechanical and say little about what an insider thinks, so they are counted but not listed.

Context only: nothing here feeds the gate, the rules or the AI. An insider buying or selling is not a signal.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta

TRADE_CODES = {"P": "A", "S": "D"}  # open-market code -> the acquired/disposed flag it must carry


@dataclass
class Owner:
    name: str
    director: bool = False
    officer: bool = False
    title: str = ""
    ten_percent: bool = False

    def roles(self) -> list[str]:
        out = []
        if self.officer:
            out.append(self.title.strip() or "Officer")
        if self.director:
            out.append("Director")
        if self.ten_percent:
            out.append("10% owner")
        return out


@dataclass
class Txn:
    date: date
    code: str
    shares: float
    price: float | None
    acquired: bool  # True = acquired (A), False = disposed (D)
    after: float | None
    security: str = ""
    direct: bool = True


@dataclass
class Form4:
    owners: list[Owner]
    plan_10b5_1: bool
    txns: list[Txn]
    issuer_cik: int | None = None  # whose shares were traded; a company's filing list also holds forms it filed as owner
    accession: str = ""
    filed: date | None = None
    url: str = ""
    extra: dict = field(default_factory=dict)


def _strip(root: ET.Element) -> ET.Element:
    for el in root.iter():
        el.tag = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else el.tag
    return root


def _text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    node = el.find(path)
    return (node.text or "").strip() if node is not None else ""


def _num(s: str) -> float | None:
    try:
        v = float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _flag(s: str) -> bool:
    return s.strip().lower() in ("1", "true")


def parse_form4(xml: bytes) -> Form4 | None:
    """Parse one Form 4 document. None when it is not a readable ownership document."""
    try:
        root = _strip(ET.fromstring(xml))
    except ET.ParseError:
        return None
    if root.tag != "ownershipDocument":
        return None
    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        owners.append(Owner(name=_text(ro, "reportingOwnerId/rptOwnerName"),
                            director=_flag(_text(rel, "isDirector")), officer=_flag(_text(rel, "isOfficer")),
                            title=_text(rel, "officerTitle"), ten_percent=_flag(_text(rel, "isTenPercentOwner"))))
    plan = _flag(_text(root, "aff10b5One"))
    if not plan:  # older forms, and some filers, say it only in a footnote
        plan = any("10b5-1" in (f.text or "") for f in root.iter("footnote"))
    txns = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        d = _text(t, "transactionDate/value")
        shares = _num(_text(t, "transactionAmounts/transactionShares/value"))
        ad = _text(t, "transactionAmounts/transactionAcquiredDisposedCode/value").upper()
        try:
            day = date.fromisoformat(d[:10])
        except ValueError:
            continue
        if shares is None or shares <= 0 or ad not in ("A", "D"):
            continue
        txns.append(Txn(date=day, code=_text(t, "transactionCoding/transactionCode").upper(), shares=shares,
                        price=_num(_text(t, "transactionAmounts/transactionPricePerShare/value")),
                        acquired=ad == "A",
                        after=_num(_text(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                        security=_text(t, "securityTitle/value"),
                        direct=_text(t, "ownershipNature/directOrIndirectOwnership/value").upper() != "I"))
    try:
        issuer = int(_text(root, "issuer/issuerCik"))
    except ValueError:
        issuer = None
    return Form4(owners=owners, plan_10b5_1=plan, txns=txns, issuer_cik=issuer)


_ENTITY = {"LP", "LLC", "INC", "CORP", "LTD", "TRUST", "FUND", "CAPITAL", "PARTNERS", "HOLDINGS", "GROUP", "COMPANY",
           "FOUNDATION", "ASSOCIATES", "&", "CO", "PLC", "ETAL"}
_SUFFIX = {"JR", "SR", "II", "III", "IV"}


def display_name(raw: str) -> str:
    """SEC writes people as 'LAST FIRST MIDDLE' (often in capitals). Shown as 'First Middle Last'; entities (and
    anything with a comma) are kept as written. A guess: the raw form stays in the filing the row links to."""
    name = " ".join(raw.split())
    words = [w.strip(".,").upper().replace(".", "") for w in name.replace(",", " ").split()]
    if not name or "," in name or any(w in _ENTITY for w in words) or any(c.isdigit() for c in name):
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    tail = []
    while len(parts) > 2 and parts[-1].strip(".").upper() in _SUFFIX:
        tail.insert(0, parts.pop())
    out = " ".join(parts[1:] + [parts[0]] + tail)
    return out.title() if out.isupper() or out.islower() else out


def summarize(forms: list[Form4], today: date, months: int = 12) -> dict:
    """Open-market trades inside the window, newest first, with totals. `forms` carry accession/filed/url."""
    start = today - timedelta(days=round(months * 30.4))
    rows, seen, other = [], set(), 0
    for f in forms:
        names = [o.name for o in f.owners if o.name]
        owner = f.owners[0] if f.owners else Owner(name="")
        for t in f.txns:
            if t.date < start or t.date > today:
                continue
            if TRADE_CODES.get(t.code) != ("A" if t.acquired else "D"):
                other += 1
                continue
            key = (tuple(sorted(names)), t.date, t.code, t.shares, t.price)
            if key in seen:  # the same trade reported on more than one form
                continue
            seen.add(key)
            rows.append({
                "date": t.date.isoformat(), "side": "buy" if t.code == "P" else "sell",
                "owner": display_name(names[0]) if names else "?", "co_owners": max(0, len(names) - 1),
                "security": t.security, "roles": owner.roles(),
                "shares": t.shares, "price": t.price,
                "value": round(t.shares * t.price, 2) if t.price else None,
                "after": t.after, "direct": t.direct, "plan_10b5_1": f.plan_10b5_1,
                "filed": f.filed.isoformat() if f.filed else None, "url": f.url,
            })
    rows.sort(key=lambda r: (r["date"], r["owner"]), reverse=True)

    def tot(side: str) -> dict:
        rs = [r for r in rows if r["side"] == side]
        return {"trades": len(rs), "people": len({r["owner"] for r in rs}),
                "shares": sum(r["shares"] for r in rs), "value": round(sum(r["value"] or 0 for r in rs), 2),
                "planned": sum(1 for r in rs if r["plan_10b5_1"])}

    by_month: dict[str, dict] = {}
    y, m = today.year, today.month
    for _ in range(months + 1):
        by_month[f"{y:04d}-{m:02d}"] = {"month": f"{y:04d}-{m:02d}", "buy": 0.0, "sell": 0.0}
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    for r in rows:
        if r["value"] and r["date"][:7] in by_month:
            by_month[r["date"][:7]][r["side"]] += r["value"]
    series = sorted(by_month.values(), key=lambda x: x["month"])
    for x in series:
        x["buy"], x["sell"] = round(x["buy"], 2), round(x["sell"], 2)

    return {"window_start": start.isoformat(), "months": series, "window_end": today.isoformat(), "buys": tot("buy"),
            "sells": tot("sell"), "other_transactions": other, "trades": rows}
