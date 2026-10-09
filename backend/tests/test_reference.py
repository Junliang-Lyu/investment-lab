"""13F reference portfolios: share-class merging, option lines, the profile, the rule draft and the endpoints."""

from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient
from test_thirteenf import FakeClient

from investment_api.app import create_app
from investment_api.reference import REFERENCE, profile, rule_draft
from investment_api.settings import Settings
from investment_core.models import RuleSet
from investment_core.thirteenf import Holding13F, Portfolio13F, by_issuer, equity_only, issuer_key
from investment_data.thirteenf import load_portfolios


def h(issuer, cusip, value, put_call=None, title="COM", shares=10):
    return Holding13F(cusip=cusip, issuer=issuer, title_class=title, value_usd=value, shares=shares, put_call=put_call)


def pf(*rows):
    return Portfolio13F(filer="X", cik="1", period=date(2026, 6, 30), filed=date(2026, 8, 14), accession="0001-26-1",
                        holdings=list(rows))


def test_issuer_key_ignores_share_class_and_punctuation():
    assert issuer_key("ALPHABET INC CL A") == issuer_key("Alphabet Inc., Class C") == "ALPHABET INC"
    assert issuer_key("APPLE INC") != issuer_key("APPLE HOSPITALITY REIT INC")


def test_options_are_left_out_and_counted():
    p = pf(h("AAA", "1", 100), h("AAA", "1", 900, put_call="Put"), h("BBB", "2", 100))
    eq, dropped = equity_only(p)
    assert dropped == 1 and eq.total_value == 200 and eq.weight(eq.holdings[0]) == 0.5


def test_share_classes_count_as_one_company():
    p = pf(h("ALPHABET INC", "A1", 60, title="CAP STK CL A"), h("ALPHABET INC", "C1", 30, title="CAP STK CL C"),
           h("BBB CO", "2", 10))
    m = by_issuer(p)
    assert len(m.holdings) == 2 and m.holdings[0].value_usd == 90 and m.weight(m.holdings[0]) == pytest.approx(0.9)
    assert m.holdings[0].title_class == "CAP STK CL A"  # the larger line names it


def berkshire():
    return load_portfolios(FakeClient(), "1067983", quarters=2)


def test_profile_of_berkshire_matches_the_golden_numbers():
    p = profile(REFERENCE[0], berkshire(), "en")
    assert p["filer"] == "BERKSHIRE HATHAWAY INC" and p["period"] == "2026-06-30" and p["previous_period"] == "2026-03-31"
    assert p["top"][0]["issuer"] == "APPLE INC" and p["top"][0]["weight"] == pytest.approx(0.2204, abs=5e-4)
    ws = [t["weight"] for t in p["top"]]
    assert ws == sorted(ws, reverse=True) and sum(ws) <= 1.0001
    c = p["concentration"]
    assert c["top1"] <= c["top3"] <= c["top5"] <= c["top10"] <= 1 and c["positions"] > 10
    assert p["filing_url"].endswith("/000119312526352200/") and p["option_lines_excluded"] == 0
    assert [x["issuer"] for x in p["changes"]["new"]] == ["D R HORTON INC"]
    assert [x["issuer"] for x in p["changes"]["exited"]] == ["CONSTELLATION BRANDS INC"]
    assert any(x["issuer"] == "BANK OF AMER CORP" for x in p["changes"]["decreased"])


def test_profile_with_a_single_quarter_has_no_changes():
    p = profile(REFERENCE[0], berkshire()[:1], "zh")
    assert p["changes"] is None and p["previous_period"] is None and p["name"] == "伯克希尔·哈撒韦"


def test_rule_draft_is_a_valid_rule_file_and_says_it_is_not_advice():
    for lang in ("en", "zh"):
        text = rule_draft("X FUND", "2026-06-30", {"top1": 0.2204, "top3": 0.5179, "positions": 26.0}, lang)
        rs = RuleSet(**yaml.safe_load(text))
        assert rs.status == "draft" and rs.get("SINGLE_MAX_WEIGHT_INVESTED").params["max"] == 0.22
        assert rs.get("TOP3_MAX_WEIGHT_INVESTED").params["max"] == 0.52
        assert text.lstrip().startswith("#") and ("不是建议" in text if lang == "zh" else "not advice" in text)


def client(factory=FakeClient, **kw):
    return TestClient(create_app(Settings(rate_per_minute=1000, **kw), client_factory=factory))


def test_reference_endpoints():
    c = client()
    lst = c.get("/api/lab/reference").json()
    assert [x["id"] for x in lst] == [r["id"] for r in REFERENCE] and lst[0]["name"]["zh"]
    r = c.get("/api/lab/reference/berkshire?lang=zh")
    assert r.status_code == 200 and r.json()["name"] == "伯克希尔·哈撒韦" and "草稿" in r.json()["rule_draft"]
    assert c.get("/api/lab/reference/nobody").status_code == 404


def test_reference_is_cached_and_survives_nothing_but_503_on_failure():
    made = []

    def factory():
        made.append(1)
        return FakeClient()

    c = client(factory)
    assert c.get("/api/lab/reference/berkshire").status_code == 200
    assert c.get("/api/lab/reference/berkshire?lang=zh").status_code == 200
    assert len(made) == 1  # the second visit (other language) is served from memory

    class Down:
        def submissions(self, cik):
            raise RuntimeError("SEC down")

    assert client(Down).get("/api/lab/reference/berkshire").status_code == 503
