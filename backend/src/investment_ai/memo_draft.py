"""Render a memo draft in the memo SOP layout (same sections as the v0 memos).

The financial table is deterministic (straight from the evidence pack). AI
sections are labelled. The user's own sections (§A-§D) are left blank on
purpose: the SOP says AI never fills them.
"""

from __future__ import annotations

from datetime import date

from investment_core.financials import CompanyFinancials

from .evidence import EvidencePack, fmt_ratio, fmt_usd, label
from .ledger import AIRun
from .validate import ResearchSkeptic

TYPE_LABEL = {
    "zh": {"fact": "事实", "inference": "推断", "to_verify": "待验证"},
    "en": {"fact": "fact", "inference": "inference", "to_verify": "to verify"},
}
T = {
    "zh": {
        "title": "Investment Memo 草稿", "status": "草稿（AI 已完成 Step 3–4；§A–§D 待你填写）",
        "s1": "## 1. 一句话投资论点（用户填写）", "s2": "## 2. 业务说明（用户填写，AI 未提供）",
        "s2_body": "回答 memo SOP Step 2 的 5 个问题：是什么、靠什么赚钱、主要收入来源、收益来自哪里、最大风险来自哪里。",
        "s3": "## 3. 最新财务数据（来源：SEC XBRL，自动生成）", "s4": "## 4. Bull Case（AI 整理，不代表推荐）",
        "s5": "## 5. Bear Case / 反方审查（AI，最强 3 条）", "s6": "## 6. 估值与配置理由 §A（用户填写）",
        "s7": "## 7. 最脆弱的关键假设（AI）", "s8": "## 8. 失效条件 §C（用户填写，至少 3 条）",
        "s8_ai": "AI 建议（仅供参考，请用你自己的话写）：", "s9": "## 9. 待验证问题（需回原始资料）",
        "s10": "## 10. 对反方的回应 §B（用户填写）", "s11": "## 11. 复盘日期 §D（用户填写）",
        "s12": "## 12. 状态", "appendix": "## 附录：证据来源与 AI 调用记录",
        "breaks": "如果成立，失效的假设", "respond": "我的回应：", "where": "查哪里",
        "reasons": "我考虑配置这个标的，是因为：", "target": "目标仓位占净值：____%",
        "review": "下次复盘日期：", "focus": "复盘重点：",
        "checklist": ["[x] Step 1 一句话论点", "[ ] Step 2 业务说明", "[x] Step 3 证据整理（AI）",
                      "[x] Step 4 反方审查（AI）", "[ ] Step 5 §A–§D（用户）", "[ ] Step 6 AI 审查你的回应",
                      "[ ] Step 7 定稿结论：watchlist / paper / 可走实盘前检查清单"],
        "disclaimer": "本草稿由系统根据公开财报自动整理，不构成投资建议。",
    },
    "en": {
        "title": "Investment Memo Draft", "status": "Draft (AI finished Steps 3-4; §A-§D are yours to write)",
        "s1": "## 1. One-line thesis (user)", "s2": "## 2. Business (user; not provided by AI)",
        "s2_body": "Answer the five Step 2 questions: what it is, how it makes money, main revenue sources, where returns come from, biggest risk.",
        "s3": "## 3. Latest financials (SEC XBRL, generated)", "s4": "## 4. Bull case (AI, not a recommendation)",
        "s5": "## 5. Bear case / skeptic review (AI, strongest 3)", "s6": "## 6. Valuation and sizing rationale §A (user)",
        "s7": "## 7. Weakest key assumption (AI)", "s8": "## 8. Invalidation conditions §C (user, at least 3)",
        "s8_ai": "AI suggestions (reference only; write your own):", "s9": "## 9. Questions to verify in primary sources",
        "s10": "## 10. Responses to the counter-arguments §B (user)", "s11": "## 11. Review date §D (user)",
        "s12": "## 12. Status", "appendix": "## Appendix: evidence and AI run record",
        "breaks": "Assumption that fails if this holds", "respond": "My response:", "where": "Where to check",
        "reasons": "I am considering this because:", "target": "Target weight of net value: ____%",
        "review": "Next review date:", "focus": "Review focus:",
        "checklist": ["[x] Step 1 thesis", "[ ] Step 2 business", "[x] Step 3 evidence (AI)",
                      "[x] Step 4 skeptic review (AI)", "[ ] Step 5 §A-§D (user)", "[ ] Step 6 AI review of your answers",
                      "[ ] Step 7 decision: watchlist / paper / eligible for pre-trade gate"],
        "disclaimer": "Generated from public filings. Not investment advice.",
    },
}

TABLE_METRICS = [("revenue", "Revenue"), ("gross_margin", "Gross margin"), ("operating_income", "Operating income"),
                 ("operating_margin", "Operating margin"), ("net_income", "Net income"), ("cfo", "Operating cash flow"),
                 ("capex", "Capex"), ("fcf", "Free cash flow"), ("cash_and_investments", "Cash + short-term investments"),
                 ("total_debt", "Total debt"), ("net_cash", "Net cash")]


