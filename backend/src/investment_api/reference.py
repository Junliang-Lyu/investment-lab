"""Reference portfolios: what well-known institutions report holding in their 13F filings (SEC), summarised for
learning how others structure a portfolio. Nothing here is a recommendation and no model is involved: the numbers are
arithmetic on the filed tables, and the rule draft only copies what the filer holds, for the reader to edit."""

from __future__ import annotations

import re

from investment_core.thirteenf import Portfolio13F, by_issuer, concentration, diff, equity_only, issuer_key

# A fixed list keeps SEC traffic bounded; any other 13F filer can be looked up by CIK (rate-limited, see lab.py).
# `expect` is a fragment of the name the SEC reports for the CIK (checked against the live SEC in the release
# routine). `sources` are the institution's OWN publications, linked and never rewritten here.
L = "letters"
REFERENCE = [
    {"id": "berkshire", "cik": "1067983", "expect": "BERKSHIRE", "en": "Berkshire Hathaway", "zh": "伯克希尔·哈撒韦",
     "aliases": ["buffett", "warren buffett", "巴菲特", "伯克希尔", "巴郡"],
     "about": {"en": "Warren Buffett's holding company. The 13F covers only the listed shares it holds, not the businesses it owns outright.",
               "zh": "巴菲特掌舵的控股公司。13F 只反映它持有的上市公司股票，不包括它全资拥有的那些企业。"},
     "sources": [{"kind": L, "url": "https://www.berkshirehathaway.com/letters/letters.html",
                  "title": {"en": "Annual letters to shareholders (all years)", "zh": "历年致股东信（全部年份）"}}]},
    {"id": "ark", "cik": "1697748", "expect": "ARK INVEST", "en": "ARK Investment Management", "zh": "方舟投资（ARK）",
     "aliases": ["ark", "cathie wood", "arkk", "方舟", "木头姐", "凯茜·伍德", "凯瑟琳·伍德"],
     "about": {"en": "Cathie Wood's firm, which runs actively managed funds themed on 'disruptive innovation'. Its 13F is the combined holdings of those funds, and it trades often, so the picture changes quickly.",
               "zh": "凯茜·伍德（木头姐）的公司，管理以“颠覆式创新”为主题的主动型基金。它的 13F 是这些基金的合并持仓，交易频繁，变化很快。"},
     "sources": [{"kind": "research", "url": "https://www.ark-invest.com/articles",
                  "title": {"en": "ARK's research articles and commentary", "zh": "ARK 的研究文章与评论"}}]},
    {"id": "hh", "cik": "1759760", "expect": "H&H", "en": "H&H International Investment", "zh": "H&H International（段永平）",
     "aliases": ["duan yongping", "h&h", "hh international", "段永平", "大道无形"],
     "about": {"en": "The 13F filer widely reported in the media as Duan Yongping's investment vehicle; the filing itself names only the company, not a person. A very concentrated portfolio.",
               "zh": "媒体普遍报道这是段永平的投资主体；报告本身只写公司名，没有写个人。组合非常集中。"},
     "note": {"en": "Duan Yongping does not publish letters. His public remarks are scattered across social platforms, and this page does not repeat them: read them at the source and check the date.",
              "zh": "段永平没有发布正式的投资信件。他的公开发言散见于社交平台，这里不转引，请到原处阅读并留意发言日期。"},
     "sources": []},
    {"id": "himalaya", "cik": "1709323", "expect": "HIMALAYA", "en": "Himalaya Capital Management", "zh": "喜马拉雅资本（李录）",
     "aliases": ["li lu", "himalaya", "李录", "喜马拉雅"],
     "about": {"en": "The firm of Li Lu, a long-term investor known for a very small number of large positions.",
               "zh": "李录的投资公司，以长期持有、仓位极少而集中著称。"},
     "note": {"en": "No regular public letters from this firm are linked here.", "zh": "这家机构没有定期公开的投资信件，这里没有可链接的官方材料。"},
     "sources": []},
    {"id": "pershing", "cik": "1336528", "expect": "PERSHING SQUARE", "en": "Pershing Square", "zh": "潘兴广场（阿克曼）",
     "aliases": ["ackman", "bill ackman", "pershing", "阿克曼", "比尔·阿克曼", "潘兴"],
     "about": {"en": "Bill Ackman's firm, which holds a small number of large positions and often explains its reasoning publicly.",
               "zh": "比尔·阿克曼的公司，持有少数几只大仓位，并经常公开阐述自己的理由。"},
     "sources": [{"kind": L, "url": "https://pershingsquareholdings.com/",
                  "title": {"en": "Pershing Square Holdings: shareholder letters and reports", "zh": "Pershing Square Holdings：股东信与报告"}}]},
    {"id": "oaktree", "cik": "1822973", "expect": "OAKTREE", "en": "Oaktree Fund Advisors", "zh": "橡树资本（Oaktree Fund Advisors）",
     "aliases": ["howard marks", "marks", "oaktree", "霍华德·马克斯", "马克斯", "橡树"],
     "about": {"en": "The Oaktree entity that files the 13F. Oaktree was co-founded by Howard Marks and is mainly a credit and distressed-debt investor, so a 13F (US listed equity only) is a small part of what it manages.",
               "zh": "橡树资本旗下提交 13F 的机构。橡树由霍华德·马克斯参与创办，以信贷和困境债务投资为主，所以只含美股的 13F 只是它管理资产的一小部分。"},
     "sources": [{"kind": "memos", "url": "https://www.oaktreecapital.com/insights/howard-marks-memos",
                  "title": {"en": "Memos from Howard Marks", "zh": "霍华德·马克斯的备忘录"}}]},
    {"id": "bridgewater", "cik": "1350694", "expect": "BRIDGEWATER", "en": "Bridgewater Associates", "zh": "桥水基金（达利欧创办）",
     "aliases": ["ray dalio", "dalio", "bridgewater", "达利欧", "桥水"],
     "about": {"en": "A macro investor founded by Ray Dalio. Most of what it runs is in futures, bonds and other instruments that a 13F does not show, so this is a small slice.",
               "zh": "雷·达利欧创办的宏观基金。它管理的大头是期货、债券等 13F 看不到的品种，所以这里只是很小的一部分。"},
     "sources": [{"kind": "research", "url": "https://www.bridgewater.com/research-and-insights",
                  "title": {"en": "Bridgewater's research and insights", "zh": "桥水的研究与观点"}}]},
    {"id": "duquesne", "cik": "1536411", "expect": "DUQUESNE", "en": "Duquesne Family Office", "zh": "杜肯家族办公室（德鲁肯米勒）",
     "aliases": ["druckenmiller", "stanley druckenmiller", "duquesne", "德鲁肯米勒", "杜肯"],
     "about": {"en": "Stanley Druckenmiller's family office, a concentrated and fast-changing portfolio.", "zh": "斯坦利·德鲁肯米勒的家族办公室，组合集中、调整频繁。"},
     "sources": []},
    {"id": "baupost", "cik": "1061768", "expect": "BAUPOST", "en": "Baupost Group", "zh": "鲍波斯特（克拉曼）",
     "aliases": ["klarman", "seth klarman", "baupost", "克拉曼", "鲍波斯特"],
     "about": {"en": "Seth Klarman's value-oriented firm. It holds a lot that a 13F does not show, such as cash and private investments.",
               "zh": "赛斯·克拉曼的价值投资机构。它持有很多 13F 看不到的东西，比如现金和非上市投资。"},
     "sources": []},
    {"id": "renaissance", "cik": "1037389", "expect": "RENAISSANCE", "en": "Renaissance Technologies", "zh": "文艺复兴科技",
     "aliases": ["simons", "jim simons", "renaissance", "rentec", "西蒙斯", "文艺复兴"],
     "about": {"en": "A quantitative fund with thousands of small positions: the opposite of a concentrated portfolio, and a useful contrast.",
               "zh": "量化基金，持有几千个小仓位——和集中型组合正好相反，适合做对照。"},
     "sources": []},
    {"id": "soros", "cik": "1029160", "expect": "SOROS", "en": "Soros Fund Management", "zh": "索罗斯基金管理公司",
     "aliases": ["soros", "george soros", "索罗斯"],
     "about": {"en": "George Soros's investment firm (now a family office). A 13F shows only its US listed equity.", "zh": "乔治·索罗斯的投资机构（现为家族办公室）。13F 只显示它的美股部分。"},
     "sources": []},
]


