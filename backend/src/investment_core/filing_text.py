"""Narrative text from 10-K / 10-Q HTML: sections, paragraphs, retrieval, quote checks. Pure, no IO.

See DESIGN §9.1. Tables are skipped (numbers come from XBRL); hidden inline-XBRL
headers are skipped. Each paragraph gets a stable id: <accession>:<item>:<n>.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from html.parser import HTMLParser

from pydantic import BaseModel

SEP = r"\s*[\.:\-—–]?\s*"  # "Item 1. Business", "Item 1—Business", "Item 1: Business"
ITEMS = {
    "item1": r"item\s*1" + SEP + r"business",
    "item1a": r"item\s*1a" + SEP + r"risk\s*factors",
    "item7": r"item\s*7" + SEP + r"management[’'`s]*\s*discussion",
    "item2_10q": r"item\s*2" + SEP + r"management[’'`s]*\s*discussion",
}
# The next section starts at a heading line with punctuation after the number ("Item 1A. Risk Factors").
# Bare running page headers ("Item 1" at the top of every page, as in Microsoft's 10-K) do not end a section.
NEXT = r"\n\s*item\s*\d+[a-c]?\s*[\.:\-—–]\s*[a-z]"
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table"}


class Passage(BaseModel):
    source_id: str
    item: str
    text: str
    url: str | None = None


class _Text(HTMLParser):
    """Visible text. Tables are dropped (financial data), except short tables that hold a section heading
    ("Item 1A." | "Risk Factors" in two cells, as in Amazon's 10-K); those become one line."""

    VOID = ("br", "img", "hr", "meta", "link", "input")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.stack: list[bool] = []
        self.tables: list[dict] = []  # open tables: {"cells": n, "parts": [...]}

    def _out(self) -> list[str]:
        return self.tables[-1]["parts"] if self.tables else self.parts

    def handle_starttag(self, tag, attrs):
        style = dict(attrs).get("style", "") or ""
        hidden = tag in ("script", "style", "ix:header") or "display:none" in style.replace(" ", "").lower()
        if tag == "table":
            self.tables.append({"cells": 0, "parts": []})
        elif tag in ("td", "th") and self.tables:
            self.tables[-1]["cells"] += 1
            self._out().append(" ")
        if tag not in self.VOID:
            self.stack.append(hidden)
            self.skip += hidden
        if tag in BLOCK and tag != "table":
            self._out().append("\n")

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack:
            self.skip -= self.stack.pop()
        if tag == "table" and self.tables:
            t = self.tables.pop()
            text = re.sub(r"\s+", " ", "".join(t["parts"])).strip()
            if len(text) <= 120 and re.match(r"(?i)(?:part\s+[iv]+\s*)?item\s*\d+[a-c]?\b", text):
                self._out().append("\n" + text + "\n")
            return
        if tag in BLOCK:
            self._out().append("\n")

    def handle_data(self, data):
        if not self.skip:
            self._out().append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def split_sections(text: str) -> dict[str, str]:
    """The longest candidate wins, so the table of contents is ignored."""
    out = {}
    for key, pat in ITEMS.items():
        best = ""
        for m in re.finditer(r"(?im)^\s*" + pat, text):
            rest = text[m.end():]
            nxt = re.search(r"(?i)" + NEXT, rest)
            body = rest[: nxt.start()] if nxt else rest
            if len(body) > len(best):
                best = body
        if best:
            out[key] = best.strip()
    return out


def paragraphs(section: str, min_words: int = 25) -> list[str]:
    out = []
    for block in re.split(r"\n\s*\n|\n", section):
        b = block.strip()
        if len(b.split()) >= min_words:
            out.append(b)
    return out


def build_passages(html: str, accession: str, url: str | None = None,
                   items: tuple[str, ...] = ("item1", "item1a", "item7", "item2_10q")) -> list[Passage]:
    secs = split_sections(html_to_text(html))
    out = []
    for item in items:
        for n, para in enumerate(paragraphs(secs.get(item, "")), 1):
            out.append(Passage(source_id=f"{accession}:{item}:{n}", item=item, text=para, url=url))
    return out


_WORD = re.compile(r"[a-z0-9]+")
STOP = set("the a an and or of to in for on with by as is are was were be been this that these those our we us it its "
           "from at which such may can could would will not no other than more also any".split())


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def retrieve(passages: list[Passage], query: list[str], k: int = 12, k1: float = 1.5, b: float = 0.75) -> list[Passage]:
    """BM25 over paragraphs. Query terms are English keywords (filings are in English)."""
    if not passages:
        return []
    docs = [_tokens(p.text) for p in passages]
    avg = sum(len(d) for d in docs) / len(docs)
    df = Counter(t for d in docs for t in set(d))
    q = [t for term in query for t in _tokens(term)]
    scores = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append((s, i))
    top = [i for s, i in sorted(scores, key=lambda x: (-x[0], x[1]))[:k] if s > 0]  # ties: document order
    return [passages[i] for i in top]  # best first


def retrieve_diverse(passages: list[Passage], terms: list[str], k: int = 14, per_term: int = 2) -> list[Passage]:
    """Take the best passages for each term in order, so one frequent term cannot crowd out the rest.
    Near-duplicate paragraphs (10-K and 10-Q often repeat text) are kept once."""
    chosen: list[Passage] = []
    seen_text: set[str] = set()
    for term in terms:
        taken = 0
        for p in retrieve(passages, [term], k=len(passages)) if passages else []:
            key = normalize(p.text)[:200]
            if key in seen_text:
                continue
            chosen.append(p)
            seen_text.add(key)
            taken += 1
            if taken >= per_term or len(chosen) >= k:
                break
        if len(chosen) >= k:
            break
    return chosen


def normalize(text: str) -> str:
    t = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", t).strip().lower()


def quote_found(quote: str, passage_text: str) -> bool:
    q = normalize(quote).strip(" .\"'")
    return bool(q) and q in normalize(passage_text)
