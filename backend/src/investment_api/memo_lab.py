"""Public Lab memo workflow (DESIGN §11.5): one memo carries a visitor from a thesis to the pre-trade gate.

    thesis + AI skeptic (§11.4) -> the visitor's own §A-§D -> AI review of those answers (memo Step 6)
    -> decision through the core state machine (Step 7) -> pre-trade gate that reads this memo

Storage: the `memos` table in the Lab SQLite file. There are no accounts; a memo's random 128-bit id is the
capability ("anyone with the link can view and edit it"). Memos untouched for `memo_retention_days` are deleted,
and a visitor can delete one at any time. IPs are kept only as the skeptic's daily-rotating salted hashes.

The AI skeptic output is taken from the server's own cache, never from the browser, so a memo cannot carry
forged "AI" content. §A-§D are written only by the visitor (core field ownership); the AI review is stored
in ai_review. Budgets, the model lock and output validation are shared with the skeptic.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from investment_ai.lab_pack import lab_pack
from investment_ai.memo_finalize import DECISION_ZH, render_final
from investment_ai.memo_parse import ParsedMemo, _meaningful
from investment_ai.memo_review import (PROMPT_VERSION as REVIEW_PROMPT_VERSION, MemoReview, ReviewResult,
                                       render_review, run_memo_review)
from investment_core.memo import (AIReview, Memo, MemoContent, Skeptic, TransitionError, UserA, missing_for,
                                  transition)
from investment_core.models import Context, MemoDecision, MemoStatus, WatchEntry

from .skeptic import cache_key, normalize_thesis, render

log = logging.getLogger("investment_api.memo_lab")
S = MemoStatus
MEMO_ID = re.compile(r"^[A-Za-z0-9_-]{20,40}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(text: str) -> str:
    return _CONTROL.sub(" ", text).strip()


# --- request bodies -----------------------------------------------------------------------------------

class CreateMemo(BaseModel):
    ticker: str = Field(max_length=10)
    thesis: str = Field(min_length=10, max_length=400)
    lang: Literal["zh", "en"] = "en"


class Answers(BaseModel):
    """The visitor's own sections. Only the visitor writes these (DESIGN §6.4)."""
    reasons: list[str] = Field(default_factory=list, max_length=5)           # §A
    target_weight_pct: float | None = Field(default=None, gt=0, le=100)     # §A, percent of net value
    responses: dict[Literal["E1", "E2", "E3"], str] = Field(default_factory=dict)  # §B
    invalidation: list[str] = Field(default_factory=list, max_length=6)      # §C
    review_date: date | None = None                                          # §D
    review_focus: str = Field(default="", max_length=300)                    # §D

    @field_validator("reasons")
    @classmethod
    def _reasons(cls, v: list[str]) -> list[str]:
        if any(len(x) > 500 for x in v):
            raise ValueError("each reason must be at most 500 characters")
        return [_clean(x) for x in v]

    @field_validator("responses")
    @classmethod
    def _responses(cls, v: dict) -> dict:
        if any(len(x) > 1000 for x in v.values()):
            raise ValueError("each response must be at most 1000 characters")
        return {k: _clean(x) for k, x in v.items()}

    @field_validator("invalidation")
    @classmethod
    def _conditions(cls, v: list[str]) -> list[str]:
        if any(len(x) > 300 for x in v):
            raise ValueError("each condition must be at most 300 characters")
        return [_clean(x) for x in v]

    @field_validator("review_focus")
    @classmethod
    def _focus(cls, v: str) -> str:
        return _clean(v)


class FinalizeRequest(BaseModel):
    decision: Literal["watchlist", "paper", "eligible_for_gate"]
    reason: str | None = Field(default=None, max_length=500)


# --- store --------------------------------------------------------------------------------------------

