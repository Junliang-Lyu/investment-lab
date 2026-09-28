from datetime import date

import pytest
from conftest import codes, core, ctx, sat, snap

from investment_core import evaluate
from investment_core.engine import UnknownRuleError
from investment_core.models import (
    Asset,
    GateRecord,
    MemoStatus,
    MemoSummary,
    Rule,
    RuleSet,
    Severity,
    Sleeve,
)


def test_single_stock_below_limit_pattern(draft_rules):
    """One stock just under the 15% single-position limit with no memo,
    no Core, and a tiny unclassified position."""
    s = snap(8_550, ("AAAA", 1_400), ("FRAC", 50))
    c = ctx(sat("AAAA", "us_megacap_tech"), Asset(symbol="FRAC"))
    found = codes(evaluate(s, draft_rules, c))
    assert "MEMO_REQUIRED_ABOVE" in found
    assert "CORE_TARGET_WEIGHT" in found
    assert "UNCLASSIFIED_POSITION" in found
    assert "SINGLE_MAX_WEIGHT_NAV" not in found  # 14% < 15%


def test_two_stock_concentration_pattern(draft_rules):
    """Two stocks ~25% each, same theme, rest cash."""
    s = snap(5_000, ("AAAA", 2_500), ("BBBB", 2_500))
    c = ctx(sat("AAAA", "us_megacap_tech"), sat("BBBB", "us_megacap_tech"))
    found = codes(evaluate(s, draft_rules, c))
    for expected in ("SATELLITE_MAX_WEIGHT", "EXPOSURE_MAX_WEIGHT", "CORE_TARGET_WEIGHT",
                     "TOP3_MAX_WEIGHT_INVESTED", "SINGLE_MAX_WEIGHT_NAV", "MEMO_REQUIRED_ABOVE"):
        assert expected in found
    assert found.count("SINGLE_MAX_WEIGHT_NAV") == 2


def test_draft_rules_never_break(draft_rules):
    s = snap(5_000, ("AAAA", 2_500), ("BBBB", 2_500))
    c = ctx(sat("AAAA", "t"), sat("BBBB", "t"))
    assert all(v.severity != Severity.BREAK for v in evaluate(s, draft_rules, c))


def test_exposure_uses_tags_not_sectors(draft_rules):
    """Different GICS sectors, same user theme -> concentration is combined."""
    s = snap(6_000, ("AAAA", 2_000), ("BBBB", 2_000))
    same = ctx(sat("AAAA", "us_megacap_tech"), sat("BBBB", "us_megacap_tech"))
    diff = ctx(sat("AAAA", "theme_a"), sat("BBBB", "theme_b"))
    assert "EXPOSURE_MAX_WEIGHT" in codes(evaluate(s, draft_rules, same))  # 40% > 35%
    assert "EXPOSURE_MAX_WEIGHT" not in codes(evaluate(s, draft_rules, diff))


def test_memo_status_threshold(draft_rules):
    s = snap(8_800, ("AAAA", 1_200))
    early = MemoSummary(symbol="AAAA", status=MemoStatus.SKEPTIC_DONE, invalidation_count=3)
    done = MemoSummary(symbol="AAAA", status=MemoStatus.FINAL, invalidation_count=3)
    assert "MEMO_REQUIRED_ABOVE" in codes(evaluate(s, draft_rules, ctx(sat("AAAA"), memos=[early])))
    assert "MEMO_REQUIRED_ABOVE" not in codes(evaluate(s, draft_rules, ctx(sat("AAAA"), memos=[done])))


def test_core_is_exempt_from_single_stock_limits(draft_rules):
    s = snap(1_000, ("CORE1", 9_000))
    found = codes(evaluate(s, draft_rules, ctx(core("CORE1", "us_large_cap"))))
    assert "SINGLE_MAX_WEIGHT_NAV" not in found
    assert "SINGLE_MAX_WEIGHT_INVESTED" not in found
    assert "TOP3_MAX_WEIGHT_INVESTED" not in found
    assert "CORE_TARGET_WEIGHT" not in found


def test_top3_count_core_param():
    rs = RuleSet(version="t", rules=[Rule(code="TOP3_MAX_WEIGHT_INVESTED", params={"max": 0.9, "count_core": True})])
    s = snap(1_000, ("C1", 6_000), ("C2", 3_000))
    assert codes(evaluate(s, rs, ctx(core("C1"), core("C2")))) == ["TOP3_MAX_WEIGHT_INVESTED"]


