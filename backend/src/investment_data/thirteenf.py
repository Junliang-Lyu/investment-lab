"""Fetch 13F portfolios from EDGAR (IO). Parsing lives in investment_core.thirteenf."""

from __future__ import annotations

from datetime import date

from investment_core.thirteenf import Portfolio13F, aggregate, parse_info_table

from .edgar import EdgarClient, pad_cik


def resolve_cik(client: EdgarClient, who: str) -> str:
    """Accept a CIK or a ticker (e.g. BRK-B)."""
    return pad_cik(who) if who.isdigit() else client.cik_for(who)


def load_portfolios(client: EdgarClient, who: str, quarters: int = 2) -> list[Portfolio13F]:
    """Newest first; one original 13F-HR per report period (amendments are not merged in v1)."""
    cik = resolve_cik(client, who)
    name = client.submissions(cik).get("name", "")
    seen, out = set(), []
    for f in client.filings(cik, ("13F-HR",)):
        if f["report_date"] in seen:
            continue
        seen.add(f["report_date"])
        filed = date.fromisoformat(f["filed"])
        rows = parse_info_table(client.info_table(cik, f["accession"]), filed)
        out.append(Portfolio13F(filer=name, cik=cik, period=f["report_date"], filed=filed, accession=f["accession"],
                                holdings=aggregate(rows)))
        if len(out) == quarters:
            break
    return out
