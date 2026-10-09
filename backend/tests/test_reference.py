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

    class Counting(FakeClient):
        def submissions(self, cik):
            made.append(1)
            return super().submissions(cik)

    def factory():
        return Counting()

    c = client(factory)
    assert c.get("/api/lab/reference/berkshire").status_code == 200
    after_first = len(made)
    assert c.get("/api/lab/reference/berkshire?lang=zh").status_code == 200
    assert after_first > 0 and len(made) == after_first  # the second visit (other language) reads no filing again: served from memory

    class Down:
        def submissions(self, cik):
            raise RuntimeError("SEC down")

    assert client(Down).get("/api/lab/reference/berkshire").status_code == 503


# ---- directory, search data, filers outside the list, gate limits ----

def test_directory_has_names_aliases_and_every_cik_once():
    ids = [r["id"] for r in REFERENCE]
    assert len(set(ids)) == len(ids) and len({r["cik"] for r in REFERENCE}) == len(REFERENCE)
    d = client().get("/api/lab/reference").json()
    by = {x["id"]: x for x in d}
    assert "方舟" in by["ark"]["aliases"] and "段永平" in by["hh"]["aliases"] and "李录" in by["himalaya"]["aliases"]
    assert all(x["about"]["en"] and x["about"]["zh"] for x in d)
    assert by["berkshire"]["has_sources"] and not by["hh"]["has_sources"]


def test_sources_are_their_own_publications_over_https():
    for r in REFERENCE:
        for s in r["sources"]:
            assert s["url"].startswith("https://") and s["title"]["en"] and s["title"]["zh"] and s["kind"]
    p = profile(REFERENCE[0], berkshire(), "zh")
    assert p["sources"][0]["url"].startswith("https://www.berkshirehathaway.com/") and p["about"] and p["custom"] is False
    hh = profile(next(r for r in REFERENCE if r["id"] == "hh"), berkshire(), "zh")
    assert hh["sources"] == [] and "不转引" in hh["note"]


def test_profile_gives_the_numbers_the_page_edits():
    p = profile(REFERENCE[0], berkshire(), "en")
    assert p["draft_values"] == {"single_max": 0.22, "top3_max": 0.52}


def test_lookup_by_cik_of_a_filer_outside_the_list():
    c = client()
    r = c.get("/api/lab/reference/cik/1067983?lang=en")  # a listed filer: served as such, no quota used
    assert r.status_code == 200 and r.json()["custom"] is False and r.json()["name"] == "Berkshire Hathaway"
    assert c.get("/api/lab/reference/cik/abc").status_code == 422
    assert c.get("/api/lab/reference/cik/12345678901").status_code == 422


def test_lookup_by_cik_unknown_filer_and_quota():
    c = client(FakeClient, custom_per_ip_daily=1)
    r = c.get("/api/lab/reference/cik/999")  # not in the list: the fake SEC answers with Berkshire's tables
    assert r.status_code == 200 and r.json()["custom"] is True and r.json()["sources"] == [] and r.json()["about"] is None
    r2 = c.get("/api/lab/reference/cik/998")
    assert r2.status_code == 429  # one cold lookup per day in this test


def test_lookup_by_cik_without_13f_filings_is_404():
    class NoFilings(FakeClient):
        def filings(self, cik, forms):
            return []

    assert client(NoFilings).get("/api/lab/reference/cik/12345").status_code == 404


def gate(c, **extra):
    body = {"portfolio_id": "concentrated-tech", "symbol": "AMZN", "side": "buy", "amount_usd": 800}
    return c.post("/api/lab/gate", json={**body, **extra})


def test_gate_limits_replace_the_two_concentration_numbers_only():
    c = client()
    base = gate(c).json()
    tight = gate(c, limits={"single_max": 0.05, "top3_max": 0.2}).json()
    assert tight["rule_set_version"].endswith("+your-limits") and base["rule_set_version"] == "demo-1"
    item = lambda body, key: next(i for i in body["items"] if i["key"] == key)
    key = "concentration:SINGLE_MAX_WEIGHT_INVESTED"
    assert item(base, key)["status"] == "pass"
    assert item(tight, key)["status"] == "fail" and "limit 5.0%" in item(tight, key)["detail"]
    loose = gate(c, limits={"single_max": 0.9, "top3_max": 0.95}).json()
    assert item(loose, key)["status"] == "pass"
    # the rest of the checklist is untouched
    other = lambda body: [(i["key"], i["status"]) for i in body["items"] if "INVESTED" not in i["key"]]
    assert other(tight) == other(base)
    assert gate(c, limits={"single_max": 0.01}).status_code == 422  # absurdly low limits are refused
    assert gate(c, limits={"top3_max": 1.5}).status_code == 422
    assert gate(c, limits={}).json()["rule_set_version"] == "demo-1"
