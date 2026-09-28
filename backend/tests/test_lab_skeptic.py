"""Public Lab AI skeptic: kill switch, limits, budgets, cache, fail-closed, 30-day purge. No network."""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from test_ai import good_output
from test_api import FakeEdgar

from investment_ai.evidence import build_evidence
from investment_ai.ledger import AIRun, BudgetExceeded
from investment_ai.providers import FakeProvider
from investment_api.app import create_app
from investment_api.settings import Settings
from investment_api.skeptic import LabStore, normalize_thesis
from investment_core.financials import build_financials

THESIS = "Cloud growth can offset the profit pressure from rising capital spending."


@pytest.fixture(scope="module")
def pack():
    edgar = FakeEdgar()
    return build_evidence("GOOG", build_financials(edgar.company_facts("x"), quarters=8))


class Counting:
    """Provider factory that hands out one shared fake and counts model calls."""

    def __init__(self, responses):
        self.fake = FakeProvider(responses)

    def __call__(self):
        return self.fake

    @property
    def calls(self):
        return len(self.fake.calls)


def make(tmp_path, provider, **kw):
    settings = Settings(rate_per_minute=1000, skeptic_enabled=True, lab_data_dir=tmp_path, **kw)
    return TestClient(create_app(settings, client_factory=FakeEdgar, provider_factory=provider))


def post(c, thesis=THESIS, ip="1.1.1.1", **kw):
    return c.post("/api/lab/skeptic", json={"ticker": "GOOG", "thesis": thesis, "lang": "en", **kw},
                  headers={"x-forwarded-for": ip})


def test_disabled_by_default(tmp_path):
    c = TestClient(create_app(Settings(rate_per_minute=1000, lab_data_dir=tmp_path), client_factory=FakeEdgar))
    assert c.get("/api/lab/skeptic/status").json()["enabled"] is False
    assert post(c).status_code == 503


