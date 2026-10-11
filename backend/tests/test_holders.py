"""Which listed institutions hold a company (13F), and the change from the quarter before."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from test_reference import berkshire, h, pf
from test_thirteenf import FakeClient

from investment_api.app import create_app
from investment_api.reference import REFERENCE
from investment_api.settings import Settings
from investment_core.holders import company_key, holder_row
from investment_core.thirteenf import Portfolio13F


def test_company_key_makes_the_sec_list_and_the_13f_name_agree():
    assert company_key("Berkshire Hathaway Inc.") == company_key("BERKSHIRE HATHAWAY INC DEL") == "BERKSHIRE HATHAWAY INC"
    assert company_key("Alphabet Inc.") == company_key("ALPHABET INC CL C") == "ALPHABET INC"
    assert company_key("Brown Forman Corp /DE/") == "BROWN FORMAN CORP"
    assert company_key("APPLE INC") != company_key("APPLE HOSPITALITY REIT INC")


def two(cur_rows, prev_rows):
    cur = pf(*cur_rows)
    prev = Portfolio13F(filer="X", cik="1", period=date(2026, 3, 31), filed=date(2026, 5, 15), accession="0001-26-0",
                        holdings=prev_rows)
    return [cur, prev]


def test_weight_rank_and_change_by_share_count():
    pfs = two([h("AAA INC", "1", 600, shares=120), h("BBB INC", "2", 300), h("CCC INC", "3", 100)],
              [h("AAA INC", "1", 500, shares=100), h("BBB INC", "2", 500)])
    a = holder_row(pfs, "AAA INC")
    assert a["held"] and a["weight"] == 0.6 and a["rank"] == 1 and a["positions"] == 3
    assert a["change"] == "increased" and a["change_pct"] == 0.2 and a["prev_shares"] == 100
    assert holder_row(pfs, "CCC INC")["change"] == "new" and holder_row(pfs, "CCC INC")["change_pct"] is None
    assert holder_row(pfs, "ZZZ INC") is None


def test_exited_and_trimmed_and_unchanged_and_single_quarter():
    pfs = two([h("AAA INC", "1", 100, shares=50), h("BBB INC", "2", 100, shares=10)],
              [h("AAA INC", "1", 100, shares=100), h("BBB INC", "2", 100, shares=10), h("OLD INC", "9", 100)])
    assert holder_row(pfs, "AAA INC")["change"] == "decreased" and holder_row(pfs, "AAA INC")["change_pct"] == -0.5
    assert holder_row(pfs, "BBB INC")["change"] == "unchanged"
    old = holder_row(pfs, "OLD INC")
    assert old["held"] is False and old["change"] == "exited" and old["weight"] is None and old["prev_shares"] == 10
    one = holder_row(pfs[:1], "AAA INC")
    assert one["change"] is None and one["previous_period"] is None


def test_share_classes_and_options_are_handled_like_the_reference_page():
    pfs = two([h("ALPHABET INC", "A", 60, title="CL A"), h("ALPHABET INC", "C", 30, title="CL C"),
               h("ALPHABET INC", "A", 900, put_call="Put"), h("BBB INC", "2", 10)], [])
    a = holder_row(pfs[:1], "ALPHABET INC")
    assert a["value_usd"] == 90 and a["weight"] == 0.9 and a["shares"] == 20


class Fake(FakeClient):
    def ticker_map(self):
        return {"AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "BRK-B": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
                "NONE": {"cik_str": 1, "ticker": "NONE", "title": "Nobody Holds This Corp"}}


def _client(factory=Fake):
    return TestClient(create_app(Settings(rate_per_minute=1000), client_factory=factory))


def test_endpoint_lists_holders_and_those_who_do_not():
    b = _client().get("/api/lab/companies/AAPL/holders?lang=zh").json()
    assert b["ticker"] == "AAPL" and b["company"] == "Apple Inc." and b["covered"] == len(REFERENCE)
    assert len(b["institutions"]) == len(REFERENCE) and b["not_held"] == []  # the fixture serves one filing to everyone
    top = b["institutions"][0]
    assert top["held"] and top["weight"] == pytest.approx(0.2204, abs=5e-4) and top["rank"] == 1
    assert top["name"] == REFERENCE[0]["zh"] or top["name"] in {r["zh"] for r in REFERENCE}
    n = _client().get("/api/lab/companies/NONE/holders").json()
    assert n["institutions"] == [] and len(n["not_held"]) == len(REFERENCE)


def test_endpoint_errors_and_cache():
    assert _client().get("/api/lab/companies/XYZQ/holders").status_code == 404
    assert _client().get("/api/lab/companies/1bad/holders").status_code == 422

    class Down:
        def ticker_map(self):
            raise RuntimeError("SEC down")
    assert _client(Down).get("/api/lab/companies/AAPL/holders").status_code == 503

    made = []

    class Counting(Fake):
        def submissions(self, cik):
            made.append(1)
            return super().submissions(cik)
    c = _client(Counting)
    assert c.get("/api/lab/companies/AAPL/holders").status_code == 200
    n = len(made)
    assert c.get("/api/lab/companies/AAPL/holders").status_code == 200 and len(made) == n


def test_endpoint_marks_institutions_it_could_not_read():
    class Partial(Fake):
        def submissions(self, cik):
            if int(cik) == int(REFERENCE[1]["cik"]):
                raise RuntimeError("boom")
            return super().submissions(cik)
    b = _client(Partial).get("/api/lab/companies/AAPL/holders").json()
    assert [u["id"] for u in b["unavailable"]] == [REFERENCE[1]["id"]] and len(b["institutions"]) == len(REFERENCE) - 1
