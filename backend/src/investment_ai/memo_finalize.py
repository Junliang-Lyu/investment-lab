"""Step 7: finalise a memo through the core state machine (DESIGN §6.4)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from investment_core.memo import AIReview, Memo, MemoContent, Skeptic, TransitionError, UserA, transition
from investment_core.models import MemoDecision, MemoStatus

from .memo_parse import ParsedMemo

DECISION_ZH = {"watchlist": "只继续观察（watchlist）", "paper": "进入模拟盘（paper）",
               "eligible_for_gate": "可走实盘前检查清单"}


def build_memo(pm: ParsedMemo, review_passed: bool | None) -> Memo:
    content = MemoContent(
        one_liner=pm.one_liner,
        skeptic=Skeptic(top3=pm.bear[:3], weakest_assumption=pm.weakest_assumption),
        user_a=UserA(reasons=pm.reasons, target_weight=pm.target_weight),
        user_b=[pm.responses.get(f"E{i}", "") for i in range(1, 4)],
        user_c=pm.invalidation,
        user_d=pm.review_date,
        ai_review=None if review_passed is None else AIReview(passed=review_passed),
    )
    return Memo(symbol=pm.ticker, status=MemoStatus.SKEPTIC_DONE, content=content)


def finalize(pm: ParsedMemo, decision: MemoDecision, *, review_passed: bool | None, reason: str | None) -> Memo:
    """Walk SKEPTIC_DONE -> USER_RESPONDED -> REVIEWED -> FINAL. Raises TransitionError with what is missing."""
    memo = build_memo(pm, review_passed)
    memo = transition(memo, MemoStatus.USER_RESPONDED, "user")
    memo = transition(memo, MemoStatus.REVIEWED, "user" if review_passed is None else "ai",
                      override_reason=reason if review_passed is None else None)
    return transition(memo, MemoStatus.FINAL, "user", decision=decision, override_reason=reason)


def render_final(memo: Memo, reason: str | None, language: str = "zh", today: date | None = None) -> str:
    zh = language == "zh"
    L = ["", "---", "", f"## {'Step 7 定稿' if zh else 'Step 7 decision'}（{today or date.today()}）", "",
         f"- {'结论' if zh else 'Decision'}：{DECISION_ZH[memo.decision.value] if zh else memo.decision.value}"]
    if reason:
        L.append(f"- {'理由（覆盖记录）' if zh else 'Override reason'}：{reason}")
    L.append(f"- {'状态流转' if zh else 'Transitions'}：" + " → ".join(
        [memo.events[0].from_status.value] + [e.to_status.value for e in memo.events]))
    return "\n".join(L) + "\n"


def write_final_into_memo(path: Path, section: str) -> None:
    md = path.read_text(encoding="utf-8")
    m = re.search(r"\n---\n\n## Step 7 (?:定稿|decision)", md)
    if m:
        md = md[:m.start()]
    path.write_text(md.rstrip("\n") + "\n" + section, encoding="utf-8")
