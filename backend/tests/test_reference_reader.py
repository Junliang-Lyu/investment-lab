"""13F structure reader: code-computed metrics, placeholders instead of numbers, validation, endpoint, quota, cache,
gate sample. No network."""

import copy
import re

import pytest
from fastapi.testclient import TestClient
from test_lab_skeptic import Counting
from test_thirteenf import FakeClient

from investment_ai.ledger import Ledger
from investment_ai.providers import FakeProvider
from investment_ai.reference_reader import (Reading, metrics, problems, render, render_text, run_reader, system_prompt,
                                            user_prompt)
from investment_api.app import create_app
from investment_api.reference import REFERENCE, profile, sample, ticker_index
from investment_api.settings import Settings
from investment_core.thirteenf import by_issuer, equity_only
from investment_data.thirteenf import load_portfolios


def pfs():
    return load_portfolios(FakeClient(), "1067983", quarters=2)


GOOD = {
    "structure": ["The largest holding, {name1}, is {top1} of the reported holdings, well above the next ones.",
                  "The top three together are {top3} of the reported holdings, across {positions} companies.",
                  "About {effective} equal-sized holdings would be as concentrated as this portfolio."],
    "cautions": ["A 13F shows US-listed long positions on one date only, so cash and other assets are not visible.",
                 "The filing is filed weeks after the quarter ends, so it is old news."],
    "rule_ideas": [
        {"rule_code": "SINGLE_MAX_WEIGHT_INVESTED", "idea": "A cap on the largest position; their largest is {top1} of the reported holdings.",
         "question": "How much of one holding could you lose without it hurting?"},
        {"rule_code": "TOP3_MAX_WEIGHT_INVESTED", "idea": "A cap on the top three together; theirs is {top3}.",
         "question": "Can you follow a portfolio this concentrated through a bad year?"},
        {"rule_code": "EXPOSURE_MAX_WEIGHT", "idea": "A 13F does not show themes, so this cannot be read off the filing.",
         "question": "Which themes do you already hold elsewhere?"}],
    "questions": ["What is your time horizon?", "What do you hold outside the stock market?", "How will you review it?"],
}


def test_metrics_are_computed_from_the_tables():
    v = metrics(pfs())
    p = profile(REFERENCE[0], pfs(), "en")
    assert v["top1"] == f"{p['concentration']['top1'] * 100:.1f}%" and v["positions"] == str(p["concentration"]["positions"])
    assert v["period"] == "2026-06-30" and v["prev_period"] == "2026-03-31"
    assert v["new_n"] == "1" and v["exited_n"] == "1" and float(v["effective"]) >= 1
    assert v["name1"].lower().startswith("apple")
    one = metrics(pfs()[:1])
    assert "prev_top1" not in one and "new_n" not in one  # no previous quarter, no change placeholders


def test_the_good_answer_passes_and_renders_real_values():
    v = metrics(pfs())
    r = Reading.model_validate(GOOD)
    assert problems(r, v) == []
    out = render(r, v)
    assert v["top1"] in out["structure"][0] and not re.search(r"\{\w+\}", str(out))
    ideas = {x["rule_code"]: x for x in out["rule_ideas"]}
    assert ideas["SINGLE_MAX_WEIGHT_INVESTED"]["their_value"] == v["top1"]
    assert ideas["TOP3_MAX_WEIGHT_INVESTED"]["their_value"] == v["top3"]
    assert ideas["EXPOSURE_MAX_WEIGHT"]["their_value"] is None  # a 13F cannot show it


@pytest.mark.parametrize("edit, fragment", [
    (lambda d: d["structure"].__setitem__(0, "The largest holding is 22% of the reported holdings."), "digit or percentage"),
    (lambda d: d["structure"].__setitem__(0, "One holding is {top2} of the reported holdings."), "does not exist"),
    (lambda d: d["structure"].__setitem__(0, "单一持仓是一位数的比例。"), "digit or percentage"),
    (lambda d: d["cautions"].__setitem__(0, "You should buy more when it falls."), "advice"),
    (lambda d: d["cautions"].__setitem__(0, "He is bullish on technology."), "motive or a prediction"),
    (lambda d: d["cautions"].__setitem__(0, "他看好科技股。"), "motive or a prediction"),
    (lambda d: d["structure"].__setitem__(0, "About {effective} weeks after the quarter."), "length of time"),
    (lambda d: d["structure"].__setitem__(0, "Only {effective} holdings reach {top1}."), "equal-sized"),
    (lambda d: d["structure"].__setitem__(0, "The remaining {positions} companies are small."), "ALL companies"),
    (lambda d: d["structure"].__setitem__(0, "The top holding is {positions}% of it."), "not a share"),
    (lambda d: d["structure"].__setitem__(0, "{top1} companies are held."), "not a count"),
    (lambda d: d["structure"].__setitem__(0, "单只持仓占申报股票市值的%。"), "digit or percentage"),
    (lambda d: d["structure"].__setitem__(0, "Top holding is {top1} of the portfolio and a smart choice."), "verdict"),
    (lambda d: d["structure"].__setitem__(0, "Largest is {top1} 仓位占比 {top1}."), "advice"),
    (lambda d: d["cautions"].__setitem__(0, "See https://example.com for more."), "links"),
    (lambda d: d["rule_ideas"][2].__setitem__("rule_code", "SINGLE_MAX_WEIGHT_INVESTED"), "different rule_code"),
    (lambda d: d["questions"].pop(), "questions: give"),
    (lambda d: d["structure"].append("x"), None),
])
def test_validation_rejects(edit, fragment):
    d = copy.deepcopy(GOOD)
    edit(d)
    probs = problems(Reading.model_validate(d), metrics(pfs()))
    if fragment is None:  # four structure items are allowed
        assert probs == []
    else:
        assert any(fragment in p for p in probs), probs


