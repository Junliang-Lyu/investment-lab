"""Fetch a company's recent Form 4 filings from EDGAR (IO). Parsing lives in investment_core.insiders."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from investment_core.insiders import Form4, parse_form4

from .edgar import EdgarClient

log = logging.getLogger("investment_data.insiders")

MAX_FILINGS = 100  # the newest Form 4s inside the window; a very busy filer is cut here and the page says so


def load_form4s(client: EdgarClient, cik: str, today: date, months: int = 12,
                cap: int = MAX_FILINGS) -> tuple[list[Form4], dict]:
    """Newest first. Returns the parsed forms and {"filings": how many were in the window, "read": parsed OK,
    "truncated": bool}. Raises only when filings exist but not one could be read."""
    start = today - timedelta(days=round(months * 30.4))
    listed = [f for f in client.filings(cik, ("4",)) if date.fromisoformat(f["filed"]) >= start]
    take = listed[:cap]
    out, failed, foreign = [], 0, 0
    for f in take:
        try:
            name = (f.get("primary_document") or "").rsplit("/", 1)[-1]  # the plain XML, not the rendered page
            if not name.lower().endswith(".xml"):
                names = [n for n in client.filing_files(cik, f["accession"]) if n.lower().endswith(".xml")]
                name = names[0] if names else ""
            if not name:
                raise KeyError("no xml")
            form = parse_form4(client.filing_file(cik, f["accession"], name))
            if form is None:
                raise ValueError("not an ownership document")
        except Exception as e:  # one bad filing must not hide the others
            failed += 1
            log.warning("form4 %s: %s", f.get("accession"), e)
            continue
        if form.issuer_cik is not None and form.issuer_cik != int(cik):
            foreign += 1  # the company filed this as an owner of another company's shares
            continue
        form.accession, form.filed = f["accession"], date.fromisoformat(f["filed"])
        form.url = client.document_url(cik, f["accession"], name)
        out.append(form)
    if take and not out and not foreign:
        raise RuntimeError("could not read any Form 4")
    return out, {"filings": len(listed), "read": len(out) + foreign, "failed": failed, "as_owner": foreign,
                 "truncated": len(listed) > len(take)}
