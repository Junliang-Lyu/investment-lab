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

EXPLAIN_VERSION = "quarter_explainer_v3"
EXPLAIN_PLAIN_VERSION = "plain_summary_explain_v2"
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


_LEVEL = re.compile(r"[0-9０-９%％]")


def watch_problems(output) -> list[str]:
    """The "what to watch next" items name a metric and a direction, never a level the model made up."""
    out = []
    for i in output.invalidation_suggestions:
        for name, text in (("condition", i.condition), ("observable_metric", i.observable_metric), ("threshold", i.threshold)):
            if text and _LEVEL.search(text):
                out.append(f"invalidation_suggestions: {name} must not contain a number or percentage "
                           f"(found in \"{text[:60]}\"); describe the metric and the direction of change in words only")
    return out


def run_explainer(company: LabCompany, provider: Provider, ledger: Ledger, *, language: str = "zh",
                  surface: str = "lab") -> tuple[EvidencePack, ResearchResult]:
    pack = explain_pack(company)
    result = run_research_skeptic(pack, EXPLAIN_THESIS, provider, ledger, surface=surface, language=language,
                                  stance="explain", plain=True, max_attempts=LAB_ATTEMPTS,
                                  max_tokens=LAB_MAX_TOKENS, prompt_version=EXPLAIN_VERSION,
                                  plain_version=EXPLAIN_PLAIN_VERSION, post_check=watch_problems)
    if result.ok and result.output is not None:
        # Levels the model proposes for "what to watch" are guesses, not facts: only the metric and direction stay.
        result.output.invalidation_suggestions = [i.model_copy(update={"threshold": None})
                                                  for i in result.output.invalidation_suggestions]
    return pack, result
