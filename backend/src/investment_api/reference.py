"""Reference portfolios: what well-known institutions report holding in their 13F filings (SEC), summarised for
learning how others structure a portfolio. Nothing here is a recommendation and no model is involved: the numbers are
arithmetic on the filed tables, and the rule draft only copies what the filer holds, for the reader to edit."""

from __future__ import annotations

from investment_core.thirteenf import Portfolio13F, by_issuer, concentration, diff, equity_only

# A fixed list keeps SEC traffic bounded. The displayed filer name is the one the SEC reports for the CIK.
REFERENCE = [
    {"id": "berkshire", "cik": "1067983", "en": "Berkshire Hathaway", "zh": "伯克希尔·哈撒韦"},
    {"id": "pershing", "cik": "1336528", "en": "Pershing Square", "zh": "潘兴广场"},
    {"id": "bridgewater", "cik": "1350694", "en": "Bridgewater Associates", "zh": "桥水基金"},
    {"id": "duquesne", "cik": "1536411", "en": "Duquesne Family Office", "zh": "杜肯家族办公室"},
    {"id": "renaissance", "cik": "1037389", "en": "Renaissance Technologies", "zh": "文艺复兴科技"},
]


def reference_by_id(rid: str) -> dict | None:
    return next((r for r in REFERENCE if r["id"] == rid), None)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def rule_draft(filer: str, period: str, conc: dict, lang: str) -> str:
    """A starting point in the Lab's rule-file format, copied mechanically from the filing: the largest single
    position and the three largest together. The reader decides every number."""
    one, three = round(conc["top1"], 2), round(conc["top3"], 2)
    if lang == "zh":
        head = [
            f"# 草稿：照抄 {filer} 在 {period} 的 13F 里的集中度，只是参照，不是建议。",
            "# 13F 只含美股多头，看不到空头、现金、债券和海外资产；请按你自己的情况改每一个数字。",
            f"# 他们的最大一笔占 {_pct(one)}，前三笔合计占 {_pct(three)}，共 {int(conc['positions'])} 只。",
        ]
    else:
        head = [
            f"# DRAFT: copies the concentration in {filer}'s 13F for {period}. A reference, not advice.",
            "# A 13F shows US long equity only (no shorts, cash, bonds or foreign assets). Edit every number to suit you.",
            f"# Their largest position is {_pct(one)}, the top three together {_pct(three)}, across {int(conc['positions'])} issuers.",
        ]
    rules = [
        f"  - {{code: SINGLE_MAX_WEIGHT_INVESTED, severity: warn, params: {{max: {one:.2f}}}}}",
        f"  - {{code: TOP3_MAX_WEIGHT_INVESTED,   severity: warn, params: {{max: {three:.2f}, count_core: false}}}}",
    ]
    return "\n".join(head + ["version: draft-13f", "status: draft", "rules:"] + rules) + "\n"


def _row(h, p: Portfolio13F, classes: dict[str, str]) -> dict:
    return {"issuer": h.issuer, "title_class": classes.get(h.cusip, h.title_class), "value_usd": round(h.value_usd),
            "weight": round(p.weight(h), 4)}


def profile(entry: dict, portfolios: list[Portfolio13F], lang: str) -> dict:
    """`portfolios` newest first (one or two quarters)."""
    cur_raw = portfolios[0]
    cur, options = equity_only(cur_raw)
    merged = by_issuer(cur)
    conc = concentration(merged)
    conc["top3"] = sum(sorted((merged.weight(h) for h in merged.holdings), reverse=True)[:3])
    classes = {h.cusip: h.title_class for h in cur_raw.holdings}
    out = {
        "id": entry["id"], "name": entry[lang if lang in ("en", "zh") else "en"], "filer": cur_raw.filer, "cik": cur_raw.cik,
        "period": cur_raw.period.isoformat(), "filed": cur_raw.filed.isoformat(), "accession": cur_raw.accession,
        "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(cur_raw.cik)}/{cur_raw.accession.replace('-', '')}/",
        "total_value_usd": round(merged.total_value), "option_lines_excluded": options,
        "concentration": {k: round(v, 4) if k != "positions" else int(v) for k, v in conc.items()},
        "top": [_row(h, merged, classes) for h in merged.top(15)],
        "previous_period": None, "changes": None,
        "rule_draft": rule_draft(cur_raw.filer, cur_raw.period.isoformat(), conc, lang),
    }
    if len(portfolios) > 1:
        prev, _ = equity_only(portfolios[1])
        classes.update({h.cusip: h.title_class for h in portfolios[1].holdings})
        groups: dict[str, list] = {"new": [], "exited": [], "increased": [], "decreased": []}
        for c in diff(prev, cur):
            if c.kind in groups and len(groups[c.kind]) < 6:
                groups[c.kind].append({"issuer": c.issuer, "title_class": classes.get(c.cusip, ""),
                                       "weight_before": round(c.weight_before, 4), "weight_after": round(c.weight_after, 4),
                                       "shares_change_pct": None if c.shares_change_pct is None else round(c.shares_change_pct, 4)})
        out["previous_period"] = portfolios[1].period.isoformat()
        out["changes"] = groups
    return out