def test_missing_api_key_disables(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = TestClient(create_app(Settings(rate_per_minute=1000, skeptic_enabled=True, lab_data_dir=tmp_path),
                              client_factory=FakeEdgar))
    body = c.get("/api/lab/skeptic/status").json()
    assert body["enabled"] is False and "not configured" in body["reason"]


def test_ok_answer_with_sources_then_cached(tmp_path, pack):
    prov = Counting([good_output(pack)])
    c = make(tmp_path, prov)
    r = post(c)
    body = r.json()
    assert r.status_code == 200 and body["ok"] and not body["cached"], body
    assert len(body["result"]["bear_case"]) == 3 and body["result"]["computed"] == []
    src = next(iter(body["result"]["evidence"].values()))
    assert src["display"].startswith("$") and src["source"].startswith("https://www.sec.gov/")
    again = post(c, thesis="  " + THESIS.upper() + "  ", ip="2.2.2.2")  # same thesis, other visitor
    assert again.json()["cached"] is True and prov.calls == 1


def test_invalid_three_times_fails_closed(tmp_path, pack):
    bad = copy.deepcopy(good_output(pack))
    bad["weakest_assumption"] = "Investors should buy before earnings."
    prov = Counting([bad, bad, bad])  # the Lab allows three attempts, then fails closed
    body = post(make(tmp_path, prov)).json()
    assert body["ok"] is False and "result" not in body and body["checks"]["advice"] >= 1 and prov.calls == 3


def test_retry_then_ok(tmp_path, pack):
    bad = copy.deepcopy(good_output(pack))
    bad["bull_case"][0]["claim"] = "Revenue reached $999.00B."
    prov = Counting([bad, good_output(pack)])
    body = post(make(tmp_path, prov)).json()
    assert body["ok"] and body["attempts"] == 2
    assert "failed validation" in prov.fake.calls[1]["user"]


def test_per_visitor_daily_limit(tmp_path, pack):
    prov = Counting([good_output(pack)] * 5)
    c = make(tmp_path, prov, skeptic_per_ip_daily=2)
    assert post(c, thesis=THESIS + " A").status_code == 200
    assert post(c, thesis=THESIS + " B").status_code == 200
    r = post(c, thesis=THESIS + " C")
    assert r.status_code == 429 and "Daily limit" in r.json()["detail"]
    assert post(c, thesis=THESIS + " C", ip="9.9.9.9").status_code == 200  # other visitor unaffected
    assert c.get("/api/lab/skeptic/status", headers={"x-forwarded-for": "1.1.1.1"}).json()["visitor_remaining"] == 0


def test_budget_exhausted(tmp_path, pack):
    prov = Counting([good_output(pack)])
    r = post(make(tmp_path, prov, lab_daily_budget_usd=0.0))
    assert r.status_code == 503 and "budget" in r.json()["detail"] and prov.calls == 0


def test_input_rules(tmp_path, pack):
    c = make(tmp_path, Counting([good_output(pack)]))
    assert post(c, thesis="too short").status_code == 422
    assert post(c, thesis="x" * 401).status_code == 422
    assert c.post("/api/lab/skeptic", json={"ticker": "IBKR", "thesis": THESIS}).status_code == 404
    assert normalize_thesis("a\x00b\n\n  c​") == "a b c​"


def test_store_budgets_and_purge(tmp_path):
    now = [datetime(2026, 9, 28, 12, tzinfo=timezone.utc)]
    store = LabStore(tmp_path / "s.db", daily_budget=0.5, monthly_budget=5, per_ip_daily=5, clock=lambda: now[0])
    run = lambda cost, at=None: AIRun(surface="lab", task="t", provider="p", model="m", prompt_version="v",
                                      input_hash="h", input={}, status="ok", cost_usd=cost, at=at or now[0])
    store.record(run(0.45))
    store.check(0.04)
    with pytest.raises(BudgetExceeded, match="daily"):
        store.check(0.06)
    now[0] = datetime(2026, 9, 29, 1, tzinfo=timezone.utc)  # new day: daily resets, monthly keeps counting
    store.check(0.4)
    for day in range(10, 19):  # earlier days this month
        store.record(run(0.5, datetime(2026, 9, day, tzinfo=timezone.utc)))
    with pytest.raises(BudgetExceeded, match="monthly"):
        store.check(0.1)

    ip = store.ip_hash("1.2.3.4")
    assert "1.2.3.4" not in ip and ip == store.ip_hash("1.2.3.4")
    now[0] += timedelta(days=1)
    assert store.ip_hash("1.2.3.4") != ip  # rotates daily
    now[0] -= timedelta(days=1)
    store.add_request(id="old", at=(now[0] - timedelta(days=31)).isoformat(), ip_hash=ip, ticker="GOOG", lang="en",
                      thesis="an old visitor thesis", cache_key="k", cached=0, status="ok", cost=0.01, attempts=1,
                      output=json.dumps({}))
    store.add_request(id="new", at=now[0].isoformat(), ip_hash=ip, ticker="GOOG", lang="en",
                      thesis="a recent visitor thesis", cache_key="k2", cached=0, status="ok", cost=0.01, attempts=1,
                      output=json.dumps({}))
    assert store.purge() == 1
    assert store.cached("k") is None and store.cached("k2") == {"output": {}, "model": None, "prompt_version": None}
    assert store.spent_this_month() > 4.9  # spending survives the purge


def test_lab_pack_retrieves_filing_text_and_renders_quote_sources(pack):
    from investment_ai.lab_pack import LabCompany, lab_pack, thesis_terms
    from investment_api.skeptic import render
    from investment_core.filing_text import Passage
    passages = [Passage(source_id=f"acc:item1a:{i}", item="item1a", url="https://www.sec.gov/doc.htm",
                        text=t) for i, t in enumerate([
                            "Our energy storage business depends on battery supply and faces intense competition.",
                            "Automotive gross margin decreased because of lower average selling prices.",
                            "We lease office space in several cities around the world."])]
    company = LabCompany(ticker="GOOG", pack=pack, passages=passages, members=["Energy Generation and Storage"])
    assert "energy storage" in thesis_terms("汽车利润下滑时，储能能撑起增长", [])
    chosen = lab_pack(company, "汽车利润下滑时，储能能撑起增长").passages
    assert chosen and chosen[0].text.startswith("Our energy storage") and len(chosen) <= 12
    out = good_output(pack)
    out["bull_case"][1]["quotes"] = [{"source_id": "acc:item1a:0", "text": passages[0].text}]
    body = render(out, lab_pack(company, "储能 汽车"), "en")
    assert body["sources"]["acc:item1a:0"] == {"document": "10-K Item 1A Risk Factors", "url": "https://www.sec.gov/doc.htm"}