class MemoStore:
    """Memos and review requests, in the same SQLite file as the skeptic (LabStore supplies clock and hashing)."""

    def __init__(self, lab, *, retention_days: int = 180, per_ip_daily: int = 10, reviews_per_ip_daily: int = 3,
                 max_memos: int = 5000):
        self.lab, self.retention_days = lab, retention_days
        self.per_ip_daily, self.reviews_per_ip_daily, self.max_memos = per_ip_daily, reviews_per_ip_daily, max_memos
        self._lock = threading.Lock()
        with lab._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memos (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ip_hash TEXT NOT NULL,
                    ticker TEXT NOT NULL, lang TEXT NOT NULL, thesis TEXT NOT NULL, skeptic TEXT NOT NULL,
                    skeptic_meta TEXT NOT NULL, answers TEXT NOT NULL, review TEXT, review_meta TEXT,
                    state TEXT NOT NULL, watch_started TEXT);
                CREATE INDEX IF NOT EXISTS memos_ip ON memos (ip_hash, created_at);
                CREATE INDEX IF NOT EXISTS memos_updated ON memos (updated_at);
                CREATE TABLE IF NOT EXISTS memo_reviews (id INTEGER PRIMARY KEY, at TEXT NOT NULL, ip_hash TEXT NOT NULL,
                                                         status TEXT NOT NULL, cost REAL NOT NULL);
            """)

    def now(self) -> datetime:
        return self.lab.clock()

    def _day_start(self) -> str:
        return self.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    def purge(self) -> int:
        cutoff = (self.now() - timedelta(days=self.retention_days)).isoformat()
        review_cutoff = (self.now() - timedelta(days=self.lab.retention_days)).isoformat()
        with self._lock, self.lab._db() as db:
            db.execute("DELETE FROM memo_reviews WHERE at < ?", (review_cutoff,))
            return db.execute("DELETE FROM memos WHERE updated_at < ?", (cutoff,)).rowcount

    def total(self) -> int:
        with self.lab._db() as db:
            return db.execute("SELECT COUNT(*) FROM memos").fetchone()[0]

    def created_today(self, ip_hash: str) -> int:
        with self.lab._db() as db:
            return db.execute("SELECT COUNT(*) FROM memos WHERE ip_hash=? AND created_at>=?",
                              (ip_hash, self._day_start())).fetchone()[0]

    def reviews_today(self, ip_hash: str) -> int:
        with self.lab._db() as db:
            return db.execute("SELECT COUNT(*) FROM memo_reviews WHERE ip_hash=? AND at>=? AND status!='cached'",
                              (ip_hash, self._day_start())).fetchone()[0]

    def add_review_request(self, ip_hash: str, status: str, cost: float) -> None:
        with self._lock, self.lab._db() as db:
            db.execute("INSERT INTO memo_reviews (at, ip_hash, status, cost) VALUES (?, ?, ?, ?)",
                       (self.now().isoformat(), ip_hash, status, cost))

    def insert(self, rec: dict) -> None:
        cols = list(rec)
        with self._lock, self.lab._db() as db:
            db.execute(f"INSERT INTO memos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [rec[c] for c in cols])

    def get(self, memo_id: str) -> dict | None:
        if not MEMO_ID.match(memo_id or ""):
            return None
        with self.lab._db() as db:
            db.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
            return db.execute("SELECT * FROM memos WHERE id=?", (memo_id,)).fetchone()

    def update(self, memo_id: str, **fields) -> None:
        fields["updated_at"] = self.now().isoformat()
        with self._lock, self.lab._db() as db:
            db.execute(f"UPDATE memos SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), memo_id])

    def delete(self, memo_id: str) -> bool:
        with self._lock, self.lab._db() as db:
            return db.execute("DELETE FROM memos WHERE id=?", (memo_id,)).rowcount > 0


# --- helpers ------------------------------------------------------------------------------------------

def _state(rec: dict) -> Memo:
    return Memo.model_validate_json(rec["state"])


def _answers(rec: dict) -> Answers:
    return Answers.model_validate_json(rec["answers"])


def parsed_memo(rec: dict) -> ParsedMemo:
    """The memo in the form the Step 6 reviewer and the Markdown parser use."""
    a, sk = _answers(rec), json.loads(rec["skeptic"])
    return ParsedMemo(
        ticker=rec["ticker"], one_liner=rec["thesis"], bear=[c["claim"] for c in sk.get("bear_case", [])],
        weakest_assumption=sk.get("weakest_assumption", ""), reasons=_meaningful(a.reasons),
        target_weight=a.target_weight_pct / 100 if a.target_weight_pct is not None else None,
        responses={k: v for k, v in a.responses.items() if v}, invalidation=_meaningful(a.invalidation),
        invalidation_raw=a.invalidation, review_date=a.review_date, review_focus=a.review_focus)


def content_from(rec: dict, memo: Memo, answers: Answers) -> MemoContent:
    pm = parsed_memo({**rec, "answers": answers.model_dump_json()})
    return memo.content.model_copy(update={
        "user_a": UserA(reasons=pm.reasons, target_weight=pm.target_weight),
        "user_b": [pm.responses.get(f"E{i}", "") for i in range(1, 4)],
        "user_c": pm.invalidation, "user_d": pm.review_date})


def gate_context(rec: dict | None, symbol: str, ctx: Context) -> str | None:
    """Put this memo into the gate's context. Returns a note for the page, or None if the memo does not apply."""
    if rec is None or rec["ticker"] != symbol:
        return None
    memo = _state(rec)
    ctx.memos[symbol] = memo.summary()
    ctx.today = max(ctx.today or date.min, date.today())  # the memo lives in real time, not the demo's price date
    if rec.get("watch_started"):  # watchlist / paper time counts from the first such decision
        started = date.fromisoformat(rec["watch_started"])
        ctx.watchlist[symbol] = WatchEntry(symbol=symbol, mode="paper" if memo.decision == MemoDecision.PAPER else "watch",
                                           started_at=started, review_count=0)
    return memo.status.value


