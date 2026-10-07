"""Quarter explainer on the company page, and the AI skeptic / memo for companies outside the curated list."""

import copy

import pytest
from test_ai import good_output
from test_lab_skeptic import THESIS, Counting, make, pack, post  # noqa: F401  (pack is a fixture)

from investment_ai.explain import EXPLAIN_VERSION, explain_pack, latest_period


def explain(c, ticker="GOOG", ip="1.1.1.1", lang="en"):
    return c.post("/api/lab/explain", json={"ticker": ticker, "lang": lang}, headers={"x-forwarded-for": ip})


def test_explain_is_cached_per_company_and_quarter(tmp_path, pack):
    prov = Counting([good_output(pack)] * 2)
    c = make(tmp_path, prov)
    body = explain(c).json()
    assert body["ok"] and not body["cached"] and body["evaluated"] is True, body
    assert body["prompt_version"] == EXPLAIN_VERSION and body["period"]
    assert len(body["result"]["bear_case"]) == 3 and "computed" in body["result"]
    assert all(i["threshold"] is None for i in body["result"]["invalidation_suggestions"])  # no model-made levels
    again = explain(c, ip="2.2.2.2").json()  # other visitor, same company: no new model call
    assert again["ok"] and again["cached"] is True and prov.calls == 1
    assert again["result"]["bear_case"] == body["result"]["bear_case"]
    assert explain(c, lang="zh").json()["cached"] is False and prov.calls == 2  # another language, another explanation


def test_explain_failure_is_not_retried_by_every_visitor(tmp_path, pack):
    bad = copy.deepcopy(good_output(pack))
    bad["weakest_assumption"] = "Investors should buy before earnings."
    prov = Counting([bad, bad, bad, bad, bad, bad])
    c = make(tmp_path, prov)
    first = explain(c).json()
    assert first["ok"] is False and first["reason"] == "unavailable" and prov.calls == 3
    again = explain(c, ip="3.3.3.3").json()
    assert again["ok"] is False and prov.calls == 3  # no new model calls for the same company and quarter


def test_explain_has_its_own_quota_and_does_not_use_the_skeptic_quota(tmp_path, pack):
    prov = Counting([good_output(pack)] * 10)
    c = make(tmp_path, prov, explain_per_ip_daily=1, skeptic_per_ip_daily=1)
    assert explain(c, "GOOG").json()["ok"]
    assert explain(c, "MSFT").status_code == 429  # one new explanation per visitor
    assert post(c).json()["ok"]  # but the skeptic quota is untouched


def test_explain_stops_when_the_day_budget_is_mostly_used(tmp_path, pack):
    c = make(tmp_path, Counting([good_output(pack)]), explain_reserve_usd=10.0)  # reserve above the daily budget
    assert explain(c).status_code == 503


def test_explain_unknown_and_bad_tickers(tmp_path, pack):
    c = make(tmp_path, Counting([good_output(pack)]))
    assert explain(c, "XYZQ").status_code == 404
    assert explain(c, "1; drop").status_code == 422


