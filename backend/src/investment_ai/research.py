"""Research + skeptic task (memo SOP Steps 3-4). See DESIGN §10."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from .evidence import EvidencePack
from .ledger import AIRun, BudgetExceeded, Ledger
from .providers import LLMError, Provider, prices_for
from .validate import ResearchSkeptic, ValidationReport, schema_for_prompt, validate_output

PROMPT_VERSION = "research_skeptic_v5"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
LANGUAGES = {"zh": "Simplified Chinese", "en": "English"}


class ResearchResult(BaseModel):
    ok: bool
    output: ResearchSkeptic | None = None
    report: ValidationReport | None = None
    runs: list[AIRun] = Field(default_factory=list)
    error: str | None = None


def system_prompt(language: str) -> str:
    text = (PROMPTS_DIR / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
    return text.replace("{language}", LANGUAGES.get(language, language))


def user_prompt(pack: EvidencePack, thesis: str, language: str = "zh") -> str:
    return (f"Company: {pack.company} ({pack.ticker}, CIK {pack.cik})\n\n"
            f"<thesis>\n{thesis.strip()}\n</thesis>\n\n"
            f"EVIDENCE (SEC XBRL filings; derived = computed from reported figures):\n{pack.to_prompt_table(language)}\n"
            + (f"\n{pack.to_prompt_passages()}\n" if pack.passages else ""))


def run_research_skeptic(pack: EvidencePack, thesis: str, provider: Provider, ledger: Ledger, *,
                         surface: str = "private", language: str = "zh", max_tokens: int = 4096,
                         max_attempts: int = 2, meta: dict | None = None) -> ResearchResult:
    if not thesis.strip():
        raise ValueError("thesis is required (memo SOP Step 1 is written by the user)")
    system = system_prompt(language)
    base_user = user_prompt(pack, thesis, language)
    schema = schema_for_prompt()
    prices = prices_for(provider.name)
    runs: list[AIRun] = []
    user = base_user
    report: ValidationReport | None = None

    for attempt in range(1, max_attempts + 1):
        est = ((len(system) + len(user)) / 3 * prices[0] + max_tokens * prices[1]) / 1e6
        common = dict(surface=surface, task="research_skeptic", provider=provider.name, model=provider.model,
                      prompt_version=PROMPT_VERSION,
                      input_hash=hashlib.sha256((system + user).encode()).hexdigest(),
                      input={"ticker": pack.ticker, "thesis": thesis, "attempt": attempt, "language": language,
                             **(meta or {})})
        try:
            ledger.check(est)
        except BudgetExceeded as e:
            runs.append(ledger.record(AIRun(status="budget_blocked", error=str(e), **common)))
            return ResearchResult(ok=False, runs=runs, error=str(e))
        try:
            res = provider.complete_json(system, user, schema, max_tokens)
        except LLMError as e:
            runs.append(ledger.record(AIRun(status="error", error=str(e), **common)))
            return ResearchResult(ok=False, runs=runs, error=str(e))

        if res.truncated:
            output, report = None, ValidationReport(ok=False, errors=[
                f"answer was cut off at {max_tokens} output tokens; keep every field to one or two sentences"])
        else:
            output, report = validate_output(res.data, pack, user_thesis=thesis)
        runs.append(ledger.record(AIRun(
            status="ok" if report.ok else "invalid", output=res.data, validation=report.model_dump(),
            tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_usd=res.cost_usd(prices),
            latency_ms=res.latency_ms, **{**common, "model": res.model})))
        if report.ok:
            return ResearchResult(ok=True, output=output, report=report, runs=runs)
        user = (base_user + "\n\nYour previous answer failed validation. Fix exactly these problems and "
                "answer again:\n" + report.feedback())

    # Fail closed: an output that never passed validation is not shown.
    return ResearchResult(ok=False, report=report, runs=runs, error="output failed validation")


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