def view(rec: dict, store: MemoStore) -> dict:
    memo, answers = _state(rec), _answers(rec)
    review = json.loads(rec["review"]) if rec.get("review") else None
    missing = missing_for(memo.model_copy(update={"content": content_from(rec, memo, answers)}), S.USER_RESPONDED)
    updated = datetime.fromisoformat(rec["updated_at"])
    return {
        "id": rec["id"], "ticker": rec["ticker"], "lang": rec["lang"], "thesis": rec["thesis"],
        "created_at": rec["created_at"], "updated_at": rec["updated_at"],
        "expires_at": (updated + timedelta(days=store.retention_days)).date().isoformat(),
        "status": memo.status.value, "version": memo.version,
        "decision": memo.decision.value if memo.decision else None,
        "events": [{"from": e.from_status.value, "to": e.to_status.value, "actor": e.actor, "at": e.at.isoformat(),
                    "reason": e.override_reason} for e in memo.events],
        "skeptic": json.loads(rec["skeptic"]), "skeptic_meta": json.loads(rec["skeptic_meta"]),
        "answers": answers.model_dump(mode="json"), "missing": missing,
        "review": review, "review_meta": json.loads(rec["review_meta"]) if rec.get("review_meta") else None,
        "review_unresolved": bool(memo.content.ai_review and not memo.content.ai_review.passed),
        "watch_started": rec.get("watch_started"),
    }


# --- Markdown export (the memo SOP layout; memo_parse.parse_memo reads it back) ------------------------