def test_skeptic_and_memo_are_open_to_custom_tickers(tmp_path, pack):
    prov = Counting([good_output(pack)])
    c = make(tmp_path, prov)
    r = c.post("/api/lab/skeptic", json={"ticker": "orcl", "thesis": THESIS, "lang": "en"},
               headers={"x-forwarded-for": "1.1.1.1"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["evaluated"] is False, body
    assert c.post("/api/lab/skeptic", json={"ticker": "XYZQ", "thesis": THESIS, "lang": "en"}).status_code == 404
    m = c.post("/api/lab/memos", json={"ticker": "ORCL", "thesis": THESIS, "lang": "en"},
               headers={"x-forwarded-for": "1.1.1.1"})
    assert m.status_code == 200 and m.json()["ticker"] == "ORCL"


def test_custom_company_loads_are_bounded(tmp_path, pack):
    prov = Counting([good_output(pack)] * 10)
    c = make(tmp_path, prov, custom_per_ip_daily=1)
    assert explain(c, "ORCL").json()["ok"]
    assert explain(c, "BRK-B").status_code == 429  # one new custom company per visitor and day, curated ones are free
    assert explain(c, "GOOG").json()["ok"]
    assert explain(c, "ORCL").json()["cached"]  # already loaded: no second cold load


def test_explain_pack_and_period(pack):
    from investment_ai.lab_pack import LabCompany
    company = LabCompany(ticker="GOOG", pack=pack)
    p = explain_pack(company)
    assert latest_period(p) == max(i.period_end for i in pack.items)


def test_cli_explain_quarter(pack, tmp_path, capsys):
    from test_api import FakeEdgar

    from investment_ai import cli as ai_cli
    from investment_ai.ledger import Ledger
    from investment_ai.providers import FakeProvider
    rc = ai_cli.main(["explain-quarter", "GOOG", "--lang", "en"], provider=FakeProvider([good_output(pack)]),
                     client=FakeEdgar(), ledger=Ledger(tmp_path / "r.jsonl"))
    out = capsys.readouterr().out
    assert rc == 0 and "ok=True" in out and "WENT WELL" in out and "WATCH" in out
    run = Ledger(tmp_path / "r.jsonl").runs()[0]
    assert run.prompt_version == EXPLAIN_VERSION


def test_explain_rejects_made_up_levels_and_retries(tmp_path, pack):
    bad = copy.deepcopy(good_output(pack))
    bad["invalidation_suggestions"][1]["condition"] = "Cloud growth falls below 80% year over year"
    prov = Counting([bad, good_output(pack)])
    body = explain(make(tmp_path, prov)).json()
    assert body["ok"] and prov.calls == 2, body
    assert not any(ch.isdigit() for i in body["result"]["invalidation_suggestions"] for ch in i["condition"])


def test_plain_sentence_cannot_add_a_first_or_a_record():
    from investment_ai.plain import new_claim_words
    from investment_ai.validate import Claim
    c = Claim(claim="Free cash flow turned negative in the quarter.", type="fact")
    assert new_claim_words("Cash left after purchases turned negative for the first time.", c)
    assert new_claim_words("现金流首次转负。", c)
    assert not new_claim_words("Cash left after purchases turned negative.", c)
    first = Claim(claim="Revenue reached a record level.", type="fact")
    assert not new_claim_words("Sales hit a record.", first)


def test_plain_layer_for_the_explainer_sees_only_the_claim(pack):
    from investment_ai.plain import new_claim_words, user_prompt
    from investment_ai.validate import ResearchSkeptic
    out = ResearchSkeptic.model_validate(good_output(pack))
    assert "why_it_matters" in user_prompt(out) or "breaks_assumption" in user_prompt(out)
    p = user_prompt(out, reasoning=False)
    assert "why_it_matters" not in p and "breaks_assumption" not in p
    from investment_ai.validate import Claim
    c = Claim(claim="Operating cash flow rose.", type="fact")
    assert new_claim_words("Cash from operations surged.", c) and not new_claim_words("Cash from operations rose.", c)


def test_plain_sentence_cannot_add_a_reason_or_a_verdict():
    from investment_ai.plain import new_claim_words
    from investment_ai.validate import Claim
    c = Claim(claim="净利润与营业利润的比率从90.2%升至275.2%。", type="fact")
    assert new_claim_words("净利润大幅增长，但增长主要来自投资等非日常业务项目。", c)
    assert new_claim_words("现金状况出现压力。", c)
    assert new_claim_words("成本控制有所改善。", c)
    assert new_claim_words("Profit rose mainly because of one-off items.", c)
    assert not new_claim_words("净利润比营业利润大得多。", c)
    given = Claim(claim="Revenue grew, driven mainly by cloud.", type="fact")
    assert not new_claim_words("Revenue grew, driven mainly by cloud.", given)


def test_watch_next_cannot_name_a_level_in_words(pack):
    from investment_ai.explain import watch_problems
    from investment_ai.validate import ResearchSkeptic
    out = ResearchSkeptic.model_validate(good_output(pack))
    assert not watch_problems(out)
    for text in ("Google Cloud收入增速放缓至个位数", "growth falls to single-digit levels", "增速降到两位数以下"):
        out.invalidation_suggestions[0].condition = text
        assert watch_problems(out), text