def _refs(refs: list[str], pack: EvidencePack, language: str = "zh") -> str:
    by_id = {i.fact_id: i for i in pack.items}
    shown = [f"{label(by_id[r].metric, language, by_id[r].member)} {by_id[r].fiscal_label or by_id[r].period_end} = {by_id[r].display}"
             for r in refs if r in by_id]
    word = "依据" if language == "zh" else "evidence"
    return f" — {word}: {'; '.join(shown)}" if shown else ""


def _quote_lines(c, pack: EvidencePack, language: str, indent: str) -> list[str]:
    word = "原文" if language == "zh" else "Filing text"
    out = []
    for q in c.quotes:
        p = pack.passage(q.source_id)
        link = f" — [{q.source_id.split(':', 1)[-1]}]({p.url})" if p and p.url else f" — {q.source_id}"
        out.append(f"{indent}- {word}：“{q.text}”{link}")
    return out


def render_memo(ticker: str, thesis: str, fin: CompanyFinancials, pack: EvidencePack, result: ResearchSkeptic,
                run: AIRun | None = None, language: str = "zh", today: date | None = None, table_quarters: int = 4) -> str:
    t, labels = T[language], TYPE_LABEL[language]
    today = today or date.today()
    L = [f"# {ticker.upper()} {t['title']}", "",
         f"> 日期 / Date: {today}", f"> 公司 / Company: {fin.name} (CIK {fin.cik})", f"> 状态 / Status: {t['status']}",
         f"> {t['disclaimer']}", "", "---", "",
         t["s1"], "", "```text", thesis.strip(), "```", "", t["s2"], "", t["s2_body"], "", "1.", "2.", "3.", "4.", "5.", "",
         t["s3"], ""]

    rows = fin.quarters[-table_quarters:]
    L.append("| Metric | " + " | ".join(q.fiscal_label or str(q.end) for q in rows) + " |")
    L.append("|---|" + "---:|" * len(rows))
    for key, name in TABLE_METRICS:
        cells = []
        for q in rows:
            m = q.metrics.get(key)
            cells.append("-" if m is None else (fmt_ratio(m.value) if m.unit == "ratio" else fmt_usd(m.value))
                         + ("*" if m is not None and m.derived and m.unit != "ratio" and key != "fcf" else ""))
        L.append(f"| {name} | " + " | ".join(cells) + " |")
    L += ["", "\\* computed from reported figures (year-to-date differencing, revenue − cost of revenue, or sums of "
             "balance-sheet lines); formulas in the appendix.", ""]

    L += [t["s4"], ""]
    for i, c in enumerate(result.bull_case, 1):
        L.append(f"{i}. [{labels[c.type]}] {c.claim}{_refs(c.evidence_refs, pack, language)}")
        L += _quote_lines(c, pack, language, "   ")
        if c.why_it_matters:
            L.append(f"   - [{labels['inference']}] {c.why_it_matters}")
    L += ["", t["s5"], ""]
    for i, c in enumerate(result.bear_case, 1):
        L.append(f"**E{i}. [{labels[c.type]}] {c.claim}**{_refs(c.evidence_refs, pack, language)}")
        L += _quote_lines(c, pack, language, "")
        if c.why_it_matters:
            L.append(f"- [{labels['inference']}] {c.why_it_matters}")
        L += [f"- {t['breaks']}: {c.breaks_assumption}", ""]
    L += [t["s6"], "", "```text", t["reasons"], "1.", "2.", "3.", t["target"], "```", "",
          t["s7"], "", result.weakest_assumption, "",
          t["s8"], "", "```text", "1.", "2.", "3.", "```", "", t["s8_ai"], ""]
    for s in result.invalidation_suggestions:
        thr = f"（{s.threshold}）" if s.threshold else ""
        L.append(f"- {s.condition} — {s.observable_metric}{thr}")
    L += ["", t["s9"], ""]
    for q in result.verify_questions:
        L.append(f"- {q.question}（{t['where']}: {q.where_to_check}）")
    L += ["", t["s10"], "", "```text"] + [f"E{i}: {t['respond']}" for i in range(1, 4)] + ["```", "",
          t["s11"], "", "```text", t["review"], t["focus"], "```", "", t["s12"], ""]
    L += [f"- {c}" for c in t["checklist"]]
    L += ["", "---", "", t["appendix"], ""]
    cited = {r for c in [*result.bull_case, *result.bear_case] for r in c.evidence_refs}
    for item in pack.items:
        if item.fact_id in cited:
            extra = f"; {item.note}" if item.note else ""
            src = f" — [{'filing'}]({item.source})" if item.source else ""
            L.append(f"- `{item.fact_id}` {item.display}{extra}{src}")
    if run:
        L += ["", f"AI run: `{run.id}` · {run.provider}/{run.model} · prompt `{run.prompt_version}` · "
                  f"tokens {run.tokens_in}+{run.tokens_out} · ${run.cost_usd:.4f}"]
    return "\n".join(L) + "\n"
