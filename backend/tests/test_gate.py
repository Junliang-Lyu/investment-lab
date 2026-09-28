from datetime import date

import pytest
from conftest import core, ctx, sat, snap

from investment_core import evaluate_trade
from investment_core.gate import BUY_ATTESTATIONS
from investment_core.models import MemoDecision, MemoStatus, MemoSummary, TradeProposal, WatchEntry


def items(result):
    return {i.key: i for i in result.items}


GOOD_MEMO = MemoSummary(symbol="AAAA", status=MemoStatus.FINAL,
                        decision=MemoDecision.ELIGIBLE_FOR_GATE, invalidation_count=3)
ALL_YES = {k: True for k in BUY_ATTESTATIONS}


def test_buy_pushes_over_single_stock_limit(demo_rules):
    s = snap(8_600, ("AAAA", 1_400))
    r = evaluate_trade(s, TradeProposal(symbol="AAAA", amount_usd=200), demo_rules, ctx(sat("AAAA")))
    it = items(r)
    assert it["concentration:SINGLE_MAX_WEIGHT_NAV"].status == "fail"
    assert it["memo_reviewed"].status == "fail"
    assert it["attest:not_fomo"].status == "unknown"
    assert r.overall == "rule_breaks"
    assert r.weight_before == pytest.approx(0.14)
    assert r.weight_after == pytest.approx(0.16)


def test_draft_rules_give_warnings_not_breaks(draft_rules):
    s = snap(8_600, ("AAAA", 1_400))
    r = evaluate_trade(s, TradeProposal(symbol="AAAA", amount_usd=200), draft_rules, ctx(sat("AAAA")))
    assert r.overall == "warnings"


def test_clean_add_with_attestations_is_clear(demo_rules):
    s = snap(9_500, ("AAAA", 500))
    r = evaluate_trade(s, TradeProposal(symbol="AAAA", amount_usd=200, attestations=ALL_YES),
                       demo_rules, ctx(sat("AAAA", "t1"), memos=[GOOD_MEMO]))
    assert r.overall == "clear", [(i.key, i.status, i.detail) for i in r.items if i.status != "pass"]


def test_missing_attestation_is_incomplete(demo_rules):
    s = snap(9_500, ("AAAA", 500))
    r = evaluate_trade(s, TradeProposal(symbol="AAAA", amount_usd=200),
                       demo_rules, ctx(sat("AAAA", "t1"), memos=[GOOD_MEMO]))
    assert r.overall == "incomplete"


def test_new_position_paper_checks(demo_rules):
    # Hold some Core so a small first position is not 100% of invested capital.
    s = snap(3_000, ("CORE1", 7_000))
    memo = GOOD_MEMO.model_copy(update={"symbol": "NEWW"})
    ready = WatchEntry(symbol="NEWW", started_at=date(2026, 8, 1), review_count=2)
    fresh = WatchEntry(symbol="NEWW", started_at=date(2026, 9, 15), review_count=0)
    base = dict(memos=[memo])

    ok = evaluate_trade(s, TradeProposal(symbol="NEWW", amount_usd=800, attestations=ALL_YES), demo_rules,
                        ctx(sat("NEWW"), core("CORE1"), watchlist={"NEWW": ready}, **base))
    assert ok.overall == "clear"

    early = items(evaluate_trade(s, TradeProposal(symbol="NEWW", amount_usd=800), demo_rules,
                                 ctx(sat("NEWW"), watchlist={"NEWW": fresh}, **base)))
    assert early["paper_weeks"].status == "fail"
    assert early["paper_reviews"].status == "fail"

    big = items(evaluate_trade(s, TradeProposal(symbol="NEWW", amount_usd=1_500), demo_rules,
                               ctx(sat("NEWW"), watchlist={"NEWW": ready}, **base)))
    assert big["first_live_size"].status == "fail"


def test_existing_position_skips_paper_checks(demo_rules):
    s = snap(9_500, ("AAAA", 500))
    it = items(evaluate_trade(s, TradeProposal(symbol="AAAA", amount_usd=100), demo_rules,
                              ctx(sat("AAAA"), memos=[GOOD_MEMO])))
    assert it["paper_weeks"].status == "not_applicable"
    assert "first_live_size" not in it


def test_core_buy_does_not_need_memo(demo_rules):
    s = snap(10_000)
    it = items(evaluate_trade(s, TradeProposal(symbol="CORE1", amount_usd=5_000), demo_rules,
                              ctx(core("CORE1", "us_large_cap"))))
    assert it["memo_reviewed"].status == "not_applicable"
    assert "paper_weeks" not in it


def test_insufficient_cash(demo_rules):
    s = snap(100, ("CORE1", 9_900))
    it = items(evaluate_trade(s, TradeProposal(symbol="CORE1", amount_usd=500), demo_rules, ctx(core("CORE1"))))
    assert it["cash"].status == "fail"


def test_preexisting_violation_not_blamed_on_unrelated_trade(demo_rules):
    """Satellite already over its cap; buying Core does not make it worse."""
    s = snap(6_000, ("AAAA", 4_000))
    r = evaluate_trade(s, TradeProposal(symbol="CORE1", amount_usd=1_000), demo_rules,
                       ctx(sat("AAAA"), core("CORE1")))
    sat_item = items(r)["concentration:SATELLITE_MAX_WEIGHT"]
    assert sat_item.status == "pass"
    assert "already over limit" in sat_item.detail


def test_sell_reduces_and_caps(demo_rules):
    s = snap(6_000, ("AAAA", 4_000, 40))
    r = evaluate_trade(s, TradeProposal(symbol="AAAA", side="sell", amount_usd=10_000), demo_rules, ctx(sat("AAAA")))
    assert r.amount_usd == 4_000
    assert r.weight_after == 0
    assert all(i.status != "fail" for i in r.items if i.kind == "auto")


def test_sell_without_position_raises(demo_rules):
    with pytest.raises(ValueError):
        evaluate_trade(snap(10_000), TradeProposal(symbol="AAAA", side="sell", amount_usd=1), demo_rules)


def test_amount_from_quantity_and_price(demo_rules):
    r = evaluate_trade(snap(10_000), TradeProposal(symbol="CORE1", quantity=2, price=250), demo_rules,
                       ctx(core("CORE1")))
    assert r.amount_usd == 500


def test_amount_required():
    with pytest.raises(ValueError):
        TradeProposal(symbol="X").resolved_amount()