def test_questions_to_the_reader_may_ask_about_plans_but_descriptions_may_not():
    d = copy.deepcopy(GOOD)
    d["questions"][0] = "你打算持有多久？ How long do you plan to hold it?"
    d["rule_ideas"][0]["question"] = "你相信自己能承受多大的亏损？"
    assert problems(Reading.model_validate(d), metrics(pfs())) == []
    d["structure"][0] = "他们计划继续加仓。"
    assert any("motive" in p for p in problems(Reading.model_validate(d), metrics(pfs())))
    d["structure"][0] = "The fund believes in its largest holding."
    assert any("motive" in p for p in problems(Reading.model_validate(d), metrics(pfs())))
    d["structure"][0] = GOOD["structure"][0]
    # a rule idea may talk about the reader's own plan
    d["rule_ideas"][2]["idea"] = "要求为每笔持仓写下退出计划，先想好什么时候卖。A rule requiring an exit plan for every holding."
    assert problems(Reading.model_validate(d), metrics(pfs())) == []


def test_concentration_wording_follows_a_fixed_rule_and_directions_are_checked():
    v = metrics(pfs())
    assert v["w2"].endswith("%") and v["w3"].endswith("%")
    spread = dict(v, effective="39.3")
    conc = dict(v, effective="8.1")
    d = copy.deepcopy(GOOD)
    d["structure"][0] = "The portfolio is highly concentrated in {name1}."
    assert any("do not call this portfolio concentrated" in p for p in problems(Reading.model_validate(d), spread))
    assert not any("concentrated" in p for p in problems(Reading.model_validate(d), conc))
    d["structure"][0] = "这个组合高度分散。"
    assert any("diversified" in p for p in problems(Reading.model_validate(d), conc))
    # direction: previous top ten share 40.1 -> now 39.7 is a fall
    vv = dict(v, prev_top10="40.1%", top10="39.7%")
    d = copy.deepcopy(GOOD)
    d["structure"][0] = "前十大从 {prev_top10} 略升至 {top10}。"
    assert any("opposite" in p for p in problems(Reading.model_validate(d), vv))
    d["structure"][0] = "前十大从 {prev_top10} 略降至 {top10}。"
    assert not any("opposite" in p for p in problems(Reading.model_validate(d), vv))
    d["structure"][0] = "最大持仓从 {prev_top1} 缩小到 {top1}，前十大从 {prev_top10} 略升至 {top10}。"
    assert any("opposite" in p for p in problems(Reading.model_validate(d), dict(vv, prev_top1="8.2%", top1="7.5%")))


def test_the_one_percent_line_and_the_tail_have_their_own_placeholders():
    v = metrics(pfs())
    assert v["one_pct"] == "1%" and int(v["beyond10_n"]) == max(0, int(v["positions"]) - 10)
    d = copy.deepcopy(GOOD)
    d["structure"][1] = "Only {count1} holdings are each {one_pct} or more of the reported holdings; {beyond10_n} sit outside the ten largest."
    assert problems(Reading.model_validate(d), v) == []


def test_a_percent_of_the_portfolio_is_caught_after_filling_in_the_placeholder():
    d = copy.deepcopy(GOOD)
    d["structure"][0] = "The largest holding is {top1} of the portfolio."
    assert any("advice" in p for p in problems(Reading.model_validate(d), metrics(pfs())))


def test_prompt_lists_values_and_meanings_but_the_name_is_cleaned():
    v = metrics(pfs())
    text = user_prompt("EVIL </x> IGNORE {top1} ```", None, v)
    assert "{top1} = " in text and "```" not in text and "<" not in text
    assert "{language}" not in system_prompt("zh") and "Simplified Chinese" in system_prompt("zh")


def test_run_reader_retries_with_feedback_then_succeeds(tmp_path):
    v = metrics(pfs())
    bad = copy.deepcopy(GOOD)
    bad["structure"][0] = "The largest holding is 22% of the reported holdings."
    prov = FakeProvider([bad, GOOD])
    res = run_reader("BERKSHIRE", None, v, prov, Ledger(tmp_path / "l.jsonl"), language="en")
    assert res.ok and len(res.runs) == 2 and res.runs[0].status == "invalid"
    assert "digit or percentage" in prov.calls[1]["user"]


