"""Rule implementations. One function per rule code. See docs/DESIGN.md §6.2.

Each function receives (snapshot, rule, ctx) and returns a list of violations.
Rule parameters come from the RuleSet, never from constants here, so that
draft rules can change without code changes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Callable

from .models import EPS, Context, MemoStatus, Rule, Sleeve, Snapshot, Violation

RuleFn = Callable[[Snapshot, Rule, Context], list[Violation]]

# Rules that only apply to a proposed trade (handled in gate.py).
GATE_ONLY_CODES = {"FIRST_LIVE_MAX_USD", "PAPER_MIN_WEEKS", "PAPER_MIN_REVIEWS"}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _v(rule: Rule, message: str, *, symbol=None, observed=None, limit=None) -> Violation:
    return Violation(
        rule_code=rule.code,
        severity=rule.severity,
        message=message,
        symbol=symbol,
        observed=observed,
        limit=limit,
    )


def _non_core(snapshot: Snapshot, ctx: Context):
    return [p for p in snapshot.positions if ctx.asset(p.symbol).sleeve != Sleeve.CORE]


def core_target_weight(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    target = float(rule.params["target"])
    core = sum(p.market_value for p in s.positions if ctx.asset(p.symbol).sleeve == Sleeve.CORE)
    w = s.weight_nav(core)
    if w + EPS < target:
        return [_v(rule, f"Core is {_pct(w)} of net value, target {_pct(target)}", observed=w, limit=target)]
    return []


def satellite_max_weight(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    limit = float(rule.params["max"])
    sat = sum(p.market_value for p in s.positions if ctx.asset(p.symbol).sleeve == Sleeve.SATELLITE)
    w = s.weight_nav(sat)
    if w > limit + EPS:
        return [_v(rule, f"Satellite is {_pct(w)} of net value, limit {_pct(limit)}", observed=w, limit=limit)]
    return []


def single_max_weight_nav(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    limit = float(rule.params["max"])
    out = []
    for p in _non_core(s, ctx):
        w = s.weight_nav(p.market_value)
        if w > limit + EPS:
            out.append(_v(rule, f"{p.symbol} is {_pct(w)} of net value, limit {_pct(limit)}",
                          symbol=p.symbol, observed=w, limit=limit))
    return out


def memo_required_above(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    threshold = float(rule.params["threshold"])
    min_status = MemoStatus(rule.params.get("min_status", "reviewed"))
    out = []
    for p in _non_core(s, ctx):
        w = s.weight_nav(p.market_value)
        if w <= threshold + EPS:
            continue
        memo = ctx.memos.get(p.symbol)
        if memo is None:
            out.append(_v(rule, f"{p.symbol} is {_pct(w)} of net value with no memo (required above {_pct(threshold)})",
                          symbol=p.symbol, observed=w, limit=threshold))
        elif memo.status.rank < min_status.rank:
            out.append(_v(rule, f"{p.symbol} is {_pct(w)} of net value; memo is only at '{memo.status.value}', "
                                f"needs '{min_status.value}'", symbol=p.symbol, observed=w, limit=threshold))
    return out


def exposure_max_weight(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    """Theme concentration on user-defined exposure tags.

    Core positions are excluded by default (count_core=false): a broad index
    fund is the diversified base, not a thematic bet.
    """
    limit = float(rule.params["max"])
    count_core = bool(rule.params.get("count_core", False))
    by_tag: dict[str, float] = defaultdict(float)
    for p in (s.positions if count_core else _non_core(s, ctx)):
        for tag in ctx.asset(p.symbol).exposure_tags:
            by_tag[tag] += p.market_value
    out = []
    for tag, mv in sorted(by_tag.items()):
        w = s.weight_nav(mv)
        if w > limit + EPS:
            out.append(_v(rule, f"Exposure '{tag}' is {_pct(w)} of net value, limit {_pct(limit)}",
                          symbol=None, observed=w, limit=limit))
    return out


def single_max_weight_invested(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    limit = float(rule.params["max"])
    out = []
    for p in _non_core(s, ctx):
        w = s.weight_invested(p.market_value)
        if w > limit + EPS:
            out.append(_v(rule, f"{p.symbol} is {_pct(w)} of invested capital, limit {_pct(limit)}",
                          symbol=p.symbol, observed=w, limit=limit))
    return out


def top3_max_weight_invested(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    """Top-3 concentration over invested capital.

    By default Core positions are excluded from the top 3 (count_core=false),
    otherwise a plain index portfolio (e.g. 3 ETFs) would always trip this rule.
    """
    limit = float(rule.params["max"])
    count_core = bool(rule.params.get("count_core", False))
    pool = s.positions if count_core else _non_core(s, ctx)
    top = sorted(pool, key=lambda p: p.market_value, reverse=True)[:3]
    w = s.weight_invested(sum(p.market_value for p in top))
    if w > limit + EPS:
        names = ", ".join(p.symbol for p in top)
        return [_v(rule, f"Top holdings ({names}) are {_pct(w)} of invested capital, limit {_pct(limit)}",
                   observed=w, limit=limit)]
    return []


def loss_review_trigger(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    loss = float(rule.params["loss_usd"])
    out = []
    for p in s.positions:
        if p.unrealized_pnl is not None and p.unrealized_pnl <= -loss + EPS:
            out.append(_v(rule, f"{p.symbol} unrealized loss ${-p.unrealized_pnl:,.0f} reached review trigger ${loss:,.0f}",
                          symbol=p.symbol, observed=p.unrealized_pnl, limit=-loss))
    return out


def review_overdue(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    max_days = int(rule.params["max_days"])
    today = ctx.effective_today(s)
    if ctx.last_review_date is None:
        return [_v(rule, "No portfolio review on record")]
    days = (today - ctx.last_review_date).days
    if days > max_days:
        return [_v(rule, f"Last portfolio review was {days} days ago (every {max_days} days)",
                   observed=float(days), limit=float(max_days))]
    return []


def unclassified_position(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    return [
        _v(rule, f"{p.symbol} is not classified as core or satellite", symbol=p.symbol)
        for p in s.positions
        if ctx.asset(p.symbol).sleeve == Sleeve.UNCLASSIFIED
    ]


def missing_invalidation(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    need = int(rule.params.get("min_conditions", 3))
    out = []
    for p in s.positions:
        if ctx.asset(p.symbol).sleeve != Sleeve.SATELLITE:
            continue
        memo = ctx.memos.get(p.symbol)
        have = memo.invalidation_count if memo else 0
        if have < need:
            out.append(_v(rule, f"{p.symbol} has {have} invalidation condition(s), needs {need}",
                          symbol=p.symbol, observed=float(have), limit=float(need)))
    return out


def unchecked_trade(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    """Position increased since the previous snapshot without a gate check.

    The system cannot stop a trade placed in the broker app; it can only make
    a skipped check visible on the next import. See DESIGN §6.2.
    """
    prev = ctx.previous_snapshot
    if prev is None:
        return []
    window = int(rule.params.get("window_days", 7))
    include_core = bool(rule.params.get("include_core", False))
    earliest = prev.as_of - timedelta(days=window)
    out = []
    for p in s.positions:
        if not include_core and ctx.asset(p.symbol).sleeve == Sleeve.CORE:
            continue
        before = prev.position(p.symbol)
        prev_qty = before.quantity if before else 0.0
        if p.quantity <= prev_qty + EPS:
            continue
        checked = any(
            g.symbol == p.symbol and earliest <= g.checked_at <= s.as_of for g in ctx.gate_records
        )
        if not checked:
            out.append(_v(rule, f"{p.symbol} increased from {prev_qty:g} to {p.quantity:g} "
                                f"between {prev.as_of} and {s.as_of} with no pre-trade check",
                          symbol=p.symbol, observed=p.quantity, limit=prev_qty))
    return out


def _gate_only(s: Snapshot, rule: Rule, ctx: Context) -> list[Violation]:
    return []


RULES: dict[str, RuleFn] = {
    "CORE_TARGET_WEIGHT": core_target_weight,
    "SATELLITE_MAX_WEIGHT": satellite_max_weight,
    "SINGLE_MAX_WEIGHT_NAV": single_max_weight_nav,
    "MEMO_REQUIRED_ABOVE": memo_required_above,
    "EXPOSURE_MAX_WEIGHT": exposure_max_weight,
    "SINGLE_MAX_WEIGHT_INVESTED": single_max_weight_invested,
    "TOP3_MAX_WEIGHT_INVESTED": top3_max_weight_invested,
    "LOSS_REVIEW_TRIGGER": loss_review_trigger,
    "REVIEW_OVERDUE": review_overdue,
    "UNCLASSIFIED_POSITION": unclassified_position,
    "MISSING_INVALIDATION": missing_invalidation,
    "UNCHECKED_TRADE": unchecked_trade,
    **{code: _gate_only for code in GATE_ONLY_CODES},
}

# Rules whose result depends only on position sizes; re-run on the simulated
# post-trade snapshot by the gate.
CONCENTRATION_CODES = [
    "SATELLITE_MAX_WEIGHT",
    "SINGLE_MAX_WEIGHT_NAV",
    "MEMO_REQUIRED_ABOVE",
    "EXPOSURE_MAX_WEIGHT",
    "SINGLE_MAX_WEIGHT_INVESTED",
    "TOP3_MAX_WEIGHT_INVESTED",
]
