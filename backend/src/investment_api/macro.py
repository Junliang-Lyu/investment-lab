"""Macro background for the Lab: a few public FRED series (Federal Reserve Bank of St. Louis), read through FRED's
keyless CSV download. Context only: nothing here feeds the gate, the rules or the AI. A failed download never breaks
the page: the last good data is served, and a series that cannot be loaded is simply left out."""

from __future__ import annotations

import csv
import io
import logging
import threading
import time
import urllib.request
from datetime import date, timedelta

log = logging.getLogger("investment_api.macro")

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"
FRED_PAGE = "https://fred.stlouisfed.org/series/{id}"
YEARS = 10

SERIES = [
    {"id": "DGS10", "en": "10-year Treasury yield", "zh": "10年期美债收益率", "unit": "%", "freq": "daily"},
    {"id": "DGS2", "en": "2-year Treasury yield", "zh": "2年期美债收益率", "unit": "%", "freq": "daily"},
    {"id": "T10Y2Y", "en": "10-year minus 2-year spread", "zh": "10年减2年利差", "unit": "pp", "freq": "daily"},
    {"id": "FEDFUNDS", "en": "Federal funds rate", "zh": "联邦基金利率", "unit": "%", "freq": "monthly"},
    {"id": "CPIAUCSL", "en": "Consumer prices, change on a year earlier", "zh": "消费者物价指数(CPI)同比",
     "unit": "%", "freq": "monthly", "yoy": True},
    {"id": "UNRATE", "en": "Unemployment rate", "zh": "失业率", "unit": "%", "freq": "monthly"},
]


def parse_csv(text: str) -> list[tuple[date, float]]:
    """FRED CSV: a date column (`observation_date` or `DATE`) and a value column; a missing value is '.' or empty."""
    rows = list(csv.reader(io.StringIO(text.strip())))
    if len(rows) < 2 or len(rows[0]) < 2:
        return []
    out: list[tuple[date, float]] = []
    for row in rows[1:]:
        if len(row) < 2 or row[1].strip() in ("", "."):
            continue
        try:
            out.append((date.fromisoformat(row[0].strip()), float(row[1])))
        except ValueError:
            continue
    out.sort()
    return out


def weekly(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Daily data thinned to the last observation of each ISO week: ten years stay a few hundred points."""
    last: dict[tuple[int, int], tuple[date, float]] = {}
    for d, v in points:
        iso = d.isocalendar()
        last[(iso[0], iso[1])] = (d, v)
    return sorted(last.values())


def year_over_year(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Percent change against the same month a year before (the index level itself says little to a reader)."""
    by_month = {(d.year, d.month): v for d, v in points}
    out = []
    for d, v in points:
        base = by_month.get((d.year - 1, d.month))
        if base:
            out.append((d, (v / base - 1) * 100))
    return out


def default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "investment-lab/1.0 (+https://invest.jun-liang-lyu.com)"})
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 (fixed https FRED host)
        return resp.read().decode("utf-8", "replace")


def _years_ago(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


class MacroService:
    def __init__(self, ttl_seconds: int = 6 * 3600, fetch=None, today=None):
        self.ttl = ttl_seconds
        self.fetch = fetch or default_fetch
        self._today = today or date.today
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._valid_until: float = 0.0
        self._updated: str = ""

    def _load(self, spec: dict) -> dict | None:
        today = self._today()
        start = _years_ago(today, YEARS)
        if spec.get("yoy"):
            start = start - timedelta(days=400)  # a year of lead so the first plotted month has a base
        pts = parse_csv(self.fetch(FRED_CSV.format(id=spec["id"], start=start.isoformat())))
        if spec.get("yoy"):
            pts = year_over_year(pts)
        pts = [(d, v) for d, v in pts if d >= _years_ago(today, YEARS)]
        if spec["freq"] == "daily":
            pts = weekly(pts)
        if len(pts) < 2:
            return None
        return {"id": spec["id"], "title": {"en": spec["en"], "zh": spec["zh"]}, "unit": spec["unit"],
                "freq": spec["freq"], "points": [[d.isoformat(), round(v, 3)] for d, v in pts],
                "latest": {"date": pts[-1][0].isoformat(), "value": round(pts[-1][1], 3)},
                "url": FRED_PAGE.format(id=spec["id"])}

    def get(self) -> dict | None:
        """The payload, refreshed when past its time. None when nothing has ever loaded."""
        with self._lock:
            if time.monotonic() < self._valid_until:
                return self._payload()
            fresh: dict[str, dict] = {}
            for spec in SERIES:
                try:
                    one = self._load(spec)
                except Exception as e:  # network, FRED outage, malformed file
                    log.warning("macro %s: fetch failed: %s", spec["id"], e)
                    one = None
                if one:
                    fresh[spec["id"]] = one
                elif spec["id"] in self._data:
                    fresh[spec["id"]] = self._data[spec["id"]]  # keep the last good copy of this series
            now = time.monotonic()
            if fresh:
                if fresh != self._data:
                    self._updated = self._today().isoformat()
                self._data = fresh
                # a partial failure is retried sooner than a full refresh
                self._valid_until = now + (self.ttl if len(fresh) == len(SERIES) else min(900, self.ttl))
            else:
                self._valid_until = now + min(300, self.ttl)  # back off a little instead of retrying on every visit
            return self._payload()

    def _payload(self) -> dict | None:
        if not self._data:
            return None
        series = [self._data[s["id"]] for s in SERIES if s["id"] in self._data]
        missing = [s["id"] for s in SERIES if s["id"] not in self._data]
        return {"source": "FRED", "attribution": "Federal Reserve Bank of St. Louis (FRED)", "updated": self._updated,
                "series": series, "missing": missing}