MD = {
    "zh": {"title": "Investment Memo", "date": "日期", "status": "状态", "disclaimer": "由 Investment Lab 生成。AI 部分已标注；§A–§D 由作者本人填写。不构成投资建议。",
           "s1": "## 1. 一句话投资论点（用户填写）", "s2": "## 2. 业务说明（用户填写）",
           "s2_body": "回答 memo SOP Step 2 的 5 个问题：是什么、靠什么赚钱、主要收入来源、收益来自哪里、最大风险来自哪里。",
           "s3": "## 3. 最新财务数据", "s3_body": "见 Lab 财报快照（SEC XBRL）。", "s4": "## 4. Bull Case（AI 整理，不代表推荐）",
           "s5": "## 5. Bear Case / 反方审查（AI，最强 3 条）", "s6": "## 6. 估值与配置理由 §A（用户填写）",
           "s7": "## 7. 最脆弱的关键假设（AI）", "s8": "## 8. 失效条件 §C（用户填写，至少 3 条）",
           "s8_ai": "AI 建议的可观测指标（仅供参考）：", "s9": "## 9. 待验证问题（需回原始资料）",
           "s10": "## 10. 对反方的回应 §B（用户填写）", "s11": "## 11. 复盘日期 §D（用户填写）", "s12": "## 12. 状态",
           "breaks": "如果成立，失效的假设", "respond": "我的回应：", "where": "查哪里", "reasons": "我考虑配置这个标的，是因为：",
           "target": "目标仓位占净值：", "review": "下次复盘日期：", "focus": "复盘重点：", "evidence": "依据", "quote": "原文"},
    "en": {"title": "Investment Memo", "date": "Date", "status": "Status", "disclaimer": "Generated by Investment Lab. AI sections are labelled; §A-§D were written by the author. Not investment advice.",
           "s1": "## 1. One-line thesis (user)", "s2": "## 2. Business (user)",
           "s2_body": "Answer the five Step 2 questions: what it is, how it makes money, main revenue sources, where returns come from, biggest risk.",
           "s3": "## 3. Latest financials", "s3_body": "See the Lab financial snapshot (SEC XBRL).",
           "s4": "## 4. Bull case (AI, not a recommendation)", "s5": "## 5. Bear case / skeptic review (AI, strongest 3)",
           "s6": "## 6. Valuation and sizing rationale §A (user)", "s7": "## 7. Weakest key assumption (AI)",
           "s8": "## 8. Invalidation conditions §C (user, at least 3)", "s8_ai": "AI-suggested observable metrics (reference only):",
           "s9": "## 9. Questions to verify in primary sources", "s10": "## 10. Responses to the counter-arguments §B (user)",
           "s11": "## 11. Review date §D (user)", "s12": "## 12. Status", "breaks": "Assumption that fails if this holds",
           "respond": "My response:", "where": "Where to check", "reasons": "I am considering this because:",
           "target": "Target weight of net value:", "review": "Next review date:", "focus": "Review focus:",
           "evidence": "evidence", "quote": "Filing text"},
}
TYPE = {"zh": {"fact": "事实", "inference": "推断", "to_verify": "待验证"},
        "en": {"fact": "fact", "inference": "inference", "to_verify": "to verify"}}


