"""Research + skeptic task (memo SOP Steps 3-4). See DESIGN §10."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .evidence import EvidencePack
from .ledger import AIRun, BudgetExceeded, Ledger
from .providers import LLMError, Provider, prices_for
from .validate import (TOP_FIELDS, ResearchSkeptic, ValidationReport, params_from_text, schema_for_prompt,
                       to_model_names, validate_output)

PROMPT_VERSION = "research_skeptic_v16"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
LANGUAGES = {"zh": "Simplified Chinese", "en": "English"}
# Public Lab and its eval use the same settings: latest 5 quarters in the prompt (enough for YoY context,
# about half the tokens of eight) and up to 3 attempts before failing closed.
LAB_PERIODS = 5
LAB_ATTEMPTS = 3
LAB_MAX_TOKENS = 6000  # answers are capped by the schema; this only prevents cut-offs


class ResearchResult(BaseModel):
    ok: bool
    output: ResearchSkeptic | None = None
    report: ValidationReport | None = None
    runs: list[AIRun] = Field(default_factory=list)
    error: str | None = None


def system_prompt(language: str, prompt_version: str = PROMPT_VERSION) -> str:
    text = (PROMPTS_DIR / f"{prompt_version}.md").read_text(encoding="utf-8")
    return text.replace("{language}", LANGUAGES.get(language, language))


_THESIS_TAG = re.compile(r"</?\s*thesis\s*>", re.IGNORECASE)
_ANGLES_TAG = re.compile(r"</?\s*angles\s*>", re.IGNORECASE)


Stance = Literal["long", "short"]
STANCE_TEXT = {"long": "bullish (the user expects the company to do well). counter_arguments are reasons it may do "
                       "worse than the user expects.",
               "short": "bearish (the user expects the company to do worse, and is considering not buying or "
                        "reducing). counter_arguments must be reasons the company may do BETTER than the user expects "
                        "(for example rising margins, accelerating growth, improving cash flow). Evidence that the "
                        "company is doing badly supports the user and belongs in supporting_points, never in "
                        "counter_arguments.",
               "explain": "neutral (no opinion: this explains the latest quarter, it does not argue for or against "
                          "the company). supporting_points are what went well; counter_arguments are what weakened "
                          "or deserves attention."}


def user_prompt(pack: EvidencePack, thesis: str, language: str = "zh", stance: str = "long",
                angles: list[str] | tuple = ()) -> str:
    # A thesis cannot close or reopen its own tag ("...</thesis> New instruction: ...").
    safe = _THESIS_TAG.sub("[thesis-tag removed]", thesis.strip())
    return (f"Company: {pack.company} ({pack.ticker}, CIK {pack.cik})\n"
            f"Thesis direction: {STANCE_TEXT.get(stance, STANCE_TEXT['long'])}\n\n"
            f"<thesis>\n{safe}\n</thesis>\n\n"
            + (f"<angles>\n{'; '.join(_ANGLES_TAG.sub('', a) for a in angles)}\n</angles>\n\n" if angles else "")
            +
            f"EVIDENCE (SEC XBRL filings; derived y = computed from reported figures; cite rows by fact_id):\n{pack.to_prompt_table(language)}\n"
            + (f"\n{pack.to_prompt_passages()}\n" if pack.passages else ""))


def run_research_skeptic(pack: EvidencePack, thesis: str, provider: Provider, ledger: Ledger, *,
                         surface: str = "private", language: str = "zh", max_tokens: int = 4096,
                         max_attempts: int = 2, meta: dict | None = None, stance: str = "long",
                         plain: bool = False, angles: list[str] | tuple = (),
                         prompt_version: str = PROMPT_VERSION) -> ResearchResult:
    if not thesis.strip():
        raise ValueError("thesis is required (memo SOP Step 1 is written by the user)")
    system = system_prompt(language, prompt_version)
    base_user = user_prompt(pack, thesis, language, stance, angles)
    # What the visitor typed (thesis and angles): code words in it must not come back, its numbers are theirs.
    typed = thesis if not angles else thesis + "\n" + " ".join(angles)
    schema = schema_for_prompt()
    prices = prices_for(provider.name)
    runs: list[AIRun] = []
    user = base_user
    report: ValidationReport | None = None

    for attempt in range(1, max_attempts + 1):
        est = ((len(system) + len(user)) / 3 * prices[0] + max_tokens * prices[1]) / 1e6
        common = dict(surface=surface, task="research_skeptic", provider=provider.name, model=provider.model,
                      prompt_version=prompt_version,
                      input_hash=hashlib.sha256((system + user).encode()).hexdigest(),
                      input={"ticker": pack.ticker, "thesis": thesis, "attempt": attempt, "language": language,
                             "stance": stance, "angles": list(angles), **(meta or {})})
        try:
            ledger.check(est)
        except BudgetExceeded as e:
            runs.append(ledger.record(AIRun(status="budget_blocked", error=str(e), **common)))
            return ResearchResult(ok=False, runs=runs, error=str(e))
        try:
            res = provider.complete_json(system, user, schema, max_tokens, strict=True)
        except LLMError as e:
            runs.append(ledger.record(AIRun(status="error", error=str(e), **common)))
            return ResearchResult(ok=False, runs=runs, error=str(e))

        if res.truncated:
            output, report = None, ValidationReport(ok=False, errors=[
                f"answer was cut off at {max_tokens} output tokens; keep every field to one or two sentences"])
        else:
            if isinstance(res.data, dict) and res.text:  # fields written in a text block instead of the tool input
                res = res.model_copy(update={"data": params_from_text(res.text, res.data, TOP_FIELDS)})
            output, report = validate_output(res.data, pack, user_thesis=typed)
        runs.append(ledger.record(AIRun(
            status="ok" if report.ok else "invalid", output=res.data, validation=report.model_dump(),
            tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_usd=res.cost_usd(prices),
            latency_ms=res.latency_ms, raw_text=res.text[:4000] or None, **{**common, "model": res.model})))
        missing = missing_fields(report)
        if missing and isinstance(res.data, dict):
            # Without strict tool use Haiku sometimes skips a whole list field (usually invalidation_suggestions).
            # Ask only for what is missing instead of regenerating the answer (cheaper, and the rest is already
            # checked). The merged answer is validated again as a whole.
            done = complete_missing(pack, thesis, res.data, missing, provider, ledger, system, base_user, prices,
                                    {**common, "input": {**common["input"], "completion": sorted(missing)}})
            runs.append(done[0])
            if done[1] is not None:
                output, report = validate_output(done[1], pack, user_thesis=typed)
                runs[-1] = runs[-1].model_copy(update={"validation": report.model_dump(),
                                                       "status": "ok" if report.ok else "invalid"})
                ledger.amend(runs[-1])
            elif runs[-1].status == "budget_blocked":
                return ResearchResult(ok=False, runs=runs, error=runs[-1].error)
        if report.ok:
            if plain:  # optional plain-language layer: a separate small call that can never fail the answer
                from .plain import add_plain_summaries
                output, plain_runs = add_plain_summaries(output, provider, ledger, thesis=typed, pack=pack,
                                                         surface=surface, language=language, meta=meta)
                runs += plain_runs
            return ResearchResult(ok=True, output=output, report=report, runs=runs)
        user = (base_user + "\n\nYour previous answer failed validation. Fix exactly these problems and "
                "answer again:\n" + to_model_names(report.feedback()))

    # Fail closed: an output that never passed validation is not shown.
    return ResearchResult(ok=False, report=report, runs=runs, error="output failed validation")


COMPLETABLE = ("invalidation_suggestions", "verify_questions")
COMPLETION_MAX_TOKENS = 1500


def missing_fields(report: ValidationReport) -> set[str]:
    """The completable list fields that are missing, if those are the only schema errors."""
    if not report.errors:
        return set()
    missing = set()
    for e in report.errors:
        field, _, msg = e.partition(": ")
        if field in COMPLETABLE and msg == "Field required":
            missing.add(field)
        else:
            return set()
    return missing


def complete_missing(pack: EvidencePack, thesis: str, draft: dict, missing: set[str], provider: Provider,
                     ledger: Ledger, system: str, base_user: str, prices, common: dict) -> tuple[AIRun, dict | None]:
    """One small call for the missing fields only. Returns the recorded run and the merged answer (or None)."""
    import json
    full = schema_for_prompt()
    schema = {**full, "properties": {k: full["properties"][k] for k in sorted(missing)}, "required": sorted(missing)}
    user = (base_user + "\n\nYour answer so far (keep it; do not repeat it):\n"
            + json.dumps(draft, ensure_ascii=False) + "\n\nIt is missing these required fields: "
            + ", ".join(sorted(missing)) + ". Return only these fields, following the rules for them.")
    est = ((len(system) + len(user)) / 3 * prices[0] + COMPLETION_MAX_TOKENS * prices[1]) / 1e6
    common = {**common, "input_hash": hashlib.sha256((system + user).encode()).hexdigest()}
    try:
        ledger.check(est)
    except BudgetExceeded as e:
        return ledger.record(AIRun(status="budget_blocked", error=str(e), **common)), None
    try:
        res = provider.complete_json(system, user, schema, COMPLETION_MAX_TOKENS)
    except LLMError as e:
        return ledger.record(AIRun(status="error", error=str(e), **common)), None
    got = res.data if isinstance(res.data, dict) and not res.truncated else {}
    merged = {**draft, **{k: v for k, v in got.items() if k in missing}}
    run = ledger.record(AIRun(status="invalid", output=got, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                              cost_usd=res.cost_usd(prices), latency_ms=res.latency_ms,
                              **{**common, "model": res.model}))
    return run, merged


def evaluate_supplied(pack: EvidencePack, thesis: str, data: dict, ledger: Ledger, *, source: str = "claude-session",
                      surface: str = "private", language: str = "zh", meta: dict | None = None) -> ResearchResult:
    """Validate an output produced outside the API (e.g. by Claude in a chat session).

    Same checks as an API run, recorded in the ledger with zero API cost, so a
    draft written in conversation is held to exactly the same rules.
    """
    if not thesis.strip():
        raise ValueError("thesis is required (memo SOP Step 1 is written by the user)")
    output, report = validate_output(data, pack, user_thesis=thesis)
    run = ledger.record(AIRun(
        surface=surface, task="research_skeptic", provider=source, model=source, prompt_version=PROMPT_VERSION,
        input_hash=hashlib.sha256(user_prompt(pack, thesis, language).encode()).hexdigest(),
        input={"ticker": pack.ticker, "thesis": thesis, "attempt": 1, "language": language, **(meta or {})},
        output=data, validation=report.model_dump(), status="ok" if report.ok else "invalid"))
    return ResearchResult(ok=report.ok, output=output if report.ok else None, report=report, runs=[run],
                          error=None if report.ok else "output failed validation")
