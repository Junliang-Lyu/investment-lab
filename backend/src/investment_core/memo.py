"""Memo state machine. See docs/DESIGN.md §6.4 and investment_memo_SOP_v0.md.

    idea -> researching -> skeptic_done -> user_responded -> reviewed -> final
     (1)      (2-3)           (4)             (5 §A-§D)        (6)       (7)

Rules enforced here:
- Only legal transitions are allowed.
- §A-§D (and the one-line thesis) can only be written by the user, never by AI.
- Overrides are allowed only where the SOP permits judgement (skipping the AI
  review, or finalising with unresolved review issues), and always need a reason.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .models import MemoDecision, MemoStatus, MemoSummary

Actor = Literal["user", "ai", "import"]

S = MemoStatus
TRANSITIONS: dict[MemoStatus, set[MemoStatus]] = {
    S.IDEA: {S.RESEARCHING, S.ARCHIVED},
    S.RESEARCHING: {S.SKEPTIC_DONE, S.ARCHIVED},
    S.SKEPTIC_DONE: {S.USER_RESPONDED, S.ARCHIVED},
    # Back to SKEPTIC_DONE when the user edits §A-§D again and they are incomplete for now (the review is cleared).
    S.USER_RESPONDED: {S.REVIEWED, S.SKEPTIC_DONE, S.ARCHIVED},
    S.REVIEWED: {S.FINAL, S.USER_RESPONDED, S.SKEPTIC_DONE, S.ARCHIVED},  # back to §A-§D after review feedback
    S.FINAL: {S.RESEARCHING, S.ARCHIVED},                  # re-open as a new version
    S.ARCHIVED: set(),
}

USER_ONLY_FIELDS = {"one_liner", "user_a", "user_b", "user_c", "user_d"}


class Claim(BaseModel):
    text: str
    type: Literal["fact", "inference", "to_verify"] = "inference"
    evidence_refs: list[str] = Field(default_factory=list)


class Skeptic(BaseModel):
    top3: list[str] = Field(default_factory=list)
    weakest_assumption: str = ""


class UserA(BaseModel):
    reasons: list[str] = Field(default_factory=list)
    target_weight: float | None = None


class AIReview(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)


class MemoContent(BaseModel):
    one_liner: str = ""
    business: dict[str, str] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    bull: list[Claim] = Field(default_factory=list)
    bear: list[Claim] = Field(default_factory=list)
    valuation_context: str = ""
    skeptic: Skeptic | None = None
    user_a: UserA | None = None      # §A reasons + target weight
    user_b: list[str] = Field(default_factory=list)  # §B responses to the 3 counter-arguments
    user_c: list[str] = Field(default_factory=list)  # §C invalidation conditions
    user_d: date | None = None       # §D next review date
    ai_review: AIReview | None = None


class MemoEvent(BaseModel):
    from_status: MemoStatus
    to_status: MemoStatus
    actor: Actor
    at: datetime
    override_reason: str | None = None


class Memo(BaseModel):
    symbol: str
    status: MemoStatus = MemoStatus.IDEA
    version: int = 1
    decision: MemoDecision | None = None
    content: MemoContent = Field(default_factory=MemoContent)
    events: list[MemoEvent] = Field(default_factory=list)

    def summary(self) -> MemoSummary:
        return MemoSummary(
            symbol=self.symbol,
            status=self.status,
            decision=self.decision,
            invalidation_count=len([c for c in self.content.user_c if c.strip()]),
            review_date=self.content.user_d,
            has_unresolved_review_issues=bool(self.content.ai_review and not self.content.ai_review.passed),
        )


class TransitionError(ValueError):
    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []


class FieldOwnershipError(PermissionError):
    pass


def update_content(memo: Memo, patch: dict, actor: Actor) -> Memo:
    """Apply a partial update to memo content, enforcing field ownership."""
    forbidden = USER_ONLY_FIELDS & set(patch) if actor == "ai" else set()
    if forbidden:
        raise FieldOwnershipError(f"AI cannot write user-only fields: {sorted(forbidden)}")
    unknown = set(patch) - set(MemoContent.model_fields)
    if unknown:
        raise ValueError(f"unknown memo fields: {sorted(unknown)}")
    merged = memo.content.model_dump() | patch
    return memo.model_copy(update={"content": MemoContent.model_validate(merged)})


def _nonempty(xs: list[str]) -> list[str]:
    return [x for x in xs if x and x.strip()]


def missing_for(memo: Memo, to: MemoStatus) -> list[str]:
    c = memo.content
    missing: list[str] = []
    if to == S.RESEARCHING and memo.status == S.IDEA and not c.one_liner.strip():
        missing.append("one_liner (Step 1)")
    if to == S.SKEPTIC_DONE:
        if c.skeptic is None or len(_nonempty(c.skeptic.top3)) != 3:
            missing.append("skeptic.top3 (exactly 3 counter-arguments)")
        if c.skeptic is None or not c.skeptic.weakest_assumption.strip():
            missing.append("skeptic.weakest_assumption")
    if to == S.USER_RESPONDED:
        if c.user_a is None or not _nonempty(c.user_a.reasons):
            missing.append("§A reasons")
        if c.user_a is None or c.user_a.target_weight is None:
            missing.append("§A target weight")
        if len(_nonempty(c.user_b)) != 3:
            missing.append("§B responses to all 3 counter-arguments")
        if len(_nonempty(c.user_c)) < 3:
            missing.append("§C at least 3 invalidation conditions")
        if c.user_d is None:
            missing.append("§D next review date")
    if to == S.REVIEWED and c.ai_review is None:
        missing.append("Step 6 AI review")
    return missing


def transition(memo: Memo, to: MemoStatus, actor: Actor, *,
               decision: MemoDecision | None = None,
               override_reason: str | None = None,
               now: datetime | None = None) -> Memo:
    """Return a new memo in state `to`, or raise TransitionError."""
    if to not in TRANSITIONS[memo.status]:
        raise TransitionError(f"illegal transition {memo.status.value} -> {to.value}")
    if actor == "ai" and to in {S.USER_RESPONDED, S.FINAL}:
        raise TransitionError(f"only the user can move a memo to {to.value}")

    reason = (override_reason or "").strip() or None
    missing = missing_for(memo, to) if to != S.ARCHIVED else []
    if missing:
        # Only skipping the AI review may be overridden; §A-§D never.
        if to == S.REVIEWED and reason:
            pass
        else:
            raise TransitionError(f"cannot move to {to.value}: missing {', '.join(missing)}", missing)

    update: dict = {"status": to}
    if to == S.FINAL:
        if decision is None:
            raise TransitionError("final requires a decision: watchlist, paper or eligible_for_gate")
        unresolved = memo.content.ai_review is not None and not memo.content.ai_review.passed
        if decision == MemoDecision.ELIGIBLE_FOR_GATE and unresolved and not reason:
            raise TransitionError("AI review has unresolved issues; eligible_for_gate needs an override reason")
        update["decision"] = decision
    if to == S.RESEARCHING and memo.status == S.FINAL:
        update["version"] = memo.version + 1
        update["decision"] = None
        update["content"] = memo.content.model_copy(update={"ai_review": None})
    if to in (S.USER_RESPONDED, S.SKEPTIC_DONE) and memo.status in (S.USER_RESPONDED, S.REVIEWED):
        update["content"] = memo.content.model_copy(update={"ai_review": None})

    event = MemoEvent(from_status=memo.status, to_status=to, actor=actor,
                      at=now or datetime.now(timezone.utc), override_reason=reason)
    update["events"] = [*memo.events, event]
    return memo.model_copy(update=update)
