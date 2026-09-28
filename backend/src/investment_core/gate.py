"""Pre-trade gate. See docs/DESIGN.md §6.3.

Mirrors personal_investment_system_v0/实盘前检查清单.md. The gate never says
"buy"; its overall result is one of: clear, incomplete, warnings, rule_breaks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .engine import evaluate
from .models import (
    EPS,
    Context,
    MemoDecision,
    MemoStatus,
    Position,
    Rule,
    RuleSet,
    Severity,
    Sleeve,
    Snapshot,
    TradeProposal,
    Violation,
)
from .rules import CONCENTRATION_CODES

ItemStatus = Literal["pass", "fail", "unknown", "not_applicable"]
Overall = Literal["clear", "incomplete", "warnings", "rule_breaks"]

BUY_ATTESTATIONS = {
    "not_fomo": "Not driven by a short-term price rise (FOMO)",
    "not_averaging_down": "Not buying mainly to lower the average cost of a losing position",
    "drawdown_ok": "A 20-30% drop in this position would not affect my life or my discipline",
    "knows_invalidation": "I know what would prove this thesis wrong",
    "not_ai_decision": "This is my decision, not one made by AI",
}
SELL_ATTESTATIONS = {
    "thesis_based": "Selling because of the thesis or its invalidation conditions, not short-term price moves",
    "not_ai_decision": "This is my decision, not one made by AI",
}


class GateItem(BaseModel):
    key: str
    section: str
    label: str
    kind: Literal["auto", "self_attest"]
    status: ItemStatus
    severity: Severity = Severity.WARN
    detail: str = ""
    rule_code: str | None = None


class GateResult(BaseModel):
    overall: Overall
    symbol: str
    side: str
    amount_usd: float
    weight_before: float
    weight_after: float
    rule_set_version: str
    items: list[GateItem] = Field(default_factory=list)


def simulate(snapshot: Snapshot, proposal: TradeProposal) -> tuple[Snapshot, float]:
    """Return the post-trade snapshot and the effective trade amount.

    Net liquidation value is unchanged by a trade at market price. Sells are
    capped at the current position value.
    """
    amount = proposal.resolved_amount()
    positions = [p.model_copy() for p in snapshot.positions]
    cash = snapshot.cash
    existing = next((p for p in positions if p.symbol == proposal.symbol), None)

    price = proposal.price
    if price is None and existing and existing.quantity > EPS:
        price = existing.market_value / existing.quantity

    if proposal.side == "buy":
        cash -= amount
        qty_delta = amount / price if price else 0.0
        if existing:
            existing.market_value += amount
            existing.quantity += qty_delta
        else:
            positions.append(Position(symbol=proposal.symbol, quantity=qty_delta, market_value=amount))
    else:
        held = existing.market_value if existing else 0.0
        amount = min(amount, held)
        cash += amount
        if existing:
            frac = amount / existing.market_value if existing.market_value > EPS else 1.0
            existing.market_value -= amount
            existing.quantity -= existing.quantity * frac
            if existing.market_value <= EPS:
                positions.remove(existing)

    post = snapshot.model_copy(update={"positions": positions, "cash": cash})
    return post, amount


def _key(v: Violation) -> tuple[str, str | None]:
    return (v.rule_code, v.symbol)


def _concentration_items(pre: Snapshot, post: Snapshot, proposal: TradeProposal,
                         rule_set: RuleSet, ctx: Context) -> list[GateItem]:
    before = {_key(v): v for v in evaluate(pre, rule_set, ctx, only=CONCENTRATION_CODES)}
    after = evaluate(post, rule_set, ctx, only=CONCENTRATION_CODES)
    items = []
    for code in CONCENTRATION_CODES:
        rule = rule_set.get(code)
        if rule is None:
            continue
        relevant = [v for v in after if v.rule_code == code and v.symbol in (None, proposal.symbol)]
        worsened, preexisting = [], []
        for v in relevant:
            prev = before.get(_key(v))
            if prev is None or (v.observed or 0) > (prev.observed or 0) + EPS:
                worsened.append(v)
            else:
                preexisting.append(v)
        if worsened:
            status, detail = "fail", "; ".join(v.message for v in worsened)
        elif preexisting:
            status = "pass"
            detail = "Not made worse by this trade (already over limit: " + \
                     "; ".join(v.message for v in preexisting) + ")"
        else:
            status, detail = "pass", "Within limit after this trade"
        items.append(GateItem(key=f"concentration:{code}", section="§6 position & concentration",
                              label=code, kind="auto", status=status, severity=rule.severity,
                              detail=detail, rule_code=code))
    return items


def _sev(rule: Rule | None) -> Severity:
    return rule.severity if rule else Severity.WARN


def _memo_items(proposal: TradeProposal, rule_set: RuleSet, ctx: Context) -> list[GateItem]:
    memo_rule = rule_set.get("MEMO_REQUIRED_ABOVE")
    min_status = MemoStatus((memo_rule.params if memo_rule else {}).get("min_status", "reviewed"))
    inval_rule = rule_set.get("MISSING_INVALIDATION")
    need_inval = int((inval_rule.params if inval_rule else {}).get("min_conditions", 3))
    memo = ctx.memos.get(proposal.symbol)
    sev = _sev(memo_rule)

    def item(key, section, label, ok, detail, severity=sev, rule_code="MEMO_REQUIRED_ABOVE"):
        return GateItem(key=key, section=section, label=label, kind="auto",
                        status="pass" if ok else "fail", severity=severity,
                        detail=detail, rule_code=rule_code)

    if memo is None:
        return [item("memo_reviewed", "§4 investment memo", "Memo exists and has been reviewed",
                     False, "No memo for this symbol")]
    return [
        item("memo_reviewed", "§4 investment memo", "Memo exists and has been reviewed",
             memo.status.rank >= min_status.rank,
             f"Memo status '{memo.status.value}', required '{min_status.value}'"),
        item("skeptic_done", "§5 skeptic review", "Three strongest counter-arguments recorded",
             memo.status.rank >= MemoStatus.SKEPTIC_DONE.rank, f"Memo status '{memo.status.value}'"),
        item("memo_decision", "§4 investment memo", "Memo conclusion allows the pre-trade gate",
             memo.decision == MemoDecision.ELIGIBLE_FOR_GATE,
             f"Memo decision: {memo.decision.value if memo.decision else 'none'}"),
        item("invalidation", "§8 exit & invalidation", f"At least {need_inval} invalidation conditions",
             memo.invalidation_count >= need_inval,
             f"{memo.invalidation_count} recorded", severity=_sev(inval_rule),
             rule_code="MISSING_INVALIDATION"),
    ]


def _paper_items(pre: Snapshot, proposal: TradeProposal, amount: float,
                 rule_set: RuleSet, ctx: Context) -> list[GateItem]:
    today = ctx.effective_today(pre)
    entry = ctx.watchlist.get(proposal.symbol)
    items = []
    weeks_rule = rule_set.get("PAPER_MIN_WEEKS")
    if weeks_rule:
        need = int(weeks_rule.params["weeks"])
        weeks = (today - entry.started_at).days / 7 if entry else 0.0
        items.append(GateItem(key="paper_weeks", section="§7 paper to live", kind="auto",
                              label=f"Tracked on paper/watchlist for at least {need} weeks",
                              status="pass" if entry and weeks + EPS >= need else "fail",
                              severity=weeks_rule.severity, rule_code=weeks_rule.code,
                              detail=f"{weeks:.1f} weeks" if entry else "Not on paper/watchlist"))
    reviews_rule = rule_set.get("PAPER_MIN_REVIEWS")
    if reviews_rule:
        need = int(reviews_rule.params["reviews"])
        have = entry.review_count if entry else 0
        items.append(GateItem(key="paper_reviews", section="§7 paper to live", kind="auto",
                              label=f"At least {need} reviews while on paper/watchlist",
                              status="pass" if have >= need else "fail",
                              severity=reviews_rule.severity, rule_code=reviews_rule.code,
                              detail=f"{have} review(s)"))
    first_rule = rule_set.get("FIRST_LIVE_MAX_USD")
    if first_rule:
        cap = float(first_rule.params["max_usd"])
        items.append(GateItem(key="first_live_size", section="§7 paper to live", kind="auto",
                              label=f"First live position no larger than ${cap:,.0f}",
                              status="pass" if amount <= cap + EPS else "fail",
                              severity=first_rule.severity, rule_code=first_rule.code,
                              detail=f"Proposed ${amount:,.0f}"))
    return items


def _attest_items(proposal: TradeProposal) -> list[GateItem]:
    labels = BUY_ATTESTATIONS if proposal.side == "buy" else SELL_ATTESTATIONS
    items = []
    for key, label in labels.items():
        answer = proposal.attestations.get(key)
        status: ItemStatus = "unknown" if answer is None else ("pass" if answer else "fail")
        items.append(GateItem(key=f"attest:{key}", section="§10 final gate", label=label,
                              kind="self_attest", status=status, severity=Severity.WARN))
    return items


def _overall(items: list[GateItem]) -> Overall:
    failed = [i for i in items if i.status == "fail"]
    if any(i.severity == Severity.BREAK for i in failed):
        return "rule_breaks"
    if failed:
        return "warnings"
    if any(i.status == "unknown" for i in items):
        return "incomplete"
    return "clear"


def evaluate_trade(snapshot: Snapshot, proposal: TradeProposal, rule_set: RuleSet,
                   ctx: Context | None = None) -> GateResult:
    ctx = ctx or Context()
    items: list[GateItem] = []
    held = snapshot.position(proposal.symbol)

    if proposal.side == "sell" and held is None:
        raise ValueError(f"cannot sell {proposal.symbol}: no position")

    post, amount = simulate(snapshot, proposal)

    if proposal.side == "buy":
        enough = snapshot.cash + EPS >= amount
        items.append(GateItem(key="cash", section="§1 basics", label="Enough cash (margin is not modeled)",
                              kind="auto", status="pass" if enough else "fail", severity=Severity.BREAK,
                              detail=f"Cash ${snapshot.cash:,.0f}, trade ${amount:,.0f}"))

    items.extend(_concentration_items(snapshot, post, proposal, rule_set, ctx))

    is_core = ctx.asset(proposal.symbol).sleeve == Sleeve.CORE
    if proposal.side == "buy":
        if is_core:
            items.append(GateItem(key="memo_reviewed", section="§4 investment memo",
                                  label="Memo exists and has been reviewed", kind="auto",
                                  status="not_applicable",
                                  detail="Core position: covered by the core strategy SOP, not the memo SOP"))
        else:
            items.extend(_memo_items(proposal, rule_set, ctx))
            if held is None:
                items.extend(_paper_items(snapshot, proposal, amount, rule_set, ctx))
            else:
                items.append(GateItem(key="paper_weeks", section="§7 paper to live",
                                      label="Paper-to-live checks", kind="auto", status="not_applicable",
                                      detail="Already held live; applies to new positions only"))
    items.extend(_attest_items(proposal))

    before_mv = held.market_value if held else 0.0
    after = post.position(proposal.symbol)
    return GateResult(
        overall=_overall(items),
        symbol=proposal.symbol,
        side=proposal.side,
        amount_usd=amount,
        weight_before=snapshot.weight_nav(before_mv),
        weight_after=post.weight_nav(after.market_value if after else 0.0),
        rule_set_version=rule_set.version,
        items=items,
    )