def render_markdown(rec: dict) -> str:
    lang = rec["lang"]
    t, ty = MD[lang], TYPE[lang]
    memo, a, sk = _state(rec), _answers(rec), json.loads(rec["skeptic"])
    ev, src = sk.get("evidence", {}), sk.get("sources", {})

    def claim_lines(c: dict, bullet: str) -> list[str]:
        refs = [f"{ev[r]['label']} {ev[r]['period']} = {ev[r]['display']}" for r in c.get("evidence_refs", []) if r in ev]
        out = [f"{bullet}[{ty.get(c['type'], c['type'])}] {c['claim']}" + (f" — {t['evidence']}: {'; '.join(refs)}" if refs else "")]
        for q in c.get("quotes", []):
            s = src.get(q.get("source_id", ""), {})
            out.append(f"   - {t['quote']}：“{q['text']}”" + (f" — [{s.get('document', '')}]({s['url']})" if s.get("url") else ""))
        return out

    L = [f"# {rec['ticker']} {t['title']}", "", f"> {t['date']}: {rec['updated_at'][:10]}",
         f"> {t['status']}: {memo.status.value}" + (f" · {memo.decision.value}" if memo.decision else ""),
         f"> {t['disclaimer']}", "", "---", "", t["s1"], "", "```text", rec["thesis"], "```", "",
         t["s2"], "", t["s2_body"], "", t["s3"], "", t["s3_body"], "", t["s4"], ""]
    for i, c in enumerate(sk.get("bull_case", []), 1):
        L += claim_lines(c, f"{i}. ")
    L += ["", t["s5"], ""]
    for i, c in enumerate(sk.get("bear_case", []), 1):
        first, *rest = claim_lines(c, "")
        L += [f"**E{i}. {first}**", *rest, f"- {t['breaks']}: {c.get('breaks_assumption', '')}", ""]
    tw = f"{a.target_weight_pct:g}%" if a.target_weight_pct is not None else "____%"
    L += [t["s6"], "", "```text", t["reasons"]] + [f"{i}. {r}" for i, r in enumerate(a.reasons or [""], 1)]
    L += [f"{t['target']}{tw}", "```", "", t["s7"], "", sk.get("weakest_assumption", ""), "", t["s8"], "", "```text"]
    L += [f"{i}. {c}" for i, c in enumerate(a.invalidation or ["", "", ""], 1)] + ["```", "", t["s8_ai"], ""]
    for s in sk.get("invalidation_suggestions", []):
        L.append(f"- {s['condition']} — {s['observable_metric']}" + (f"（{s['threshold']}）" if s.get("threshold") else ""))
    L += ["", t["s9"], ""] + [f"- {q['question']}（{t['where']}: {q['where_to_check']}）" for q in sk.get("verify_questions", [])]
    L += ["", t["s10"], "", "```text"] + [f"E{i}: {t['respond']}{a.responses.get(f'E{i}', '')}" for i in range(1, 4)]
    L += ["```", "", t["s11"], "", "```text", f"{t['review']}{a.review_date or ''}", f"{t['focus']}{a.review_focus}", "```", "",
          t["s12"], ""]
    L += [f"- {e.from_status.value} → {e.to_status.value} ({e.actor}, {e.at.date()})" +
          (f": {e.override_reason}" if e.override_reason else "") for e in memo.events]
    md = "\n".join(L) + "\n"
    if rec.get("review"):
        result = ReviewResult(ok=True, review=MemoReview.model_validate(json.loads(rec["review"])))
        md += render_review(result, [], None, lang, today=(json.loads(rec["review_meta"]) or {}).get("at", "")[:10] or None)
    if memo.status == S.FINAL:
        final = next((e for e in reversed(memo.events) if e.to_status == S.FINAL), None)
        md += render_final(memo, final.override_reason if final else None, lang, today=final.at.date() if final else None)
    return md


# --- routes -------------------------------------------------------------------------------------------

