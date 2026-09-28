"""Golden tests on real SEC filings, recorded with:

    python -m investment_data record-fixture GOOG --out tests/fixtures/edgar

They run only when the recorded fixture exists. Two kinds of checks:
1. Internal consistency: for every complete fiscal year, the four quarters add
   up to the annual (10-K) value.
2. Spot values checked by hand against the company's own earnings release,
   listed in EXPECTED (fill in after verifying; DESIGN §9).
"""

import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

from investment_core.financials import build_financials, extract_facts, METRIC_CONCEPTS

FIXTURES = Path(__file__).parent / "fixtures" / "edgar"
TICKERS = ["GOOG", "TSLA", "MSFT", "MU"]

# (ticker, metric, period_end) -> value in USD, verified against the earnings release.
EXPECTED: dict[tuple[str, str, str], float] = {
    # Checked 2026-09-27. Older quarters match the releases to the $1M; recent
    # quarters match the headline figures (e.g. "$119.8 billion").
    ("GOOG", "revenue", "2024-12-31"): 96_469e6,      # Q4 derived as FY - 9M; release $96,469M
    ("GOOG", "net_income", "2025-03-31"): 34_540e6,   # release $34,540M
    ("GOOG", "revenue", "2026-06-30"): 119_796e6,     # release $119.8B
    ("GOOG", "net_income", "2026-06-30"): 112_193e6,  # release $112.2B
    ("TSLA", "revenue", "2025-09-30"): 28_095e6,      # release $28,095M
    ("MSFT", "revenue", "2025-09-30"): 77_673e6,      # FY26 Q1, release $77.7B
    ("MSFT", "revenue", "2026-06-30"): 90_007e6,      # FY26 Q4 derived as FY - 9M; release ~$90B
    ("MU", "revenue", "2025-08-28"): 11_315e6,        # FY25 Q4 derived; release $11.32B
    ("MU", "revenue", "2026-05-28"): 41_456e6,        # FY26 Q3, release $41.5B
    ("GOOG", "cash_and_investments", "2024-12-31"): 95_657e6,  # "cash, cash equivalents and marketable securities $95.7B"
}


def load(ticker):
    path = FIXTURES / f"{ticker}_companyfacts.json"
    if not path.exists():
        pytest.skip(f"no recorded fixture for {ticker}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("ticker", TICKERS)
@pytest.mark.parametrize("metric", ["revenue", "cfo"])
def test_quarters_sum_to_fiscal_year(ticker, metric):
    data = load(ticker)
    fin = build_financials(data, quarters=40)
    by_fy = defaultdict(list)
    for q in fin.quarters:
        if q.fiscal_label and metric in q.metrics:
            by_fy[q.fiscal_label.split()[0]].append(q.metrics[metric])
    annual = {}
    for concept in METRIC_CONCEPTS[metric]:
        for f in extract_facts(data, concept):
            if f.months == 12:
                # Same naming rule as fiscal_label(): the calendar year in which the fiscal year ends.
                annual.setdefault(f"FY{(f.start + timedelta(days=364)).year}", f.val)
    checked = 0
    for fy, qs in by_fy.items():
        if len(qs) == 4 and fy in annual:
            assert sum(m.value for m in qs) == pytest.approx(annual[fy], rel=1e-5), fy  # filings round to $1M
            checked += 1
    assert checked >= 1, "expected at least one complete fiscal year in the fixture"


@pytest.mark.parametrize("key", list(EXPECTED), ids=lambda k: "-".join(k))
def test_spot_values(key):
    ticker, metric, end = key
    fin = build_financials(load(ticker), quarters=40)
    row = next(q for q in fin.quarters if str(q.end) == end)
    assert row.metrics[metric].value == pytest.approx(EXPECTED[key], rel=1e-4)
