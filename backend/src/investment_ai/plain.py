"""Plain-language layer for the AI skeptic (DESIGN §11.6, v15).

A second, small model call turns the already validated counter-arguments and supporting points into one
everyday sentence each. It runs only after the main answer passed validation, sees only that validated text (never
the user's thesis or the filings), and its output is checked: no figures at all, no advice, no echoed code words.
It is optional: a sentence that does not pass is left out and the page shows the original wording, so this layer
can never make an answer fail.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .evidence import EvidencePack
from .ledger import AIRun, BudgetExceeded, Ledger
from .providers import LLMError, Provider, prices_for
from .validate import ResearchSkeptic, plain_text_problems, thesis_markers

PLAIN_PROMPT_VERSION = "plain_summary_v1"
PLAIN_MAX_TOKENS = 1200
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
LANGUAGES = {"zh": "Simplified Chinese", "en": "English"}

SCHEMA = {
    "type": "object",
    "properties": {"summaries": {"type": "array", "description": "one item per point id", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "plain_summary": {"type": "string"}},
        "required": ["id", "plain_summary"]}}},
    "required": ["summaries"],
}


_NEW_CLAIM = re.compile(
    r"first time|for the first|record|all-time|unprecedented|highest|lowest|\bever\b|"
    r"surg(?:e|ed|es|ing)\b|soar(?:ed|s|ing)?\b|skyrocket|plunge[ds]?\b|massive|enormous|"
    r"首次|第一次|创纪录|纪录|历史新高|历史最|前所未有|史上|有史以来|暴增|飙升|激增|暴跌|暴涨", re.IGNORECASE)


def new_claim_words(text: str, claim) -> list[str]:
    """Words that state a new fact (a first, a record) or a verdict (surged, massive) are allowed only if the point itself says them."""
    source = " ".join(filter(None, [claim.claim, claim.why_it_matters or ""])).lower()
    return [f"adds a claim the point does not make: \"{m.group(0)}\"" for m in _NEW_CLAIM.finditer(text.lower())
            if m.group(0) not in source]


def points(output: ResearchSkeptic) -> dict[str, tuple[str, object]]:
    """id -> (kind, claim object): c1..c3 counter-arguments, s1..s3 supporting points."""
    out = {f"c{i}": ("counter", c) for i, c in enumerate(output.bear_case, 1)}
    out.update({f"s{i}": ("support", c) for i, c in enumerate(output.bull_case, 1)})
    return out


def user_prompt(output: ResearchSkeptic, ids: set[str] | None = None, reasoning: bool = True) -> str:
    items = []
    for pid, (kind, c) in points(output).items():
        if ids is not None and pid not in ids:
            continue
        item = {"id": pid, "kind": kind, "claim": c.claim}
        if c.angle:
            item["angle"] = c.angle
        if reasoning and c.why_it_matters:
            item["why_it_matters"] = c.why_it_matters
        if reasoning and kind == "counter" and getattr(c, "breaks_assumption", None):
            item["breaks_assumption"] = c.breaks_assumption
        items.append(item)
    return "Points:\n" + json.dumps(items, ensure_ascii=False, indent=1)


def add_plain_summaries(output: ResearchSkeptic, provider: Provider, ledger: Ledger, *, thesis: str = "",
                        pack: EvidencePack | None = None, surface: str = "private", language: str = "zh",
                        max_attempts: int = 2, meta: dict | None = None,
                        prompt_version: str = PLAIN_PROMPT_VERSION,
                        reasoning: bool = True) -> tuple[ResearchSkeptic, list[AIRun]]:
    """Fill plain_summary on the claims where the model's sentence passes the checks. Never raises."""
    system = (PROMPTS_DIR / f"{prompt_version}.md").read_text(encoding="utf-8").replace(
        "{language}", LANGUAGES.get(language, language))
    pts = points(output)
    markers = [m.lower() for m in thesis_markers(thesis, pack)] if thesis else []
    prices = prices_for(provider.name)
    runs: list[AIRun] = []
    done: dict[str, str] = {}
    problems: dict[str, list[str]] = {}
    for attempt in range(1, max_attempts + 1):
        todo = set(pts) - set(done)
        if not todo:
            break
        user = user_prompt(output, todo, reasoning)
        if problems:
            user += "\n\nYour previous sentences failed these checks; write them again without the problem:\n" + \
                "\n".join(f"{pid}: {', '.join(p)}" for pid, p in sorted(problems.items()) if pid in todo)
        est = ((len(system) + len(user)) / 3 * prices[0] + PLAIN_MAX_TOKENS * prices[1]) / 1e6
        common = dict(surface=surface, task="plain_summary", provider=provider.name, model=provider.model,
                      prompt_version=prompt_version,
                      input_hash=hashlib.sha256((system + user).encode()).hexdigest(),
                      input={"plain_attempt": attempt, "language": language, **(meta or {})})
        try:
            ledger.check(est)
        except BudgetExceeded as e:
            runs.append(ledger.record(AIRun(status="budget_blocked", error=str(e), **common)))
            break
        try:
            res = provider.complete_json(system, user, SCHEMA, PLAIN_MAX_TOKENS)
        except LLMError as e:
            runs.append(ledger.record(AIRun(status="error", error=str(e), **common)))
            break
        got = res.data.get("summaries") if isinstance(res.data, dict) and not res.truncated else None
        problems = {}
        for item in got if isinstance(got, list) else []:
            if not isinstance(item, dict):
                continue
            pid, text = str(item.get("id", "")).strip(), item.get("plain_summary")
            if pid not in todo or not isinstance(text, str):
                continue
            probs = plain_text_problems(text)
            probs += new_claim_words(text, pts[pid][1])
            if any(m and m in text.lower() for m in markers):
                probs.append("repeats a code word from the thesis")
            if probs:
                problems[pid] = probs
            else:
                done[pid] = text.strip()
        for pid in todo - set(done):
            problems.setdefault(pid, ["missing"])
        runs.append(ledger.record(AIRun(
            status="ok" if len(done) == len(pts) else "invalid", output=res.data,
            validation={"problems": problems}, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
            cost_usd=res.cost_usd(prices), latency_ms=res.latency_ms, **{**common, "model": res.model})))
    if not done:
        return output, runs
    updated = output.model_copy(deep=True)
    for pid, (_, c) in points(updated).items():
        if pid in done:
            c.plain_summary = done[pid]
    return updated, runs
