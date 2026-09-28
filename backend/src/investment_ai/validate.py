"""Output validation. See docs/DESIGN.md §10.4.

Every model output passes three checks before anyone sees it:
1. It matches the schema.
2. Every number in the text is grounded. In a bull/bear claim, each number
   must match one of that claim's own evidence_refs (within the precision it
   was written with). Anywhere else it must be copied literally from the
   evidence pack (e.g. "$119.80B", "24.1%"), which stops coarse numbers like
   "30%" from passing by coincidence. Suggested thresholds, numbers from the
   user's own thesis, years, dates, fiscal labels and small counts are exempt.
3. It contains no trading advice (buy/sell/hold, target prices, sizing).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from investment_core.filing_text import quote_found

from .evidence import EvidencePack

ClaimType = Literal["fact", "inference", "to_verify"]


class Quote(BaseModel):
    source_id: str
    text: str


class Claim(BaseModel):
    claim: str
    type: ClaimType
    evidence_refs: list[str] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)  # verbatim text from filing passages
    why_it_matters: str | None = None  # interpretation; always an inference


class BearClaim(Claim):
    breaks_assumption: str


class Invalidation(BaseModel):
    condition: str
    observable_metric: str
    threshold: str | None = None


class VerifyQuestion(BaseModel):
    question: str
    where_to_check: str


class ResearchSkeptic(BaseModel):
    thesis_restated: str
    bull_case: list[Claim] = Field(min_length=2, max_length=4)
    bear_case: list[BearClaim] = Field(min_length=3, max_length=3)
    weakest_assumption: str
    invalidation_suggestions: list[Invalidation] = Field(min_length=3, max_length=5)
    verify_questions: list[VerifyQuestion] = Field(min_length=2, max_length=6)

    @field_validator("thesis_restated", "weakest_assumption")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v


def schema_for_prompt() -> dict:
    return ResearchSkeptic.model_json_schema()


# --- forbidden content ------------------------------------------------------

FORBIDDEN = [
    r"\b(?:strong\s+)?(?:buy|sell|hold)\s+(?:rating|recommendation|signal)\b",
    r"\b(?:we|i)\s+(?:recommend|suggest|advise)\b",
    r"\b(?:you|investors?)\s+should\s+(?:buy|sell|hold|add|trim|accumulate|exit)\b",
    r"\b(?:recommend(?:ed)?|advis(?:e|ed))\s+(?:buying|selling|holding)\b",
    r"\b(?:price|share[-\s]price)\s+target\b",
    r"\btarget\s+price\b",
    r"\bfair\s+value\s+(?:of|is)\s+\$",
    r"\b(?:good|great|ideal)\s+(?:time|entry(?:\s+point)?)\s+to\s+(?:buy|sell)\b",
    r"\ballocate\s+\d",
    r"建议\s*(?:买入|卖出|持有|加仓|减仓|清仓|建仓)",
    r"(?:推荐|值得)\s*(?:买入|购买|入手|持有)",
    r"目标价",
    r"(?:应该|可以)\s*(?:买入|卖出|加仓|减仓|清仓)",
]
_FORBIDDEN_RE = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN]


def forbidden_hits(text: str) -> list[str]:
    return [m.group(0) for r in _FORBIDDEN_RE for m in r.finditer(text)]


# --- number grounding ---------------------------------------------------------

_SCALE = {"trillion": 1e12, "tn": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9,
          "million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3, "亿": 1e8, "万": 1e4}
_NUM = re.compile(
    r"(?P<dollar>\$)?\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?"
    r"(?P<unit>%|trillion|billion|million|thousand|tn|bn|mn|亿|万|倍|×|x(?![A-Za-z])|[TBMK](?![A-Za-z]))?",
    re.IGNORECASE,
)
_STRIP = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),               # ISO dates
    re.compile(r"\bFY\s?\d{2,4}(?:\s?Q[1-4])?\b", re.I),  # fiscal labels
    re.compile(r"\b(?:Q[1-4]|H[12])\s?(?:FY)?\s?\d{2,4}\b", re.I),
    re.compile(r"\b(?:Q[1-4]|H[12])\b", re.I),
    re.compile(r"\d{4}\s?年(?:第?[一二三四1-4]季度)?"),
    re.compile(r"\b10-[KQ]\b|\b13F\b|\b8-K\b", re.I),
]


class NumberMention(BaseModel):
    text: str
    value: float
    kind: Literal["usd", "ratio", "plain", "multiple"]
    tolerance: float


_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s?[-–~～至到]\s?(\d+(?:\.\d+)?)\s?(%|倍)")


def extract_numbers(text: str) -> list[NumberMention]:
    for r in _STRIP:
        text = r.sub(" ", text)
    text = _RANGE.sub(r"\1\3 - \2\3", text)  # "18-24%" -> "18% - 24%"
    out = []
    for m in _NUM.finditer(text):
        raw, unit, dollar = m.group("num"), (m.group("unit") or ""), m.group("dollar")
        n = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        step = 10 ** -decimals
        u = unit.lower()
        if u in ("倍", "×", "x"):
            # Multiples ("4倍", "25x") are never in the evidence pack; the model must not compute them.
            out.append(NumberMention(text=m.group(0).strip(), value=n, kind="multiple", tolerance=0))
        elif u == "%":
            out.append(NumberMention(text=m.group(0).strip(), value=n / 100, kind="ratio",
                                     tolerance=(0.5 * step + 0.05) / 100))
        elif u in _SCALE:
            s = _SCALE[u]
            out.append(NumberMention(text=m.group(0).strip(), value=n * s, kind="usd", tolerance=0.5 * step * s + 0.005 * n * s))
        elif dollar:
            out.append(NumberMention(text=m.group(0).strip(), value=n, kind="usd", tolerance=0.5 * step + 0.005 * n))
        else:
            if n <= 12 and decimals == 0:
                continue  # small counts ("three risks", "2 quarters")
            if decimals == 0 and 1990 <= n <= 2100:
                continue  # bare years
            out.append(NumberMention(text=m.group(0).strip(), value=n, kind="plain", tolerance=0.5 * step))
    return out


def matches_item(num: NumberMention, item) -> bool:
    v = abs(item.value)
    if num.kind == "ratio" and item.unit == "ratio":
        return abs(num.value - v) <= num.tolerance
    if num.kind == "usd" and item.unit == "USD":
        return abs(num.value - v) <= num.tolerance
    return False


def _norm(text: str) -> str:
    return text.replace(" ", "").replace("$", "").replace("-", "").lower()


def is_literal(num: NumberMention, pack: EvidencePack) -> bool:
    displays = {_norm(i.display) for i in pack.items}
    return _norm(num.text) in displays


# --- entry point --------------------------------------------------------------

class ValidationReport(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    ungrounded: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    bad_refs: list[str] = Field(default_factory=list)
    mislabeled: list[str] = Field(default_factory=list)
    bad_quotes: list[str] = Field(default_factory=list)

    def feedback(self) -> str:
        parts = []
        if self.errors:
            parts.append("Schema errors: " + "; ".join(self.errors))
        if self.ungrounded:
            parts.append("These numbers are not in the evidence pack; remove them or use the exact evidence "
                         "value: " + ", ".join(self.ungrounded))
        if self.forbidden:
            parts.append("Remove trading advice: " + ", ".join(self.forbidden))
        if self.bad_refs:
            parts.append("Unknown or missing evidence refs: " + ", ".join(self.bad_refs))
        if self.bad_quotes:
            parts.append("These quotes are not verbatim text from the cited passage (copy the exact words, 5-60 words): "
                         + "; ".join(self.bad_quotes))
        if self.mislabeled:
            parts.append("Fact claims may only restate evidence. Move interpretation to why_it_matters and do not "
                         "attribute totals to AI, cloud, search or segments in a fact claim: " + "; ".join(self.mislabeled))
        return "\n".join(parts)


def _free_texts(obj: ResearchSkeptic) -> list[str]:
    """Statements not tied to one claim's refs (reasoning text).

    A number here is grounded if it is copied literally from the evidence pack,
    or matches (within the precision it was written with) an item that some
    claim in this output cites. Limiting the tolerant match to cited items keeps
    rounded summaries like "18-24%" valid without letting a random "30%" pass.

    Invalidation suggestions and verify questions are exempt from grounding:
    their numbers are proposed thresholds or hypotheticals ("margin below 30%"),
    not claims about the company. They are still checked for advice.
    """
    t = [obj.thesis_restated, obj.weakest_assumption]
    t += [c.breaks_assumption for c in obj.bear_case]
    t += [c.why_it_matters for c in [*obj.bull_case, *obj.bear_case] if c.why_it_matters]
    return t


# A "fact" claim restates evidence. Interpretation belongs in why_it_matters,
# and the evidence has company totals only, so no segment attribution.
INTERPRETATION = [r"表明", r"说明", r"意味着", r"证明", r"反映出?", r"导致", r"因为", r"驱动", r"得益于", r"拖累",
                  r"indicat\w*", r"suggest\w*", r"shows?\s+that", r"means?\s+that", r"impl(?:y|ies)",
                  r"driven\s+by", r"due\s+to", r"because", r"reflect\w*"]
SEGMENTS = [r"(?<![A-Za-z])AI(?![A-Za-z])", r"人工智能", r"云", r"cloud", r"搜索", r"search", r"YouTube",
            r"广告", r"advertis\w*", r"segment\w*", r"业务线"]
_INTERP_RE = [re.compile(p, re.IGNORECASE) for p in INTERPRETATION]
_SEG_RE = [re.compile(p, re.IGNORECASE) for p in SEGMENTS]


_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9&]*(?:\s+[A-Z][A-Za-z0-9&]*)*\b")
GENERIC_NAMES = {"FY", "Q1", "Q2", "Q3", "Q4", "YoY", "QoQ", "SEC", "GAAP", "USD", "EPS", "B", "M", "K", "T"}


_CJK = re.compile(r"[\u4e00-\u9fff]")
_SENTENCE_START = re.compile(r"(?:^|[.!?;:。；：！？]\s*)$")


def _metric_words() -> set[str]:
    from .evidence import LABELS
    words = {w for v in LABELS["en"].values() for w in re.findall(r"[A-Za-z]+", v)}
    return {w.lower() for w in words} | {"capex", "fcf", "yoy", "qoq"}


def unsupported_names(claim: str, allowed: set[str]) -> list[str]:
    """Latin-script proper names (Waymo, TPU, Gemini...) in a fact claim that its evidence does not mention.

    In English text an ordinary capitalised word at the start of a sentence is not a name, unless it is an
    acronym (TPU) or CamelCase (YouTube). In Chinese text every capitalised Latin word counts.
    """
    allowed_l = {a.lower() for a in allowed} | _metric_words()
    cjk = bool(_CJK.search(claim))
    out = []
    for m in _PROPER.finditer(claim):
        words = m.group(0).split()
        if not cjk and _SENTENCE_START.search(claim[:m.start()]):
            first = words[0]
            if not (first.isupper() and len(first) > 1) and not re.search(r".[A-Z]", first):
                words = words[1:]
        words = [w for w in words if w not in GENERIC_NAMES and not re.fullmatch(r"FY\d+|Q\d", w)]
        if words and not all(w.lower() in allowed_l for w in words):
            out.append(" ".join(words))
    return out


def fact_claim_problems(claim: str) -> list[str]:
    hits = [m.group(0) for r in _INTERP_RE for m in r.finditer(claim)]
    hits += [m.group(0) for r in _SEG_RE for m in r.finditer(claim)]
    return hits


def validate_output(raw: dict, pack: EvidencePack, user_thesis: str = "") -> tuple[ResearchSkeptic | None, ValidationReport]:
    try:
        obj = ResearchSkeptic.model_validate(raw)
    except ValidationError as e:
        errs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        return None, ValidationReport(ok=False, errors=errs)

    # Numbers the user wrote in their own thesis are theirs; restating them is allowed.
    thesis_numbers = {round(n.value, 6) for n in extract_numbers(user_thesis)}
    by_id = {i.fact_id: i for i in pack.items}
    ungrounded, forbidden = [], []
    mislabeled, bad_quotes = [], []
    for c in [*obj.bull_case, *obj.bear_case]:
        forbidden += forbidden_hits(c.claim) + forbidden_hits(c.why_it_matters or "")
        refs = [by_id[r] for r in c.evidence_refs if r in by_id]
        good_quotes = []
        for q in c.quotes:
            p = pack.passage(q.source_id)
            words = len(q.text.split())
            if p is None or not quote_found(q.text, p.text) or not (5 <= words <= 60):
                bad_quotes.append(f"[{q.source_id}] {q.text[:60]}")
            else:
                good_quotes.append(q.text)
        if c.type == "fact":
            # Segment words are fine when the claim cites segment numbers or a filing quote.
            has_segment_support = bool(good_quotes) or any(r.member for r in refs)
            probs = [h for h in fact_claim_problems(c.claim)
                     if not (has_segment_support and any(re.search(p, h, re.IGNORECASE) for p in SEGMENTS))]
            if not good_quotes:
                allowed = set(re.findall(r"[A-Za-z0-9&]+", f"{pack.ticker} {pack.company}"))
                for r in refs:
                    allowed |= set(re.findall(r"[A-Za-z0-9&]+", r.member or ""))
                probs += unsupported_names(c.claim, allowed)
            if probs:
                mislabeled.append(f"{c.claim[:40]}… ({', '.join(sorted(set(probs)))})")
        quote_numbers = [n for qt in good_quotes for n in extract_numbers(qt)]
        for n in extract_numbers(c.claim):
            if round(n.value, 6) in thesis_numbers:
                continue
            in_quote = any(n.kind == m.kind and abs(n.value - m.value) <= max(n.tolerance, m.tolerance)
                           for m in quote_numbers)
            if not in_quote and not any(matches_item(n, item) for item in refs):
                ungrounded.append(n.text)
    cited = [by_id[r] for c in [*obj.bull_case, *obj.bear_case] for r in c.evidence_refs if r in by_id]
    for text in _free_texts(obj):
        forbidden += forbidden_hits(text)
        for n in extract_numbers(text):
            if round(n.value, 6) in thesis_numbers or is_literal(n, pack):
                continue
            if any(matches_item(n, item) for item in cited):
                continue
            ungrounded.append(n.text)
    for i in obj.invalidation_suggestions:
        forbidden += forbidden_hits(f"{i.condition} {i.observable_metric} {i.threshold or ''}")
    for q in obj.verify_questions:
        forbidden += forbidden_hits(q.question)

    ids = pack.ids()
    bad_refs = []
    for c in [*obj.bull_case, *obj.bear_case]:
        bad_refs += [r for r in c.evidence_refs if r not in ids]
        if c.type == "fact" and not c.evidence_refs and not c.quotes:
            bad_refs.append(f"fact without evidence_refs or quotes: {c.claim[:60]}")

    report = ValidationReport(ok=not (ungrounded or forbidden or bad_refs or mislabeled or bad_quotes),
                              ungrounded=sorted(set(ungrounded)), forbidden=sorted(set(forbidden)),
                              bad_refs=bad_refs, mislabeled=mislabeled, bad_quotes=bad_quotes)
    return obj, report
