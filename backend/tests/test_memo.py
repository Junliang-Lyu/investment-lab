from datetime import date

import pytest

from investment_core.memo import (
    AIReview,
    FieldOwnershipError,
    Memo,
    Skeptic,
    TransitionError,
    UserA,
    transition,
    update_content,
)
from investment_core.models import MemoDecision, MemoStatus as S


def to_user_responded() -> Memo:
    m = Memo(symbol="AAAA")
    m = update_content(m, {"one_liner": "Cloud growth sustains margins"}, "user")
    m = transition(m, S.RESEARCHING, "user")
    m = update_content(m, {"skeptic": Skeptic(top3=["a", "b", "c"], weakest_assumption="x").model_dump()}, "ai")
    m = transition(m, S.SKEPTIC_DONE, "ai")
    m = update_content(m, {
        "user_a": UserA(reasons=["r1"], target_weight=0.05).model_dump(),
        "user_b": ["b1", "b2", "b3"],
        "user_c": ["c1", "c2", "c3"],
        "user_d": date(2026, 12, 1),
    }, "user")
    return transition(m, S.USER_RESPONDED, "user")


def test_happy_path_to_final():
    m = to_user_responded()
    m = update_content(m, {"ai_review": AIReview(passed=True).model_dump()}, "ai")
    m = transition(m, S.REVIEWED, "ai")
    m = transition(m, S.FINAL, "user", decision=MemoDecision.ELIGIBLE_FOR_GATE)
    assert m.status == S.FINAL
    assert [e.to_status for e in m.events] == [S.RESEARCHING, S.SKEPTIC_DONE, S.USER_RESPONDED, S.REVIEWED, S.FINAL]
    summary = m.summary()
    assert summary.invalidation_count == 3 and summary.review_date == date(2026, 12, 1)


def test_idea_requires_one_liner():
    with pytest.raises(TransitionError):
        transition(Memo(symbol="AAAA"), S.RESEARCHING, "user")


def test_illegal_transition():
    with pytest.raises(TransitionError):
        transition(Memo(symbol="AAAA"), S.FINAL, "user", decision=MemoDecision.PAPER)


def test_user_fields_required():
    m = Memo(symbol="AAAA", status=S.SKEPTIC_DONE)
    with pytest.raises(TransitionError) as e:
        transition(m, S.USER_RESPONDED, "user")
    assert any("§C" in x for x in e.value.missing)


def test_user_fields_cannot_be_overridden():
    m = Memo(symbol="AAAA", status=S.SKEPTIC_DONE)
    with pytest.raises(TransitionError):
        transition(m, S.USER_RESPONDED, "user", override_reason="trust me")


def test_ai_cannot_write_user_fields():
    with pytest.raises(FieldOwnershipError):
        update_content(Memo(symbol="AAAA"), {"user_c": ["x", "y", "z"]}, "ai")


def test_ai_cannot_finalise():
    m = to_user_responded().model_copy(update={"status": S.REVIEWED})
    with pytest.raises(TransitionError):
        transition(m, S.FINAL, "ai", decision=MemoDecision.PAPER)


def test_skip_ai_review_needs_reason():
    m = to_user_responded()
    with pytest.raises(TransitionError):
        transition(m, S.REVIEWED, "user")
    m2 = transition(m, S.REVIEWED, "user", override_reason="AI review unavailable, reviewed manually")
    assert m2.events[-1].override_reason


def test_unresolved_review_needs_override_for_gate():
    m = to_user_responded()
    m = update_content(m, {"ai_review": AIReview(passed=False, issues=["§C vague"]).model_dump()}, "ai")
    m = transition(m, S.REVIEWED, "ai")
    with pytest.raises(TransitionError):
        transition(m, S.FINAL, "user", decision=MemoDecision.ELIGIBLE_FOR_GATE)
    ok = transition(m, S.FINAL, "user", decision=MemoDecision.WATCHLIST)
    assert ok.decision == MemoDecision.WATCHLIST
    forced = transition(m, S.FINAL, "user", decision=MemoDecision.ELIGIBLE_FOR_GATE, override_reason="accepted risk")
    assert forced.events[-1].override_reason == "accepted risk"


def test_final_requires_decision():
    m = to_user_responded()
    m = update_content(m, {"ai_review": AIReview(passed=True).model_dump()}, "ai")
    m = transition(m, S.REVIEWED, "ai")
    with pytest.raises(TransitionError):
        transition(m, S.FINAL, "user")


def test_reopen_bumps_version_and_clears_review():
    m = to_user_responded()
    m = update_content(m, {"ai_review": AIReview(passed=True).model_dump()}, "ai")
    m = transition(transition(m, S.REVIEWED, "ai"), S.FINAL, "user", decision=MemoDecision.PAPER)
    reopened = transition(m, S.RESEARCHING, "user")
    assert reopened.version == 2 and reopened.decision is None and reopened.content.ai_review is None
    assert reopened.content.user_c == ["c1", "c2", "c3"]


def test_archived_is_terminal():
    m = transition(Memo(symbol="AAAA"), S.ARCHIVED, "user")
    with pytest.raises(TransitionError):
        transition(m, S.RESEARCHING, "user")


def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        update_content(Memo(symbol="AAAA"), {"target_price": 1}, "user")
