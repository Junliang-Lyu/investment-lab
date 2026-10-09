"""Structure reader for the 13F reference page (v1).

A 13F gives a table of holdings. Code computes the structure metrics from it (concentration, breadth, how much moved
between two quarters). The model only explains that structure in everyday words and lists the questions a reader must
answer before borrowing anything from it. It never sees a number it may write: it writes placeholders such as {top1}
and the code fills in the real value afterwards, so a number on the page cannot be one the model made up. It also never
proposes limits, allocations or trades: rule ideas name a rule *type* the pre-trade gate already has, in words.

This is not the research skeptic engine: there is no company evidence to cite, so the checks are different (no digits at
all, only known placeholders, known rule codes, no advice, no verdict or motive words). No eval set exists for it yet;
the page says so.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, Field, ValidationError

from investment_core.thirteenf import Portfolio13F, by_issuer, diff, equity_only

from .ledger import AIRun, BudgetExceeded, Ledger
from .providers import LLMError, Provider, prices_for
from .research import LANGUAGES, PROMPTS_DIR
from .validate import forbidden_hits

READER_VERSION = "reference_reader_v1"
READER_ATTEMPTS = 3
READER_MAX_TOKENS = 2500

# Rule types of the pre-trade gate that a reader could borrow an idea for. Only the first two have a value a 13F can
# show; for the others the page says that a 13F cannot show it.
RULE_CODES = ["SINGLE_MAX_WEIGHT_INVESTED", "TOP3_MAX_WEIGHT_INVESTED", "SINGLE_MAX_WEIGHT_NAV", "EXPOSURE_MAX_WEIGHT",
              "SATELLITE_MAX_WEIGHT", "CORE_TARGET_WEIGHT", "MEMO_REQUIRED_ABOVE", "LOSS_REVIEW_TRIGGER",
              "REVIEW_OVERDUE", "MISSING_INVALIDATION"]
RULE_VALUE = {"SINGLE_MAX_WEIGHT_INVESTED": "top1", "TOP3_MAX_WEIGHT_INVESTED": "top3"}

COUNTS = {"structure": (3, 4), "cautions": (2, 3), "rule_ideas": (3, 3), "questions": (3, 4)}
MAX_CHARS = 320


class RuleIdea(BaseModel):
    rule_code: str
    idea: str = Field(max_length=MAX_CHARS)
    question: str = Field(max_length=MAX_CHARS)


class Reading(BaseModel):
    structure: list[str]
    cautions: list[str]
    rule_ideas: list[RuleIdea]
    questions: list[str]


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "structure": {"type": "array", "items": {"type": "string"}, "description": "3 or 4 items"},
        "cautions": {"type": "array", "items": {"type": "string"}, "description": "2 or 3 items"},
        "rule_ideas": {"type": "array", "description": "exactly 3 items, each a different rule_code", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"rule_code": {"type": "string", "enum": RULE_CODES},
                           "idea": {"type": "string"}, "question": {"type": "string"}},
            "required": ["rule_code", "idea", "question"]}},
        "questions": {"type": "array", "items": {"type": "string"}, "description": "3 or 4 items"},
    },
    "required": ["structure", "cautions", "rule_ideas", "questions"],
}

# placeholder -> what it means (the model sees the value and the meaning, and writes only the placeholder)
MEANING = {
    "period": "the quarter end of the latest filing",
    "positions": "number of ALL companies held (share classes of one company counted once); not 'the remaining' ones",
    "top1": "share of the largest holding in the reported equity",
    "top3": "share of the three largest holdings together",
    "top5": "share of the five largest holdings together",
    "top10": "share of the ten largest holdings together",
    "beyond10": "share held outside the ten largest",
    "effective": "effective number of holdings = 1 divided by the sum of squared weights. Use it ONLY in a sentence of the form "
                 "'about {effective} equal-sized holdings would be as concentrated as this portfolio'. It is not a share, "
                 "a duration or a threshold",
    "count1": "number of holdings that are each at least {one_pct} of the reported equity",
    "one_pct": "the one-percent line used by {count1}; write {one_pct} whenever you need that threshold",
    "beyond10_n": "number of companies outside the ten largest (the long tail)",
    "w2": "share of the second largest holding", "w3": "share of the third largest holding",
    "name1": "name of the largest holding", "name2": "name of the second largest", "name3": "name of the third largest",
    "options_n": "option lines left out of the table (a 13F reports options at the value of the underlying shares)",
    "prev_period": "the quarter end of the previous filing",
    "prev_positions": "number of companies in the previous quarter",
    "prev_top1": "largest holding's share in the previous quarter", "prev_top3": "top three together, previous quarter",
    "prev_top10": "top ten together, previous quarter",
    "new_n": "number of companies not held in the previous quarter",
    "exited_n": "number of companies held in the previous quarter and sold out",
    "added_n": "number of companies where the share count rose", "trimmed_n": "number of companies where it fell",
    "new_w": "combined share of the new companies in the latest quarter",
    "exited_w": "combined share the sold-out companies had in the previous quarter",
}


def _p(x: float) -> str:
    return f"{x * 100:.1f}%"


def metrics(portfolios: list[Portfolio13F]) -> dict[str, str]:
    """Display strings keyed by placeholder, from the filed tables. `portfolios` newest first."""
    cur_raw = portfolios[0]
    cur, options = equity_only(cur_raw)
    merged = by_issuer(cur)
    ranked = sorted(merged.holdings, key=lambda h: h.value_usd, reverse=True)
    ws = [merged.weight(h) for h in ranked]
    hhi = sum(w * w for w in ws)
    out = {"period": cur_raw.period.isoformat(), "positions": str(len(ws)), "top1": _p(sum(ws[:1])),
           "top3": _p(sum(ws[:3])), "top5": _p(sum(ws[:5])), "top10": _p(sum(ws[:10])),
           "beyond10": _p(max(0.0, 1 - sum(ws[:10]))), "effective": f"{1 / hhi:.1f}" if hhi else "0",
           "count1": str(sum(1 for w in ws if w >= 0.01)), "one_pct": "1%",
           "beyond10_n": str(max(0, len(ws) - 10))}
    for i in range(3):
        if i < len(ranked):
            if i:
                out[f"w{i + 1}"] = _p(ws[i])
            out[f"name{i + 1}"] = ranked[i].issuer.title() if ranked[i].issuer.isupper() else ranked[i].issuer
    if options:
        out["options_n"] = str(options)
    if len(portfolios) > 1:
        prev, _ = equity_only(portfolios[1])
        pm = by_issuer(prev)
        pw = sorted((pm.weight(h) for h in pm.holdings), reverse=True)
        out.update(prev_period=portfolios[1].period.isoformat(), prev_positions=str(len(pw)), prev_top1=_p(sum(pw[:1])),
                   prev_top3=_p(sum(pw[:3])), prev_top10=_p(sum(pw[:10])))
        ch = diff(pm, merged)  # companies, not share classes, so a class switch is not a sale
        by = {k: [c for c in ch if c.kind == k] for k in ("new", "exited", "increased", "decreased")}
        out.update(new_n=str(len(by["new"])), exited_n=str(len(by["exited"])), added_n=str(len(by["increased"])),
                   trimmed_n=str(len(by["decreased"])), new_w=_p(sum(c.weight_after for c in by["new"])),
                   exited_w=_p(sum(c.weight_before for c in by["exited"])))
    return out


def system_prompt(language: str) -> str:
    text = (PROMPTS_DIR / f"{READER_VERSION}.md").read_text(encoding="utf-8")
    return text.replace("{language}", LANGUAGES.get(language, language))


_BRACE = re.compile(r"\{(\w+)\}")
_SAFE_NAME = re.compile(r"[^\w .,&'’\-]")


SHAPE_RULE = "effective number of holdings below 10: concentrated; below 25: moderately spread; otherwise broadly spread"


def shape(values: dict[str, str]) -> str:
    """A fixed, visible rule (not the model's impression) for how spread out the portfolio is."""
    try:
        eff = float(values["effective"])
    except (KeyError, ValueError):
        return "unknown"
    return "concentrated" if eff < 10 else "moderately spread" if eff < 25 else "broadly spread"


def user_prompt(filer: str, about: str | None, values: dict[str, str]) -> str:
    rows = "\n".join(f"{{{k}}} = {v} -- {MEANING[k]}" for k, v in values.items())
    safe = _SAFE_NAME.sub("", filer)[:80]
    note = f"\nNOTE ABOUT THE INSTITUTION (written by the site, not by the filer):\n{about}\n" if about else ""
    return (f"Institution (name as filed with the SEC): {safe}\n{note}\n"
            f"SHAPE (fixed rule in code: {SHAPE_RULE}). This portfolio: {shape(values)}. Describe concentration only "
            f"in line with this.\n\n"
            f"STRUCTURE METRICS (computed by code from the 13F tables; write the placeholder, never the value):\n{rows}\n")


# Words that give a verdict, a motive or a prediction. A 13F shows what was held on one date, nothing about why.
_VERDICT = re.compile(
    r"excellent|outstanding|impressive|brilliant|smart|wise|skilled|genius|reckless|foolish|robust|"
    r"优秀|出色|明智|聪明|高明|厉害|愚蠢|鲁莽|亮眼", re.IGNORECASE)
# Motive and prediction words are about the FILER, so they are checked in the descriptive texts only (structure,
# cautions, rule ideas), never in a question to the reader ("how long do you plan to hold it"). In a rule idea, words
# like "plan" belong to the reader ("write an exit plan"), so those count only when a filer-like subject owns them.
_MOTIVE = re.compile(
    r"看好|看空|押注|坚信|\bbullish\b|\bbearish\b|\bconvinced\b|\bbet(?:s|ting)? on\b|"
    r"\bwill (?:likely|probably|continue|keep)\b|\bis likely to\b|\bexpect(?:s|ed)? to\b|将会|可能会继续|预计", re.IGNORECASE)
_MOTIVE_OWNED = re.compile(
    r"(?:他们|它们|其|该机构|机构|基金|管理人|\bthey|\bit|\bthe (?:fund|filer|institution|manager|firm))\W{0,3}(?:的)?\s*"
    r"(?:计划|打算|相信|信心|意图|\bplans?\b|\bintends?\b|\bbelieves?\b|\bis confident\b|\bhas confidence\b)", re.IGNORECASE)
PCT = {"top1", "top3", "top5", "top10", "beyond10", "prev_top1", "prev_top3", "prev_top10", "new_w", "exited_w", "one_pct"}
COUNT = {"positions", "count1", "beyond10_n", "options_n", "prev_positions", "new_n", "exited_n", "added_n", "trimmed_n"}
_DURATION = re.compile(r"^\s*(?:weeks?|days?|months?|years?|周|天|个月|年)", re.IGNORECASE)
_UNIT_PCT = re.compile(r"^\s*(?:%|percent|of the reported|占)", re.IGNORECASE)
_UNIT_COUNT = re.compile(r"^\s*(?:companies|positions|holdings|stocks|names|家|只)", re.IGNORECASE)
_REMAINING = re.compile(r"(?:remaining|other|rest of the|rest|其余|剩余|其他|剩下)\W{0,3}$", re.IGNORECASE)
_EQUAL = re.compile(r"equal|same[- ]size|evenly|相等|等权|等大|同样大|一样大|均等|等额|同等", re.IGNORECASE)
_SENT = re.compile(r"[^.!?。！？]+[.!?。！？]?")
_NAME_OK = re.compile(r"13F(?:-HR)?", re.IGNORECASE)  # the name of the filing is not a level
_URL = re.compile(r"https?:|www\.|\.com\b", re.IGNORECASE)
_DIGIT = re.compile(r"[0-9０-９%％]|百分之|个位数|一位数|两位数|双位数|三位数|single[- ]digit|double[- ]digit|triple[- ]digit", re.IGNORECASE)


def _texts(r: Reading) -> list[tuple[str, str]]:
    out = [(f"structure[{i}]", t) for i, t in enumerate(r.structure)]
    out += [(f"cautions[{i}]", t) for i, t in enumerate(r.cautions)]
    out += [(f"questions[{i}]", t) for i, t in enumerate(r.questions)]
    for i, x in enumerate(r.rule_ideas):
        out += [(f"rule_ideas[{i}].idea", x.idea), (f"rule_ideas[{i}].question", x.question)]
    return out


def render_text(text: str, values: dict[str, str]) -> str:
    return _BRACE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


_HIGH_CONC = re.compile(
    r"高度集中|集中度(?:很|相当|极|非常)?高|极度集中|相当集中|十分集中|非常集中|高集中|highly concentrated|extremely concentrated|"
    r"very concentrated|quite concentrated|heavily concentrated|concentrated portfolio", re.IGNORECASE)
_HIGH_SPREAD = re.compile(r"高度分散|非常分散|well[- ]diversified|highly diversified|very diversified", re.IGNORECASE)
_RISE = re.compile(r"升|增|提高|扩大|上涨|rose|rise|increas|grew|grow|climb|higher|\bup\b", re.IGNORECASE)
_FALL = re.compile(r"降|减|缩小|下滑|下跌|收窄|fell|fall|decreas|declin|shrank|shrink|narrow|lower|\bdown\b", re.IGNORECASE)
_CLAUSE = re.compile(r"[^,，;；.。!?！？]+")
PAIRS = [("prev_top1", "top1"), ("prev_top3", "top3"), ("prev_top10", "top10"), ("prev_positions", "positions")]


def _num(x: str) -> float:
    return float(x.rstrip("%"))


def shape_problems(where: str, text: str, values: dict[str, str]) -> list[str]:
    out = []
    kind = shape(values)
    if kind != "concentrated" and _HIGH_CONC.search(text):
        out.append(f"{where}: do not call this portfolio concentrated: by the fixed rule it is {kind} "
                   f"({SHAPE_RULE}). Describe it as the numbers show")
    if kind == "concentrated" and _HIGH_SPREAD.search(text):
        out.append(f"{where}: do not call this portfolio diversified: by the fixed rule it is concentrated")
    return out


def direction_problems(where: str, text: str, values: dict[str, str]) -> list[str]:
    """"From {prev_top10} up to {top10}" when the number actually fell: a clause naming both values of a pair must
    describe the direction they really moved."""
    out = []
    for clause in _CLAUSE.findall(text):
        for before, after in PAIRS:
            if f"{{{before}}}" in clause and f"{{{after}}}" in clause and before in values and after in values:
                d = _num(values[after]) - _num(values[before])
                rise, fall = bool(_RISE.search(clause)), bool(_FALL.search(clause))
                if (d < 0 and rise and not fall) or (d > 0 and fall and not rise) or (d == 0 and (rise or fall)):
                    word = "fell" if d < 0 else "rose" if d > 0 else "did not change"
                    out.append(f"{where}: in \"{clause.strip()[:50]}\" the value {word} ({values[before]} to {values[after]}); "
                               f"the wording says the opposite. Check the direction of every comparison")
    return out


def placement_problems(where: str, text: str) -> list[str]:
    """A placeholder must stand for exactly what its meaning says: a share is not a duration, a count is not a share,
    "all companies" is not "the remaining ones", and the effective number belongs only in an "equal-sized" sentence."""
    out = []
    for m in _BRACE.finditer(text):
        name, after, before = m.group(1), text[m.end():m.end() + 16], text[max(0, m.start() - 16):m.start()]
        if name in PCT or name in COUNT or name == "effective":
            if _DURATION.search(after):
                out.append(f"{where}: {{{name}}} is a number from the table, not a length of time. Do not state how many "
                           f"weeks or days after the quarter a 13F is filed; say \"some weeks after the quarter ends\" in words")
        if name in COUNT or name == "effective":
            if _UNIT_PCT.search(after):
                out.append(f"{where}: {{{name}}} is not a share; do not follow it with % or \"of the reported holdings\"")
        if name in PCT and _UNIT_COUNT.search(after):
            out.append(f"{where}: {{{name}}} is a share, not a count of companies")
        if name == "positions" and _REMAINING.search(before):
            out.append(f"{where}: {{positions}} is ALL companies, not the remaining ones; use {{beyond10_n}} for the companies "
                       f"outside the ten largest")
    for sent in _SENT.findall(text):
        if "{effective}" in sent and not _EQUAL.search(sent):
            out.append(f"{where}: {{effective}} may only be used in a sentence saying about that many equal-sized holdings "
                       f"would be as concentrated as this portfolio")
    return out


def problems(r: Reading, values: dict[str, str]) -> list[str]:
    out: list[str] = []
    for field, (lo, hi) in COUNTS.items():
        n = len(getattr(r, field))
        if not lo <= n <= hi:
            out.append(f"{field}: give {lo if lo == hi else f'{lo} or {hi}'} items, not {n}")
    codes = [x.rule_code for x in r.rule_ideas]
    if len(set(codes)) != len(codes):
        out.append("rule_ideas: each item needs a different rule_code")
    for x in r.rule_ideas:
        if x.rule_code not in RULE_CODES:
            out.append(f"rule_ideas: unknown rule_code {x.rule_code}")
    for where, text in _texts(r):
        if not text.strip():
            out.append(f"{where}: empty")
            continue
        if len(text) > MAX_CHARS:
            out.append(f"{where}: longer than {MAX_CHARS} characters; use one or two sentences")
        stripped = _NAME_OK.sub("", _BRACE.sub("", text))
        if _DIGIT.search(stripped):
            out.append(f"{where}: contains a digit or percentage (\"{stripped[:50]}\"); write the placeholder, such as "
                       f"{{top1}}, instead of any number, and never invent a level")
        for name in _BRACE.findall(text):
            if name not in values:
                out.append(f"{where}: placeholder {{{name}}} does not exist (use only: {', '.join(values)})")
        if _URL.search(text):
            out.append(f"{where}: no links or web addresses")
        shown = render_text(text, values)
        for hit in forbidden_hits(shown) + forbidden_hits(text):
            out.append(f"{where}: \"{hit}\" reads as advice or sizing. Do not say what share of a portfolio anything "
                       f"should be; describe the filer's structure only (say \"of the reported holdings\", "
                       f"\"占申报股票市值\", not \"of the portfolio\" or \"仓位占比\")")
        for m in _VERDICT.finditer(text):
            out.append(f"{where}: \"{m.group(0)}\" is a verdict. Describe the structure; do not grade the investor")
        if not where.startswith("questions") and not where.endswith(".question"):
            for m in [*_MOTIVE.finditer(text), *_MOTIVE_OWNED.finditer(text)]:
                out.append(f"{where}: \"{m.group(0)}\" is a motive or a prediction. A 13F shows what was held on one date, "
                           f"not why or what comes next")
        out.extend(placement_problems(where, text))
        out.extend(shape_problems(where, text, values))
        out.extend(direction_problems(where, text, values))
    return sorted(set(out))


def render(r: Reading, values: dict[str, str]) -> dict:
    """What the page shows: the model's words with the real values filled in, and the filer's own value for each
    rule type that a 13F can show (none for the others)."""
    return {
        "structure": [render_text(t, values) for t in r.structure],
        "cautions": [render_text(t, values) for t in r.cautions],
        "rule_ideas": [{"rule_code": x.rule_code, "idea": render_text(x.idea, values),
                        "question": render_text(x.question, values),
                        "their_value": values.get(RULE_VALUE.get(x.rule_code, ""))} for x in r.rule_ideas],
        "questions": [render_text(t, values) for t in r.questions],
    }


class ReaderResult(BaseModel):
    ok: bool
    output: Reading | None = None
    runs: list[AIRun] = Field(default_factory=list)
    error: str | None = None
    problems: list[str] = Field(default_factory=list)


def run_reader(filer: str, about: str | None, values: dict[str, str], provider: Provider, ledger: Ledger, *,
               language: str = "zh", surface: str = "lab", max_attempts: int = READER_ATTEMPTS,
               max_tokens: int = READER_MAX_TOKENS) -> ReaderResult:
    system = system_prompt(language)
    base_user = user_prompt(filer, about, values)
    prices = prices_for(provider.name, provider.model)
    runs: list[AIRun] = []
    user, last_problems = base_user, []
    for attempt in range(1, max_attempts + 1):
        est = ((len(system) + len(user)) / 3 * prices[0] + max_tokens * prices[1]) / 1e6
        common = dict(surface=surface, task="reference_reader", provider=provider.name, model=provider.model,
                      prompt_version=READER_VERSION, input_hash=hashlib.sha256((system + user).encode()).hexdigest(),
                      input={"filer": filer, "attempt": attempt, "language": language})
        try:
            ledger.check(est)
        except BudgetExceeded as e:
            runs.append(ledger.record(AIRun(status="budget_blocked", error=str(e), **common)))
            return ReaderResult(ok=False, runs=runs, error=str(e))
        try:
            res = provider.complete_json(system, user, SCHEMA, max_tokens)
        except LLMError as e:
            runs.append(ledger.record(AIRun(status="error", error=str(e), **common)))
            return ReaderResult(ok=False, runs=runs, error=str(e))
        reading, last_problems = None, []
        if res.truncated:
            last_problems = [f"answer was cut off at {max_tokens} output tokens; keep every item to one sentence"]
        else:
            try:
                reading = Reading.model_validate(res.data)
                last_problems = problems(reading, values)
            except ValidationError as e:
                last_problems = [f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}" for x in e.errors()][:6]
        ok = reading is not None and not last_problems
        runs.append(ledger.record(AIRun(
            status="ok" if ok else "invalid", output=res.data, validation={"ok": ok, "problems": last_problems},
            tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_usd=res.cost_usd(prices), latency_ms=res.latency_ms,
            raw_text=res.text[:2000] or None, **{**common, "model": res.model})))
        if ok:
            return ReaderResult(ok=True, output=reading, runs=runs)
        user = base_user + "\n\nYour previous answer failed these checks. Fix exactly these and answer again:\n" + \
            "\n".join(f"- {p}" for p in last_problems)
    return ReaderResult(ok=False, runs=runs, error="output failed validation", problems=last_problems)