def add_memo_routes(r: APIRouter, settings, sk) -> MemoStore | None:
    store = MemoStore(sk.store, retention_days=settings.memo_retention_days, per_ip_daily=settings.memo_per_ip_daily,
                      reviews_per_ip_daily=settings.memo_review_per_ip_daily,
                      max_memos=settings.memo_max) if sk.store is not None else None
    if store is not None:
        store.purge()

    def need_store():
        ok, reason = sk.enabled()
        if not ok or store is None:
            raise HTTPException(503, reason or "The memo workflow is not enabled on this server.")

    def load(memo_id: str) -> dict:
        need_store()
        rec = store.get(memo_id)
        if rec is None:
            raise HTTPException(404, "memo not found (it may have been deleted or expired)")
        return rec

    def pack_for(rec: dict):
        try:
            return lab_pack(sk.load_company(rec["ticker"]), rec["thesis"])
        except HTTPException:
            raise
        except Exception as e:
            log.warning("memo %s: SEC fetch failed: %s", rec["ticker"], e)
            raise HTTPException(503, "SEC data is temporarily unavailable. Please try again in a few minutes.") from e

    @r.post("/memos")
    def create(req: CreateMemo, request: Request):
        need_store()
        store.purge()
        ticker = req.ticker.upper()
        if ticker not in settings.curated:
            raise HTTPException(404, "not in the Lab company list")
        thesis = normalize_thesis(req.thesis)
        hit = sk.store.cached(cache_key(ticker, req.lang, thesis))
        if hit is None:  # the AI part comes only from this server's own skeptic results
            raise HTTPException(409, "Run the AI skeptic on this thesis first.")
        ip_hash = sk.visitor(request)
        if store.created_today(ip_hash) >= store.per_ip_daily:
            raise HTTPException(429, f"Daily limit reached ({store.per_ip_daily} new memos per visitor).")
        if store.total() >= store.max_memos:
            raise HTTPException(503, "The Lab has reached its memo storage limit. Please try again later.")
        try:
            pack = lab_pack(sk.load_company(ticker), thesis)
        except Exception:  # the memo still works without source links
            pack = None
        skeptic = render(hit["output"], pack, req.lang)
        memo = Memo(symbol=ticker, content=MemoContent(one_liner=thesis, skeptic=Skeptic(
            top3=[c["claim"] for c in hit["output"]["bear_case"]], weakest_assumption=hit["output"]["weakest_assumption"])))
        memo = transition(memo, S.RESEARCHING, "user")
        memo = transition(memo, S.SKEPTIC_DONE, "ai")
        now = store.now().isoformat()
        rec = {"id": secrets.token_urlsafe(16), "created_at": now, "updated_at": now, "ip_hash": ip_hash,
               "ticker": ticker, "lang": req.lang, "thesis": thesis, "skeptic": json.dumps(skeptic, ensure_ascii=False),
               "skeptic_meta": json.dumps({"model": hit["model"], "prompt_version": hit["prompt_version"]}),
               "answers": Answers().model_dump_json(), "state": memo.model_dump_json()}
        store.insert(rec)
        return view(store.get(rec["id"]), store)

    @r.get("/memos/{memo_id}")
    def get(memo_id: str):
        return view(load(memo_id), store)

    @r.put("/memos/{memo_id}/answers")
    def save_answers(memo_id: str, answers: Answers):
        rec = load(memo_id)
        memo = _state(rec)
        if memo.status == S.FINAL:
            raise HTTPException(409, "This memo is final. Reopen it as a new version to edit.")
        if memo.status == S.ARCHIVED:
            raise HTTPException(409, "This memo is archived.")
        new_content = content_from(rec, memo, answers)
        changed = answers != _answers(rec)
        memo = memo.model_copy(update={"content": new_content})
        review, review_meta = rec.get("review"), rec.get("review_meta")
        if changed and memo.status in (S.USER_RESPONDED, S.REVIEWED):
            memo = transition(memo, S.SKEPTIC_DONE, "user")  # edited again: the old review no longer applies
            review = review_meta = None
        if memo.status == S.SKEPTIC_DONE and not missing_for(memo, S.USER_RESPONDED):
            memo = transition(memo, S.USER_RESPONDED, "user")
        store.update(memo_id, answers=answers.model_dump_json(), state=memo.model_dump_json(), review=review,
                     review_meta=review_meta)
        return view(store.get(memo_id), store)

    @r.post("/memos/{memo_id}/review")
    def review(memo_id: str, request: Request):
        rec = load(memo_id)
        memo = _state(rec)
        if memo.status == S.REVIEWED and rec.get("review"):
            return {"ok": True, "cached": True, "memo": view(rec, store)}
        if memo.status != S.USER_RESPONDED:
            raise HTTPException(409, "Fill in §A-§D first." if memo.status == S.SKEPTIC_DONE
                                else f"Cannot review a memo in state '{memo.status.value}'.")
        ip_hash = sk.visitor(request)
        if store.reviews_today(ip_hash) >= store.reviews_per_ip_daily:
            raise HTTPException(429, f"Daily limit reached ({store.reviews_per_ip_daily} AI reviews per visitor). "
                                     "Please come back tomorrow.")
        if sk.store.remaining_today() <= 0.01:
            raise HTTPException(503, "Today's AI budget for the Lab is used up. Please try again tomorrow.")
        pm, pack = parsed_memo(rec), pack_for(rec)
        with sk.model_lock:
            result = run_memo_review(pm, pack, sk.provider_factory(), sk.store, language=rec["lang"], surface="lab")
        cost = sum(run.cost_usd for run in result.runs)
        last = result.runs[-1] if result.runs else None
        status = ("ok" if result.ok else last.status if last and last.status in ("budget_blocked", "error")
                  else "invalid")
        store.add_review_request(ip_hash, status, cost)
        if status == "budget_blocked":
            raise HTTPException(503, "Today's AI budget for the Lab is used up. Please try again tomorrow.")
        if status == "error":
            log.warning("memo review %s: model error: %s", rec["ticker"], result.error)
            raise HTTPException(502, "The AI model is unavailable right now. Please try again later.")
        if not result.ok:
            rep = result.report
            return {"ok": False, "attempts": len(result.runs),
                    "checks": {"advice": len(rep.forbidden) if rep else 0,
                               "ungrounded_numbers": len(rep.ungrounded) if rep else 0,
                               "other": (len(rep.errors) + len(rep.echoed)) if rep else 0}}
        rv = result.review
        issues = [*rv.section_a.issues, *[f"{x.counter}: {x.comment}" for x in rv.responses if x.verdict in
                                          ("not_refuted", "off_topic")], *rv.section_c.issues]
        memo = memo.model_copy(update={"content": memo.content.model_copy(
            update={"ai_review": AIReview(passed=not result.unresolved, issues=issues)})})
        memo = transition(memo, S.REVIEWED, "ai")
        store.update(memo_id, state=memo.model_dump_json(), review=rv.model_dump_json(),
                     review_meta=json.dumps({"model": last.model if last else None, "prompt_version": REVIEW_PROMPT_VERSION,
                                             "at": store.now().isoformat(), "attempts": len(result.runs)}))
        return {"ok": True, "cached": False, "memo": view(store.get(memo_id), store)}

    @r.post("/memos/{memo_id}/finalize")
    def finalize(memo_id: str, req: FinalizeRequest):
        rec = load(memo_id)
        memo = _state(rec)
        reason = (req.reason or "").strip() or None
        try:
            if memo.status == S.USER_RESPONDED:  # finalising without the AI review is an override (needs a reason)
                memo = transition(memo, S.REVIEWED, "user", override_reason=reason)
            memo = transition(memo, S.FINAL, "user", decision=MemoDecision(req.decision), override_reason=reason)
        except TransitionError as e:
            raise HTTPException(422, {"message": str(e), "missing": e.missing})
        watch = rec.get("watch_started") or (store.now().date().isoformat()
                                             if req.decision in ("watchlist", "paper") else None)
        store.update(memo_id, state=memo.model_dump_json(), watch_started=watch)
        return view(store.get(memo_id), store)

    @r.post("/memos/{memo_id}/reopen")
    def reopen(memo_id: str):
        rec = load(memo_id)
        memo = _state(rec)
        if memo.status != S.FINAL:
            raise HTTPException(409, "Only a final memo can be reopened.")
        memo = transition(memo, S.RESEARCHING, "user")  # new version; decision and AI review are cleared
        memo = transition(memo, S.SKEPTIC_DONE, "user")
        if not missing_for(memo, S.USER_RESPONDED):
            memo = transition(memo, S.USER_RESPONDED, "user")
        store.update(memo_id, state=memo.model_dump_json(), review=None, review_meta=None)
        return view(store.get(memo_id), store)

    @r.get("/memos/{memo_id}/markdown")
    def markdown(memo_id: str):
        rec = load(memo_id)
        name = f"{rec['ticker']}_memo_{rec['updated_at'][:10]}.md"
        return PlainTextResponse(render_markdown(rec), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @r.delete("/memos/{memo_id}")
    def delete(memo_id: str):
        load(memo_id)
        store.delete(memo_id)
        return {"deleted": True}

    return store
