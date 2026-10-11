"""Which well-known institutions held a given company in their latest 13F, and how that changed. Pure, no IO.

A 13F shows long positions in US-listed shares as of a quarter end, filed up to 45 days later. It says what a manager
held, not why, and not what it did since. Context only: it does not feed the gate, the rules or the AI.
"""

from __future__ import annotations

import re

from .thirteenf import Portfolio13F, by_issuer, equity_only, issuer_key

_SLASH = re.compile(r"\s*/[A-Z]{1,4}/?\s*$")
_TAIL = {"DEL", "NEW", "THE"}


def company_key(name: str) -> str:
    """'Berkshire Hathaway Inc.' (SEC list) and 'BERKSHIRE HATHAWAY INC DEL' (13F) become the same words."""
    words = issuer_key(_SLASH.sub("", name.strip())).split()
    while len(words) > 1 and words[-1] in _TAIL:
        words.pop()
    return " ".join(words)


def _find(p: Portfolio13F, key: str):
    """The company's merged holding in a portfolio (already by_issuer), or None."""
    return next((h for h in p.holdings if company_key(h.issuer) == key), None)


def holder_row(portfolios: list[Portfolio13F], key: str) -> dict | None:
    """`portfolios` newest first (one or two quarters). None when the institution held the company in neither."""
    cur, _ = equity_only(portfolios[0])
    cur = by_issuer(cur)
    now = _find(cur, key)
    before = None
    if len(portfolios) > 1:
        prev, _ = equity_only(portfolios[1])
        before = _find(by_issuer(prev), key)
    if now is None and before is None:
        return None
    row = {"period": portfolios[0].period.isoformat(), "filed": portfolios[0].filed.isoformat(),
           "previous_period": portfolios[1].period.isoformat() if len(portfolios) > 1 else None,
           "held": now is not None, "weight": None, "rank": None, "positions": len(cur.holdings),
           "value_usd": None, "shares": None, "prev_shares": before.shares if before else None,
           "change": None, "change_pct": None}
    if now is not None:
        order = sorted(cur.holdings, key=lambda h: h.value_usd, reverse=True)
        row.update(weight=round(cur.weight(now), 4), rank=order.index(now) + 1, value_usd=round(now.value_usd),
                   shares=now.shares)
    if len(portfolios) > 1:
        sa, sb = (now.shares if now else 0.0), (before.shares if before else 0.0)
        row["change"] = ("new" if before is None else "exited" if now is None else
                         "increased" if sa > sb else "decreased" if sa < sb else "unchanged")
        if before is not None and now is not None and sb:
            row["change_pct"] = round(sa / sb - 1, 4)
    return row
