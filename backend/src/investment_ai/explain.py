"""Quarterly explainer for the public Lab company page (v1).

The same engine, validator and plain-language layer as the AI skeptic, with a different prompt: instead of arguing
against a thesis it explains the latest reported quarter in everyday words (what improved, what weakened, what to
watch next), from the same evidence (XBRL numbers, 10-K / 10-Q paragraphs, the earnings press release, new risk
factors). It is neutral: no advice, no opinion on the stock. Numbers and quotes are checked exactly like the skeptic.
"""

from __future__ import annotations

import re

from investment_core.filing_text import retrieve_diverse

from .evidence import EvidencePack
from .lab_pack import BASE_TERMS, LabCompany
from .ledger import Ledger
from .providers import Provider
from .research import LAB_ATTEMPTS, LAB_MAX_TOKENS, ResearchResult, run_research_skeptic

EXPLAIN_VERSION = "quarter_explainer_v4"
EXPLAIN_PLAIN_VERSION = "plain_summary_explain_v3"
# Fixed and neutral, so the explanation depends on the company and the quarter only.
EXPLAIN_THESIS = "What changed in the latest reported quarter, and what is worth watching next."
EXPLAIN_TERMS = ["revenue increased", "revenue decreased", "operating income", "net income", "margin", "outlook",
                 "results of operations", "compared to", "primarily due to", "guidance", "expect", "cash flow",
                 "capital expenditures"]
EXPLAIN_PASSAGES = 8   # from the latest 10-K and 10-Q
EXPLAIN_RELEASE = 5    # management's own words about the quarter (press release)
EXPLAIN_NEW_RISKS = 2


def explain_pack(company: LabCompany) -> EvidencePack:
    """Numbers plus paragraphs about results and outlook (deterministic for a company)."""
    terms = [*EXPLAIN_TERMS, *company.members, *BASE_TERMS]
    chosen = retrieve_diverse(company.passages, terms, k=EXPLAIN_PASSAGES) if company.passages else []
    chosen += retrieve_diverse(company.release, terms, k=EXPLAIN_RELEASE, per_term=2) if company.release else []
    seen = {p.text[:200] for p in chosen}
    new = [p for p in company.new_risks if p.text[:200] not in seen]
    chosen += retrieve_diverse(new, terms, k=EXPLAIN_NEW_RISKS, per_term=1) if new else []
    return company.pack.model_copy(update={"passages": chosen})


def latest_period(pack: EvidencePack) -> str:
    """The newest period in the evidence, used so a cached explanation is replaced when a new quarter is reported."""
    ends = [str(i.period_end) for i in pack.items if getattr(i, "period_end", None)]
    return max(ends) if ends else ""


_LEVEL = re.compile(r"[0-9０-９%％]|个位数|一位数|两位数|双位数|三位数|single[- ]digit|double[- ]digit|triple[- ]digit", re.IGNORECASE)


def watch_problems(output) -> list[str]:
    """The "what to watch next" items name a metric and a direction, never a level the model made up."""
    out = []
    for i in output.invalidation_suggestions:
        for name, text in (("condition", i.condition), ("observable_metric", i.observable_metric), ("threshold", i.threshold)):
            if text and _LEVEL.search(text):
                out.append(f"invalidation_suggestions: {name} must not contain a number or percentage "
                           f"(found in \"{text[:60]}\"); describe the metric and the direction of change in words only")
    return out


# A record or a first needs more history than the evidence table has (a handful of quarters), so it is allowed
# only when a quote from the filing says it. Verdict adjectives are never the explainer's to give.
_RECORD = re.compile(r"all-time|unprecedented|first time|for the first|historic|highest ever|lowest ever|\brecord\b|"
                     r"历史新高|历史最|创纪录|纪录|史上|有史以来|前所未有|首次|第一次", re.IGNORECASE)
_VERDICT = re.compile(r"robust|impressive|outstanding|stellar|alarming|disappointing|"
                      r"强劲|表现突出|亮眼|惊人|出色|堪忧|令人担忧", re.IGNORECASE)


def claim_problems(output) -> list[str]:
    out = []
    for c in [*output.bull_case, *output.bear_case]:
        if not c.quotes:
            for m in _RECORD.finditer(c.claim):
                out.append(f"claim \"{c.claim[:40]}…\" says \"{m.group(0)}\", but the evidence covers only the latest few "
                           f"quarters: compare only with the periods in the table, and do not claim a record or a first")
        for m in _VERDICT.finditer(c.claim):
            out.append(f"claim \"{c.claim[:40]}…\" uses the verdict word \"{m.group(0)}\": say what moved with a "
                       f"neutral verb (rose, fell, grew faster), not how impressive or worrying it is")
    return out


def check_explanation(output) -> list[str]:
    return [*watch_problems(output), *claim_problems(output)]


def run_explainer(company: LabCompany, provider: Provider, ledger: Ledger, *, language: str = "zh",
                  surface: str = "lab") -> tuple[EvidencePack, ResearchResult]:
    pack = explain_pack(company)
    result = run_research_skeptic(pack, EXPLAIN_THESIS, provider, ledger, surface=surface, language=language,
                                  stance="explain", plain=True, max_attempts=LAB_ATTEMPTS,
                                  max_tokens=LAB_MAX_TOKENS, prompt_version=EXPLAIN_VERSION,
                                  plain_version=EXPLAIN_PLAIN_VERSION, post_check=check_explanation, plain_reasoning=False)
    if result.ok and result.output is not None:
        # Levels the model proposes for "what to watch" are guesses, not facts: only the metric and direction stay.
        result.output.invalidation_suggestions = [i.model_copy(update={"threshold": None})
                                                  for i in result.output.invalidation_suggestions]
    return pack, result