def test_unchecked_trade(draft_rules):
    prev = snap(8_000, ("AAAA", 2_000, 20), as_of=date(2026, 9, 1))
    now = snap(7_000, ("AAAA", 3_000, 30))
    assets = [sat("AAAA")]
    missing = ctx(*assets, previous_snapshot=prev)
    checked = ctx(*assets, previous_snapshot=prev,
                  gate_records=[GateRecord(symbol="AAAA", checked_at=date(2026, 9, 10))])
    stale = ctx(*assets, previous_snapshot=prev,
                gate_records=[GateRecord(symbol="AAAA", checked_at=date(2026, 8, 1))])
    assert "UNCHECKED_TRADE" in codes(evaluate(now, draft_rules, missing))
    assert "UNCHECKED_TRADE" not in codes(evaluate(now, draft_rules, checked))
    assert "UNCHECKED_TRADE" in codes(evaluate(now, draft_rules, stale))


def test_unchecked_trade_new_position_and_core_excluded(draft_rules):
    prev = snap(10_000, as_of=date(2026, 9, 1))
    now = snap(8_000, ("AAAA", 1_000, 10), ("CORE1", 1_000, 2))
    found = evaluate(now, draft_rules, ctx(sat("AAAA"), core("CORE1"), previous_snapshot=prev))
    unchecked = [v.symbol for v in found if v.rule_code == "UNCHECKED_TRADE"]
    assert unchecked == ["AAAA"]


def test_review_overdue(draft_rules):
    s = snap(10_000)
    assert "REVIEW_OVERDUE" in codes(evaluate(s, draft_rules, ctx(last_review_date=date(2026, 6, 6))))
    assert "REVIEW_OVERDUE" in codes(evaluate(s, draft_rules, ctx(last_review_date=None)))
    assert "REVIEW_OVERDUE" not in codes(evaluate(s, draft_rules, ctx(last_review_date=date(2026, 9, 1))))


def test_loss_review_trigger(draft_rules):
    s = snap(9_000, ("AAAA", 1_000, 10, -650))
    v = [x for x in evaluate(s, draft_rules, ctx(sat("AAAA"))) if x.rule_code == "LOSS_REVIEW_TRIGGER"]
    assert len(v) == 1 and v[0].severity == Severity.INFO


def test_missing_invalidation(draft_rules):
    s = snap(9_500, ("AAAA", 500))
    two = MemoSummary(symbol="AAAA", status=MemoStatus.FINAL, invalidation_count=2)
    three = MemoSummary(symbol="AAAA", status=MemoStatus.FINAL, invalidation_count=3)
    assert "MISSING_INVALIDATION" in codes(evaluate(s, draft_rules, ctx(sat("AAAA"), memos=[two])))
    assert "MISSING_INVALIDATION" not in codes(evaluate(s, draft_rules, ctx(sat("AAAA"), memos=[three])))


def test_unknown_rule_code_fails_loudly():
    with pytest.raises(UnknownRuleError):
        evaluate(snap(10_000), RuleSet(version="t", rules=[Rule(code="TYPO_RULE")]))


def test_disabled_rule_is_skipped():
    rs = RuleSet(version="t", rules=[Rule(code="CORE_TARGET_WEIGHT", params={"target": 0.7}, enabled=False)])
    assert evaluate(snap(10_000), rs) == []


def test_results_sorted_by_severity(demo_rules):
    s = snap(5_000, ("AAAA", 2_500), ("BBBB", 2_500))
    found = evaluate(s, demo_rules, ctx(sat("AAAA", "t"), sat("BBBB", "t")))
    ranks = [v.severity.rank for v in found]
    assert ranks == sorted(ranks, reverse=True)


def test_unclassified_default_asset(draft_rules):
    s = snap(9_000, ("ZZZZ", 1_000))
    v = [x for x in evaluate(s, draft_rules, ctx()) if x.rule_code == "UNCLASSIFIED_POSITION"]
    assert v and v[0].symbol == "ZZZZ"
    assert Asset(symbol="ZZZZ").sleeve == Sleeve.UNCLASSIFIED


def test_exposure_excludes_core_by_default(draft_rules):
    s = snap(1_000, ("CORE1", 7_000), ("AAAA", 2_000))
    c = ctx(core("CORE1", "us_large_cap"), sat("AAAA", "us_large_cap"))
    assert "EXPOSURE_MAX_WEIGHT" not in codes(evaluate(s, draft_rules, c))
    rs = RuleSet(version="t", rules=[Rule(code="EXPOSURE_MAX_WEIGHT", params={"max": 0.35, "count_core": True})])
    assert codes(evaluate(s, rs, c)) == ["EXPOSURE_MAX_WEIGHT"]


def test_invested_basis_rules_fire_on_mostly_cash_portfolio(draft_rules):
    """Documented behaviour: with mostly cash, one small stock is 100% of
    invested capital. Kept as-is; revisit when finalising rules."""
    s = snap(9_500, ("AAAA", 500))
    found = codes(evaluate(s, draft_rules, ctx(sat("AAAA"))))
    assert "SINGLE_MAX_WEIGHT_INVESTED" in found and "TOP3_MAX_WEIGHT_INVESTED" in found
