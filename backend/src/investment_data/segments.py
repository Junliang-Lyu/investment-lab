"""Fetch segment data for recent filings (IO)."""

from __future__ import annotations

from datetime import date

from investment_core.segments import DimFact, parse_instance

from .edgar import EdgarClient


def load_segment_facts(client: EdgarClient, cik: str, filings: int = 5) -> list[DimFact]:
    """Dimensional facts from the latest 10-Q/10-K filings (5 covers a Q4 derivation: 10-K plus prior Q3)."""
    out: list[DimFact] = []
    for f in client.filings(cik, ("10-Q", "10-K"))[:filings]:
        xml = client.xbrl_instance(cik, f["accession"])
        out += parse_instance(xml, f["accession"], f["form"], date.fromisoformat(f["filed"]))
    return out