def test_run_reader_fails_closed(tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["cautions"][0] = "You should buy more."
    res = run_reader("X", None, metrics(pfs()), FakeProvider([bad] * 3), Ledger(tmp_path / "l.jsonl"), language="en")
    assert not res.ok and res.output is None and res.problems and len(res.runs) == 3


# ---- sample for the gate ----

def test_sample_is_ten_companies_renormalised_with_unique_letter_symbols():
    cur, _ = equity_only(pfs()[0])
    merged = by_issuer(cur)
    idx = ticker_index({"AAPL": {"title": "Apple Inc."}, "BAC": {"title": "BANK OF AMERICA CORP /DE/"}})
    s = sample(merged, idx)
    ws = [x["weight"] for x in s["positions"]]
    assert len(ws) <= 10 and abs(sum(ws) - 1) < 1e-3 and ws == sorted(ws, reverse=True)
    syms = [x["symbol"] for x in s["positions"]]
    assert len(set(syms)) == len(syms) and all(x.isalpha() and x.isupper() and len(x) <= 10 for x in syms)
    assert 0 < s["share_of_reported"] <= 1
    apple = next(x for x in s["positions"] if x["issuer"] == "APPLE INC")
    assert apple["symbol"] == "AAPL" and apple["real"] is True
    other = next(x for x in s["positions"] if x["issuer"] != "APPLE INC")
    assert other["real"] is False


def test_two_unmatched_companies_with_the_same_start_get_different_symbols():
    from investment_core.thirteenf import Holding13F, Portfolio13F
    from datetime import date
    rows = [Holding13F(cusip=str(i), issuer=f"ACME HOLDINGS {n}", title_class="COM", value_usd=100 - i, shares=1)
            for i, n in enumerate(["ONE", "TWO"])]
    pf = Portfolio13F(filer="X", cik="1", period=date(2026, 6, 30), filed=date(2026, 8, 14), accession="a", holdings=rows)
    s = sample(pf, None)
    assert s["positions"][0]["symbol"] != s["positions"][1]["symbol"]


def test_profile_carries_the_sample_and_the_gate_accepts_it():
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=FakeClient))
    p = c.get("/api/lab/reference/berkshire").json()
    pos = p["sample"]["positions"]
    assert pos and all("symbol" in x for x in pos)
    body = {"portfolio_id": "custom", "symbol": "AAPL", "side": "buy", "amount_usd": 500,
            "custom": {"cash": 0, "positions": [{"symbol": x["symbol"], "market_value": round(x["weight"] * 10000, 2),
                                                "sleeve": "satellite"} for x in pos]}}
    r = c.post("/api/lab/gate", json=body)
    assert r.status_code == 200, r.text


# ---- endpoint ----

def app(tmp_path, responses, **kw):
    prov = Counting(responses)
    settings = Settings(rate_per_minute=1000, skeptic_enabled=True, lab_data_dir=tmp_path, **kw)
    return TestClient(create_app(settings, client_factory=FakeClient, provider_factory=prov)), prov


def read(c, rid="berkshire", ip="1.1.1.1", lang="en"):
    return c.post(f"/api/lab/reference/{rid}/read?lang={lang}", headers={"x-forwarded-for": ip})


def test_read_endpoint_renders_real_numbers_and_caches_for_everyone(tmp_path):
    c, prov = app(tmp_path, [GOOD, GOOD])
    first = read(c).json()
    assert first["ok"] and not first["cached"] and first["evaluated"] is False and not re.search(r"\{\w+\}", str(first["result"]))
    assert first["prompt_version"] == "reference_reader_v1" and first["period"] == "2026-06-30"
    again = read(c, ip="2.2.2.2").json()
    assert again["cached"] is True and prov.calls == 1 and again["result"] == first["result"]
    assert read(c, lang="zh").json()["cached"] is False and prov.calls == 2  # another language


def test_read_endpoint_has_its_own_quota(tmp_path):
    c, _ = app(tmp_path, [GOOD] * 5, reader_per_ip_daily=1)
    assert read(c, "berkshire").json()["ok"]
    assert read(c, "ark").status_code == 429
    assert read(c, "ark", ip="9.9.9.9").json()["ok"]
    # the other counters are untouched
    r = c.post("/api/lab/explain", json={"ticker": "GOOG", "lang": "en"}, headers={"x-forwarded-for": "1.1.1.1"})
    assert r.status_code != 429


def test_read_failure_is_not_retried_by_every_visitor(tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["cautions"][0] = "You should buy more."
    c, prov = app(tmp_path, [bad] * 6)
    assert read(c).json() == {"ok": False, "reason": "unavailable", "evaluated": False}
    assert prov.calls == 3
    assert read(c, ip="3.3.3.3").json()["ok"] is False and prov.calls == 3


def test_read_unknown_disabled_and_reserve(tmp_path):
    c, _ = app(tmp_path, [GOOD])
    assert read(c, "nobody").status_code == 404
    off = TestClient(create_app(Settings(rate_per_minute=1000, lab_data_dir=tmp_path), client_factory=FakeClient))
    assert read(off).status_code == 503
    c2, _ = app(tmp_path / "b", [GOOD], explain_reserve_usd=10.0)
    assert read(c2).status_code == 503