def reference_by_id(rid: str) -> dict | None:
    return next((r for r in REFERENCE if r["id"] == rid), None)


def custom_entry(cik: str, name: str) -> dict:
    """Any other 13F filer, looked up by CIK: only what the SEC itself says."""
    return {"id": f"cik{int(cik)}", "cik": cik, "en": name, "zh": name, "aliases": [], "about": None, "sources": [],
            "custom": True}


def directory() -> list[dict]:
    return [{"id": x["id"], "name": {"en": x["en"], "zh": x["zh"]}, "aliases": x["aliases"],
             "about": x.get("about"), "has_sources": bool(x.get("sources"))} for x in REFERENCE]


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


SAMPLE_N = 10
_SLASH = re.compile(r"\s*/[A-Z]{1,4}/?\s*$")


def ticker_index(ticker_map: dict[str, dict]) -> dict[str, str]:
    """Company name (as `issuer_key` writes it) -> ticker, from the SEC's list. The first ticker listed wins, so
    when a company has several (share classes) the larger one is used. Best effort: names are not unique keys."""
    out: dict[str, str] = {}
    for tk, row in ticker_map.items():
        key = issuer_key(_SLASH.sub("", str(row.get("title", ""))))
        if key and key not in out:
            out[key] = tk
    return out


def sample(merged: Portfolio13F, index: dict[str, str] | None, n: int = SAMPLE_N) -> dict:
    """The top `n` companies renormalised to 100%: a stand-in portfolio to try in the pre-trade gate. A company whose
    ticker cannot be matched gets a made-up label (`real: false`), only so it can be told apart in the table."""
    top = merged.top(n)
    total = sum(h.value_usd for h in top) or 1.0
    used: set[str] = set()
    rows = []
    for h in top:
        sym = (index or {}).get(issuer_key(h.issuer))
        real = bool(sym and re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", sym))
        if not real:
            letters = re.sub(r"[^A-Z]", "", issuer_key(h.issuer))[:10] or "X"
            sym = letters
        base, k = sym, 0
        while sym in used:  # symbols are letters only in the gate: tell twins apart with a trailing letter
            k += 1
            sym = base[: 9] + "BCDEFGHIJ"[(k - 1) % 9]
        used.add(sym)
        rows.append({"symbol": sym, "issuer": h.issuer, "real": real, "weight": round(h.value_usd / total, 4)})
    return {"positions": rows, "share_of_reported": round(sum(h.value_usd for h in top) / (merged.total_value or 1.0), 4)}


def _row(h, p: Portfolio13F, classes: dict[str, str]) -> dict:
    return {"issuer": h.issuer, "title_class": classes.get(h.cusip, h.title_class), "value_usd": round(h.value_usd),
            "weight": round(p.weight(h), 4)}


def profile(entry: dict, portfolios: list[Portfolio13F], lang: str, tickers: dict[str, str] | None = None) -> dict:
    """`portfolios` newest first (one or two quarters). `tickers` (see ticker_index) lets the sample carry real symbols."""
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
        "sample": sample(merged, tickers),
        "previous_period": None, "changes": None,
        "rule_draft": rule_draft(cur_raw.filer, cur_raw.period.isoformat(), conc, lang),
        "draft_values": {"single_max": round(conc["top1"], 2), "top3_max": round(conc["top3"], 2)},
        "about": (entry.get("about") or {}).get(lang if lang in ("en", "zh") else "en"),
        "note": (entry.get("note") or {}).get(lang if lang in ("en", "zh") else "en"),
        "sources": [{"kind": x["kind"], "url": x["url"], "title": x["title"][lang if lang in ("en", "zh") else "en"]}
                    for x in entry.get("sources", [])],
        "custom": bool(entry.get("custom")),
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
