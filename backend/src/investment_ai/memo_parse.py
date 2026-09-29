"""Read the user's sections back out of a memo draft (the layout from memo_draft.py)."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

PLACEHOLDER = re.compile(r"^(?:暂时?没想好|待定|无|tbd|n/?a|-|—)$", re.IGNORECASE)


class ParsedMemo(BaseModel):
    ticker: str
    stance: str = "long"   # "long" (bullish) or "short" (bearish); a memo line "方向 / Direction: 看空 / bearish"
    one_liner: str = ""
    bear: list[str] = Field(default_factory=list)          # E1..E3 claim text
    weakest_assumption: str = ""
    reasons: list[str] = Field(default_factory=list)       # §A
    target_weight: float | None = None                    # §A, 0.25 = 25%
    responses: dict[str, str] = Field(default_factory=dict)  # §B, "E1" -> text
    invalidation: list[str] = Field(default_factory=list)  # §C, meaningful items only
    invalidation_raw: list[str] = Field(default_factory=list)
    review_date: date | None = None                        # §D
    review_focus: str = ""


def _sections(md: str) -> dict[int, str]:
    parts = re.split(r"^## (\d+)\.", md, flags=re.MULTILINE)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        out[int(parts[i])] = parts[i + 1].split("\n", 1)[1] if "\n" in parts[i + 1] else ""  # drop heading text
    return out


def _code_block(section: str) -> list[str]:
    m = re.search(r"```(?:text)?\n(.*?)```", section, flags=re.DOTALL)
    return [ln.rstrip() for ln in m.group(1).splitlines()] if m else []


def _numbered(lines: list[str]) -> list[str]:
    items = []
    for ln in lines:
        m = re.match(r"^\s*\d+\s*[.、．)]\s*(.*)$", ln)
        if m:
            items.append(m.group(1).strip())
    return items


def _meaningful(items: list[str]) -> list[str]:
    return [x for x in items if x and not PLACEHOLDER.match(x.strip()) and len(x.strip()) >= 4]


def _parse_date(text: str, today: date) -> date | None:
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s*[./月-]\s*(\d{1,2})", text)
    if m:
        d = date(today.year, int(m.group(1)), int(m.group(2)))
        return d if d >= today else date(today.year + 1, d.month, d.day)
    return None


def parse_memo(md: str, today: date | None = None) -> ParsedMemo:
    today = today or date.today()
    title = re.search(r"^#\s+([A-Z.\-]+)\s", md, flags=re.MULTILINE)
    sec = _sections(md)
    pm = ParsedMemo(ticker=title.group(1) if title else "")

    pm.one_liner = "\n".join(_code_block(sec.get(1, ""))).strip()
    if re.search(r"(?:方向|Direction)\s*[:：]\s*(?:看空|bearish|short)", sec.get(1, ""), re.I):
        pm.stance = "short"
    pm.bear = [re.sub(r"^\[[^\]]+\]\s*", "", m.strip()) for m in re.findall(r"\*\*E\d\.\s*(.*?)\*\*", sec.get(5, ""))]
    body7 = [ln.strip() for ln in sec.get(7, "").splitlines() if ln.strip()]
    pm.weakest_assumption = body7[0] if body7 else ""

    a = _code_block(sec.get(6, ""))
    pm.reasons = _meaningful(_numbered(a))
    for ln in a:
        if "%" in ln and re.search(r"\d", ln):
            n = re.search(r"(\d+(?:\.\d+)?)\s*_*\s*%", ln)
            if n:
                pm.target_weight = float(n.group(1)) / 100

    for ln in _code_block(sec.get(10, "")):
        m = re.match(r"^\s*(E\d)\s*[:：]\s*(.*)$", ln)
        if m:
            text = re.sub(r"^(我的回应|My response)\s*[:：]\s*", "", m.group(2).strip())
            pm.responses[m.group(1)] = text

    c = _numbered(_code_block(sec.get(8, "")))
    pm.invalidation_raw = c
    pm.invalidation = _meaningful(c)

    for ln in _code_block(sec.get(11, "")):
        if re.match(r"^(下次复盘日期|Next review date)", ln):
            pm.review_date = _parse_date(ln.split("：")[-1].split(":")[-1], today)
        elif re.match(r"^(复盘重点|Review focus)", ln):
            pm.review_focus = re.split(r"[:：]", ln, maxsplit=1)[-1].strip()
    return pm
