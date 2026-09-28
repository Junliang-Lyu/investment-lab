"""Fetch 10-K / 10-Q narrative passages (IO)."""

from __future__ import annotations

from investment_core.filing_text import Passage, build_passages

from .edgar import EdgarClient


def load_passages(client: EdgarClient, cik: str) -> list[Passage]:
    """Latest 10-K (business, risk factors, MD&A) and latest 10-Q (MD&A)."""
    out: list[Passage] = []
    filings = client.filings(cik, ("10-K", "10-Q"))
    for form, items in (("10-K", ("item1", "item1a", "item7")), ("10-Q", ("item2_10q",))):
        f = next((x for x in filings if x["form"] == form and x.get("primary_document")), None)
        if f is None:
            continue
        html = client.filing_file(cik, f["accession"], f["primary_document"]).decode("utf-8", "replace")
        url = client.document_url(cik, f["accession"], f["primary_document"])
        out += build_passages(html, f["accession"], url, items)
    return out
