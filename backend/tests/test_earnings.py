"""Estimated next earnings date from past earnings 8-K dates; and the review-date rule on memos."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from test_api import FakeEdgar
from test_lab_memo import ANSWERS, start
from test_lab_skeptic import pack  # noqa: F401  (fixture)

from investment_api.app import create_app
from investment_api.earnings import estimate_next_earnings
from investment_api.settings import Settings

# Alphabet-like rhythm: late Jan, Apr, Jul, Oct.
D = [date(2026, 7, 23), date(2026, 4, 23), date(2026, 2, 4), date(2025, 10, 29), date(2025, 7, 23), date(2025, 4, 24)]


def test_estimate_uses_the_same_quarter_a_year_earlier():
    e = estimate_next_earnings(D, today=date(2026, 8, 1))
    assert e["method"] == "year_ago" and e["last_reported"] == "2026-07-23"
    assert e["estimated"] == str(date(2025, 10, 29) + timedelta(days=364)) == "2026-10-28"
    assert e["estimated"] and not e["past"]


def test_estimate_falls_back_to_the_quarterly_rhythm():
    e = estimate_next_earnings(D[:2], today=date(2026, 8, 1))
    assert e["method"] == "cadence" and e["estimated"] == str(D[0] + timedelta(days=91))
    # an extra 8-K in the list breaks the pattern: a date that does not fit a quarter is not used
    odd = [date(2026, 7, 23), date(2026, 7, 20), date(2026, 7, 1), date(2026, 6, 20), date(2026, 4, 23)]
    assert estimate_next_earnings(odd, today=date(2026, 8, 1))["method"] == "cadence"


def test_estimate_flags_a_date_that_has_passed_and_handles_no_history():
    assert estimate_next_earnings(D, today=date(2026, 11, 15))["past"] is True
    assert estimate_next_earnings([], today=date(2026, 8, 1)) is None


class WithFilings(FakeEdgar):
    def filings(self, cik, forms):
        out = [{"form": "8-K", "filed": d.isoformat(), "items": "2.02,9.01", "accession": "x", "report_date": "",
                "primary_document": ""} for d in D]
        out.insert(1, {"form": "8-K", "filed": "2026-06-01", "items": "5.07", "accession": "y", "report_date": "",
                       "primary_document": ""})  # a shareholder-meeting 8-K is not an earnings release
        return out


def test_next_earnings_endpoint():
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=WithFilings))
    r = c.get("/api/lab/companies/GOOG/next-earnings")
    assert r.status_code == 200 and r.json()["method"] == "year_ago" and r.json()["last_reported"] == "2026-07-23"
    assert c.get("/api/lab/companies/ORCL/next-earnings").status_code == 200  # any SEC-registered company
    assert c.get("/api/lab/companies/XYZQ/next-earnings").status_code == 404


def test_next_earnings_when_sec_is_down():
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=FakeEdgar))  # filings() raises
    assert c.get("/api/lab/companies/GOOG/next-earnings").status_code == 503


def test_review_date_must_not_be_in_the_past(tmp_path, pack):
    c, _, m = start(tmp_path, pack)
    yesterday = str(date.today() - timedelta(days=2))
    r = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "review_date": yesterday})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "review_date"
    ok = c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS)
    assert ok.status_code == 200
