"""Fetch 10-K / 10-Q narrative passages (IO)."""

from __future__ import annotations

import re

from investment_core.filing_text import Passage, build_passages, build_release_passages, new_risk_passages

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


_EXHIBIT = re.compile(r"ex[-_]?99[-_.]?0?1?|99[-_.]?1|press|earnings|results", re.I)


def release_file(names: list[str], primary: str) -> str | None:
    """The press-release exhibit of an 8-K: names look like ex99.htm, goog-20260630xex99.htm, q2pressrelease.htm."""
    htm = [n for n in names if n.lower().endswith((".htm", ".html")) and n != primary
           and not re.match(r"(?i)r\d+\.htm|.*index.*", n)]
    for n in htm:
        if re.search(r"(?i)ex[-_]?99", n):
            return n
    hit = next((n for n in htm if _EXHIBIT.search(n)), None)
    # Short names such as q2fy27pr.htm ("pr" = press release), as NVIDIA files it; CFO commentary is not the release.
    return hit or next((n for n in htm if re.search(r"(?i)(?:^|[^a-z])pr\.html?$|\d{2}pr\.html?$", n)), None)


def load_release_passages(client: EdgarClient, cik: str) -> list[Passage]:
    """The latest earnings press release: the newest 8-K that reports results (Item 2.02)."""
    f = next((x for x in client.filings(cik, ("8-K",)) if "2.02" in (x.get("items") or "")), None)
    if f is None:
        return []
    name = release_file(client.filing_files(cik, f["accession"]), f.get("primary_document") or "")
    if name is None:
        return []
    html = client.filing_file(cik, f["accession"], name).decode("utf-8", "replace")
    return build_release_passages(html, f["accession"], client.document_url(cik, f["accession"], name))


def load_new_risks(client: EdgarClient, cik: str, latest: list[Passage]) -> list[Passage]:
    """Risk-factor paragraphs of the latest 10-K that are new or reworded compared with the prior year's 10-K."""
    tenks = [x for x in client.filings(cik, ("10-K",)) if x.get("primary_document")]
    if len(tenks) < 2:
        return []
    prior = tenks[1]
    html = client.filing_file(cik, prior["accession"], prior["primary_document"]).decode("utf-8", "replace")
    old = build_passages(html, prior["accession"], None, ("item1a",))
    return new_risk_passages(latest, old)
