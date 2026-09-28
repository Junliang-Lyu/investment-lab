"""SEC EDGAR client. See docs/DESIGN.md §9.

SEC fair-access rules: every request declares a User-Agent with a name and a
contact email, and stays under 10 requests per second. The User-Agent comes
from the SEC_USER_AGENT environment variable, e.g. "Jane Doe jane@example.com".
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Callable

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

Fetch = Callable[[str, dict[str, str]], bytes]


class EdgarConfigError(RuntimeError):
    pass


def _urllib_fetch(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body


def pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


class EdgarClient:
    def __init__(self, user_agent: str | None = None, cache_dir: str | Path | None = None,
                 ttl_hours: float = 24, min_interval: float = 0.12, fetch: Fetch | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT", "").strip()
        if not self.user_agent or "@" not in self.user_agent:
            raise EdgarConfigError(
                "Set SEC_USER_AGENT to '<your name> <your email>' (SEC requires a contact in the User-Agent)")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.ttl_seconds = ttl_hours * 3600
        self.min_interval = min_interval
        self._fetch = fetch or _urllib_fetch
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None

    # -- transport --------------------------------------------------------
    def _get(self, url: str, cache_name: str, immutable: bool = False) -> bytes:
        cached = self._cache_read(cache_name, immutable)
        if cached is not None:
            return cached
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        body = self._fetch(url, headers)
        self._last = self._clock()
        self._cache_write(cache_name, body)
        return body

    def _get_json(self, url: str, cache_name: str, immutable: bool = False) -> dict:
        return json.loads(self._get(url, cache_name, immutable))

    def _cache_read(self, name: str, immutable: bool = False) -> bytes | None:
        if not self.cache_dir:
            return None
        path = self.cache_dir / name
        if path.exists() and (immutable or time.time() - path.stat().st_mtime < self.ttl_seconds):
            return path.read_bytes()
        return None

    def _cache_write(self, name: str, body: bytes) -> None:
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / name).write_bytes(body)

    # -- API --------------------------------------------------------------
    def ticker_map(self) -> dict[str, dict]:
        data = self._get_json(TICKERS_URL, "company_tickers.json")
        return {row["ticker"].upper(): row for row in data.values()}

    def cik_for(self, ticker: str) -> str:
        row = self.ticker_map().get(ticker.upper())
        if row is None:
            raise KeyError(f"unknown ticker: {ticker}")
        return pad_cik(row["cik_str"])

    def company_facts(self, cik: str | int) -> dict:
        c = pad_cik(cik)
        return self._get_json(FACTS_URL.format(cik=c), f"companyfacts_{c}.json")

    def submissions(self, cik: str | int) -> dict:
        c = pad_cik(cik)
        return self._get_json(SUBMISSIONS_URL.format(cik=c), f"submissions_{c}.json")

    def filings(self, cik: str | int, forms: tuple[str, ...]) -> list[dict]:
        """Recent filings of the given form types, newest first."""
        r = self.submissions(cik).get("filings", {}).get("recent", {})
        out = []
        for i, form in enumerate(r.get("form", [])):
            if form in forms:
                out.append({"form": form, "accession": r["accessionNumber"][i], "filed": r["filingDate"][i],
                            "report_date": r["reportDate"][i],
                            "primary_document": (r.get("primaryDocument") or [""] * len(r["form"]))[i]})
        return out

    def filing_files(self, cik: str | int, accession: str) -> list[str]:
        acc = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json"
        data = self._get_json(url, f"index_{acc}.json", immutable=True)  # filed documents never change
        return [i["name"] for i in data.get("directory", {}).get("item", [])]

    def filing_file(self, cik: str | int, accession: str, name: str) -> bytes:
        acc = accession.replace("-", "")
        return self._get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{name}", f"file_{acc}_{name}",
                         immutable=True)

    def info_table(self, cik: str | int, accession: str) -> bytes:
        """The 13F information table XML (file name varies by filer agent)."""
        names = [n for n in self.filing_files(cik, accession) if n.lower().endswith(".xml") and n != "primary_doc.xml"]
        if not names:
            raise KeyError(f"no information table in {accession}")
        preferred = [n for n in names if "info" in n.lower()]
        return self.filing_file(cik, accession, (preferred or names)[0])

    def xbrl_instance(self, cik: str | int, accession: str) -> bytes:
        """The filing's XBRL instance (inline filings: *_htm.xml)."""
        names = self.filing_files(cik, accession)
        inline = [n for n in names if n.endswith("_htm.xml")]
        if not inline:
            skip = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "FilingSummary.xml")
            inline = [n for n in names if n.endswith(".xml") and not n.endswith(skip)]
        if not inline:
            raise KeyError(f"no XBRL instance in {accession}")
        return self.filing_file(cik, accession, inline[0])

    def document_url(self, cik: str | int, accession: str, name: str) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{name}"
