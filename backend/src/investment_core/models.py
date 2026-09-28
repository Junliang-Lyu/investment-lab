"""Domain models. See docs/DESIGN.md §6.1."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EPS = 1e-9


class Sleeve(str, Enum):
    CORE = "core"
    SATELLITE = "satellite"
    UNCLASSIFIED = "unclassified"


class AssetType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    OTHER = "other"


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    BREAK = "break"

    @property
    def rank(self) -> int:
        return {"info": 0, "warn": 1, "break": 2}[self.value]


class Asset(BaseModel):
    symbol: str
    name: str = ""
    asset_type: AssetType = AssetType.STOCK
    sleeve: Sleeve = Sleeve.UNCLASSIFIED
    # User-defined exposure themes (e.g. "us_megacap_tech"). Concentration is
    # measured on these, not on GICS sectors. See DESIGN §6.1.
    exposure_tags: list[str] = Field(default_factory=list)
    cik: str | None = None


class Position(BaseModel):
    symbol: str
    quantity: float
    market_value: float
    cost_basis: float | None = None
    unrealized_pnl: float | None = None
    currency: str = "USD"


class Snapshot(BaseModel):
    as_of: date
    net_liquidation: float
    cash: float
    positions: list[Position] = Field(default_factory=list)
    source: str = "manual"

    @field_validator("net_liquidation")
    @classmethod
    def _positive_nav(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("net_liquidation must be positive")
        return v

    @property
    def invested(self) -> float:
        return sum(p.market_value for p in self.positions)

    def position(self, symbol: str) -> Position | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def weight_nav(self, market_value: float) -> float:
        return market_value / self.net_liquidation

    def weight_invested(self, market_value: float) -> float:
        inv = self.invested
        return market_value / inv if inv > EPS else 0.0


class Rule(BaseModel):
    code: str
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = Severity.WARN
    enabled: bool = True


class RuleSet(BaseModel):
    version: str
    status: Literal["draft", "active", "retired"] = "draft"
    notes: str = ""
    rules: list[Rule]

    def get(self, code: str) -> Rule | None:
        for r in self.rules:
            if r.code == code and r.enabled:
                return r
        return None


class Violation(BaseModel):
    rule_code: str
    severity: Severity
    message: str
    symbol: str | None = None
    observed: float | None = None
    limit: float | None = None


class MemoStatus(str, Enum):
    IDEA = "idea"
    RESEARCHING = "researching"
    SKEPTIC_DONE = "skeptic_done"
    USER_RESPONDED = "user_responded"
    REVIEWED = "reviewed"
    FINAL = "final"
    ARCHIVED = "archived"

    @property
    def rank(self) -> int:
        order = ["idea", "researching", "skeptic_done", "user_responded", "reviewed", "final"]
        return order.index(self.value) if self.value in order else -1


class MemoDecision(str, Enum):
    WATCHLIST = "watchlist"
    PAPER = "paper"
    ELIGIBLE_FOR_GATE = "eligible_for_gate"


class MemoSummary(BaseModel):
    """What the rule engine needs to know about a memo."""

    symbol: str
    status: MemoStatus
    decision: MemoDecision | None = None
    invalidation_count: int = 0
    review_date: date | None = None
    has_unresolved_review_issues: bool = False


class WatchEntry(BaseModel):
    symbol: str
    mode: Literal["paper", "watch"] = "watch"
    started_at: date
    review_count: int = 0


class GateRecord(BaseModel):
    symbol: str
    checked_at: date


class Context(BaseModel):
    assets: dict[str, Asset] = Field(default_factory=dict)
    memos: dict[str, MemoSummary] = Field(default_factory=dict)
    watchlist: dict[str, WatchEntry] = Field(default_factory=dict)
    last_review_date: date | None = None
    previous_snapshot: Snapshot | None = None
    gate_records: list[GateRecord] = Field(default_factory=list)
    today: date | None = None

    def asset(self, symbol: str) -> Asset:
        return self.assets.get(symbol) or Asset(symbol=symbol)

    def effective_today(self, snapshot: Snapshot) -> date:
        return self.today or snapshot.as_of


class TradeProposal(BaseModel):
    symbol: str
    side: Literal["buy", "sell"] = "buy"
    amount_usd: float | None = None
    quantity: float | None = None
    price: float | None = None
    note: str = ""
    # Self-attested checklist answers keyed by GateItem.key; missing = unknown.
    attestations: dict[str, bool] = Field(default_factory=dict)

    def resolved_amount(self) -> float:
        if self.amount_usd is not None:
            if self.amount_usd <= 0:
                raise ValueError("amount_usd must be positive")
            return self.amount_usd
        if self.quantity is not None and self.price is not None:
            amount = self.quantity * self.price
            if amount <= 0:
                raise ValueError("quantity * price must be positive")
            return amount
        raise ValueError("provide amount_usd, or quantity and price")
