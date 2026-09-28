"""Shared test helpers.

All fixtures use fictional tickers (AAAA, BBBB, ...) and a $10,000 net value.
Real holdings never appear in this repository (DESIGN §3).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from investment_core.importers import load_rule_set
from investment_core.models import Asset, AssetType, Context, MemoSummary, Position, Sleeve, Snapshot

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
AS_OF = date(2026, 9, 25)


def snap(cash: float, *positions: tuple, nav: float = 10_000, as_of: date = AS_OF) -> Snapshot:
    """positions: (symbol, market_value[, quantity[, unrealized_pnl]])"""
    ps = []
    for p in positions:
        sym, mv = p[0], p[1]
        qty = p[2] if len(p) > 2 else mv / 100
        pnl = p[3] if len(p) > 3 else None
        ps.append(Position(symbol=sym, quantity=qty, market_value=mv, unrealized_pnl=pnl))
    return Snapshot(as_of=as_of, net_liquidation=nav, cash=cash, positions=ps)


def sat(symbol: str, *tags: str) -> Asset:
    return Asset(symbol=symbol, asset_type=AssetType.STOCK, sleeve=Sleeve.SATELLITE, exposure_tags=list(tags))


def core(symbol: str, *tags: str) -> Asset:
    return Asset(symbol=symbol, asset_type=AssetType.ETF, sleeve=Sleeve.CORE, exposure_tags=list(tags))


def ctx(*assets: Asset, memos: list[MemoSummary] | None = None, **kw) -> Context:
    kw.setdefault("last_review_date", date(2026, 9, 20))
    return Context(assets={a.symbol: a for a in assets},
                   memos={m.symbol: m for m in (memos or [])}, **kw)


def codes(violations) -> list[str]:
    return [v.rule_code for v in violations]


@pytest.fixture
def draft_rules():
    return load_rule_set(FIXTURES / "rules" / "v0_draft.yaml")


@pytest.fixture
def demo_rules():
    return load_rule_set(FIXTURES / "rules" / "demo.yaml")
