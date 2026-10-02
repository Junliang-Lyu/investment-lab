"""Focus angles a visitor can ask the skeptic to look from (DESIGN §11.6).

Angles are free text of a few words. The page offers common ones as presets, but any short phrase works, because
people look at a company from different sides. The text is data for the model, never an instruction: it is
restricted to letters, digits and a few separators, kept short, and checked for echoed code words like the thesis.
"""

from __future__ import annotations

import re

MAX_ANGLES = 4
MAX_LEN = 30
_ALLOWED = re.compile(r"^[\w][\w \-/&,，、·]{0,%d}$" % (MAX_LEN - 1))

# preset: (zh label, en label, English terms used to find matching filing paragraphs)
PRESETS: dict[str, tuple[str, str, list[str]]] = {
    "technology": ("技术与产品", "Technology and product", ["technology", "product", "innovation", "research and development"]),
    "competition": ("竞争格局", "Competition", ["competition", "competitors", "market share", "pricing"]),
    "cash": ("现金流与资本开支", "Cash flow and capex", ["cash flow", "capital expenditures", "free cash flow"]),
    "customers": ("客户与需求", "Customers and demand", ["customers", "demand", "backlog", "concentration"]),
    "management": ("管理层与治理", "Management and governance", ["management", "strategy", "executive officers"]),
    "regulation": ("监管与法律", "Regulation and legal", ["regulatory", "legal proceedings", "antitrust", "compliance"]),
    "supply": ("供应链与成本", "Supply chain and costs", ["supply chain", "suppliers", "manufacturing", "costs"]),
}


class AngleError(ValueError):
    pass


def normalize_angles(raw: list[str] | None) -> list[str]:
    """Clean, de-duplicate and validate the visitor's angles. Raises AngleError on anything unusual."""
    out: list[str] = []
    for a in raw or []:
        t = re.sub(r"\s+", " ", str(a)).strip()
        if not t:
            continue
        if not _ALLOWED.match(t):
            raise AngleError(f"an angle may only contain letters, digits and spaces, up to {MAX_LEN} characters: {t[:40]}")
        if t.lower() not in {x.lower() for x in out}:
            out.append(t)
    if len(out) > MAX_ANGLES:
        raise AngleError(f"at most {MAX_ANGLES} angles")
    return out


def retrieval_terms(angle: str) -> list[str]:
    """English terms to find filing paragraphs for an angle (presets are known; other text is used as it is)."""
    low = angle.lower()
    for zh, en, terms in PRESETS.values():
        if angle == zh or low == en.lower():
            return list(terms)
    return [angle]
