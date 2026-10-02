"""Lab memo workflow (DESIGN §11.5): thesis -> skeptic -> §A-§D -> AI review -> decision -> gate. No network."""

import copy
from datetime import date, timedelta

import pytest
from test_ai import good_output
from test_lab_skeptic import THESIS, Counting, make, pack, post  # noqa: F401  (shared fixtures)
from test_memo_flow import review_json

from investment_ai.memo_parse import parse_memo

ANSWERS = {
    "reasons": ["Cloud revenue is growing faster than the company overall.", "Net cash covers two years of capex."],
    "target_weight_pct": 5,
    "responses": {"E1": "Accept the risk for at most two quarters; reconsider if FCF stays negative.",
                  "E2": "Search revenue still grew last quarter.", "E3": "Remedies take years to take effect."},
    "invalidation": ["Free cash flow negative for two quarters in a row", "Cloud operating margin below 20%",
                     "Search revenue declines year over year"],
    "review_date": str(date.today() + timedelta(days=40)),
    "review_focus": "Capex guidance and cloud margin",
}


def start(tmp_path, pack, extra=()):
    prov = Counting([good_output(pack), *extra])
    c = make(tmp_path, prov)
    assert post(c).json()["ok"]
    r = c.post("/api/lab/memos", json={"ticker": "GOOG", "thesis": THESIS, "lang": "en"},
               headers={"x-forwarded-for": "1.1.1.1"})
    assert r.status_code == 200, r.text
    return c, prov, r.json()


def test_memo_needs_the_servers_own_skeptic_result(tmp_path, pack):
    c = make(tmp_path, Counting([]))
    r = c.post("/api/lab/memos", json={"ticker": "GOOG", "thesis": "A thesis nobody has run through the skeptic.",
                                       "lang": "en"})
    assert r.status_code == 409


def test_full_workflow_to_the_gate(tmp_path, pack):
    c, prov, m = start(tmp_path, pack, [review_json(("refuted", "risk_accepted", "refuted"))])
    assert m["status"] == "skeptic_done" and len(m["skeptic"]["bear_case"]) == 3 and m["skeptic"]["evidence"]
    assert "§A reasons" in m["missing"] and len(m["id"]) >= 20

    # A partial save is kept as a draft; the complete one moves the memo to user_responded.
    part = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "invalidation": ANSWERS["invalidation"][:2]}).json()
    assert part["status"] == "skeptic_done" and any("§C" in x for x in part["missing"])
    m = c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS).json()
    assert m["status"] == "user_responded" and m["missing"] == []

    r = c.post(f"/api/lab/memos/{m['id']}/review", headers={"x-forwarded-for": "1.1.1.1"}).json()
    assert r["ok"] and r["memo"]["status"] == "reviewed" and not r["memo"]["review_unresolved"]
    assert prov.calls == 2

    m = c.post(f"/api/lab/memos/{m['id']}/finalize", json={"decision": "eligible_for_gate"}).json()
    assert m["status"] == "final" and m["decision"] == "eligible_for_gate"

    # The gate reads this memo: memo items pass; with no memo the same trade fails them.
    body = {"portfolio_id": "cash-heavy-starter", "symbol": "GOOG", "amount_usd": 400}
    with_memo = c.post("/api/lab/gate", json={**body, "memo_id": m["id"]}).json()
    items = {i["key"]: i["status"] for i in with_memo["items"]}
    assert with_memo["memo_status"] == "final"
    assert items["memo_reviewed"] == "pass" and items["memo_decision"] == "pass" and items["invalidation"] == "pass"
    without = {i["key"]: i["status"] for i in c.post("/api/lab/gate", json=body).json()["items"]}
    assert without["memo_reviewed"] == "fail"
    # A memo about another company cannot be used.
    assert c.post("/api/lab/gate", json={**body, "symbol": "MSFT", "memo_id": m["id"]}).status_code == 422

    # Markdown export uses the SOP layout and parses back with the local tools.
    md = c.get(f"/api/lab/memos/{m['id']}/markdown").text
    pm = parse_memo(md)
    assert pm.ticker == "GOOG" and pm.one_liner == THESIS and len(pm.bear) == 3
    assert pm.target_weight == pytest.approx(0.05) and len(pm.invalidation) == 3 and pm.responses["E2"]
    assert "Step 6" in md and "Step 7" in md

    # Final memos are read-only until reopened as a new version.
    assert c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS).status_code == 409
    m = c.post(f"/api/lab/memos/{m['id']}/reopen").json()
    assert m["status"] == "user_responded" and m["version"] == 2 and m["decision"] is None and m["review"] is None


def test_editing_after_review_clears_it(tmp_path, pack):
    c, prov, m = start(tmp_path, pack, [review_json()])
    c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS)
    assert c.post(f"/api/lab/memos/{m['id']}/review").json()["memo"]["status"] == "reviewed"
    again = c.post(f"/api/lab/memos/{m['id']}/review").json()  # already reviewed: no second model call
    assert again["cached"] and prov.calls == 2
    m = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "review_focus": "Something else"}).json()
    assert m["status"] == "user_responded" and m["review"] is None
    m = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "responses": {}}).json()
    assert m["status"] == "skeptic_done"


def test_unresolved_review_needs_a_reason_to_go_to_the_gate(tmp_path, pack):
    c, _, m = start(tmp_path, pack, [review_json()])  # E2 and E3 not refuted
    c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS)
    m = c.post(f"/api/lab/memos/{m['id']}/review").json()["memo"]
    assert m["review_unresolved"]
    r = c.post(f"/api/lab/memos/{m['id']}/finalize", json={"decision": "eligible_for_gate"})
    assert r.status_code == 422
    ok = c.post(f"/api/lab/memos/{m['id']}/finalize", json={"decision": "eligible_for_gate",
                                                            "reason": "Small starter position; revisit after earnings."})
    assert ok.json()["status"] == "final"


def test_skipping_the_review_needs_a_reason(tmp_path, pack):
    c, prov, m = start(tmp_path, pack)
    c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS)
    assert c.post(f"/api/lab/memos/{m['id']}/finalize", json={"decision": "paper"}).status_code == 422
    m = c.post(f"/api/lab/memos/{m['id']}/finalize", json={"decision": "paper", "reason": "Paper only."}).json()
    assert m["status"] == "final" and m["watch_started"] == str(date.today()) and prov.calls == 1
    # Paper decided today: the gate's paper-to-live checks see 0 weeks.
    g = c.post("/api/lab/gate", json={"portfolio_id": "cash-heavy-starter", "symbol": "GOOG", "amount_usd": 300,
                                     "memo_id": m["id"]}).json()
    weeks = next(i for i in g["items"] if i["key"] == "paper_weeks")
    assert weeks["status"] == "fail" and "0.0" in weeks["detail"]


def test_invalid_review_is_not_stored(tmp_path, pack):
    bad = review_json()
    bad["summary"] = "Investors should buy before earnings."
    c, prov, m = start(tmp_path, pack, [bad, bad, bad])  # the Lab allows three attempts, then fails closed
    c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS)
    r = c.post(f"/api/lab/memos/{m['id']}/review").json()
    assert r["ok"] is False and r["checks"]["advice"] >= 1 and prov.calls == 4
    assert c.get(f"/api/lab/memos/{m['id']}").json()["status"] == "user_responded"


def test_review_limits(tmp_path, pack):
    c, prov, m = start(tmp_path, pack, [review_json()])
    c.put(f"/api/lab/memos/{m['id']}/answers", json=ANSWERS)
    store_limit = make(tmp_path, prov, memo_review_per_ip_daily=0)
    assert store_limit.post(f"/api/lab/memos/{m['id']}/review").status_code == 429
    assert c.post(f"/api/lab/memos/{m['id']}/review").status_code == 200


def test_input_limits_and_ids(tmp_path, pack):
    c, _, m = start(tmp_path, pack)
    assert c.get("/api/lab/memos/not-a-real-id").status_code == 404
    assert c.get("/api/lab/memos/" + "A" * 22).status_code == 404
    too_long = {**ANSWERS, "responses": {"E1": "x" * 1001}}
    assert c.put(f"/api/lab/memos/{m['id']}/answers", json=too_long).status_code == 422
    assert c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "target_weight_pct": 150}).status_code == 422
    assert c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "invalidation": ["a"] * 7}).status_code == 422


def test_create_limit_delete_and_retention(tmp_path, pack):
    c, _, m = start(tmp_path, pack)
    limited = make(tmp_path, Counting([]), memo_per_ip_daily=1)
    r = limited.post("/api/lab/memos", json={"ticker": "GOOG", "thesis": THESIS, "lang": "en"},
                     headers={"x-forwarded-for": "1.1.1.1"})
    assert r.status_code == 429
    assert c.delete(f"/api/lab/memos/{m['id']}").json()["deleted"]
    assert c.get(f"/api/lab/memos/{m['id']}").status_code == 404


def test_store_purges_untouched_memos(tmp_path, pack):
    from datetime import datetime, timezone
    from investment_api.memo_lab import MemoStore
    from investment_api.skeptic import LabStore
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    lab = LabStore(tmp_path / "x.sqlite3", daily_budget=1, monthly_budget=5, per_ip_daily=3, clock=lambda: now[0])
    ms = MemoStore(lab, retention_days=180)
    ms.insert({"id": "A" * 22, "created_at": now[0].isoformat(), "updated_at": now[0].isoformat(), "ip_hash": "h",
               "ticker": "GOOG", "lang": "en", "thesis": "t", "skeptic": "{}", "skeptic_meta": "{}", "answers": "{}",
               "state": "{}"})
    now[0] = now[0] + timedelta(days=179)
    assert ms.purge() == 0 and ms.get("A" * 22)
    now[0] = now[0] + timedelta(days=2)
    assert ms.purge() == 1 and ms.get("A" * 22) is None


def test_custom_portfolio_is_checked_not_stored(tmp_path, pack):
    c = make(tmp_path, Counting([]))
    body = {"portfolio_id": "custom", "symbol": "NVDA", "amount_usd": 2000,
            "custom": {"cash": 3000, "positions": [{"symbol": "voo", "market_value": 6000, "sleeve": "core"},
                                                   {"symbol": "NVDA", "market_value": 1000}]}}
    g = c.post("/api/lab/gate", json=body).json()
    assert g["weight_before"] == pytest.approx(0.1) and g["weight_after"] == pytest.approx(0.3)
    assert any(i["key"].startswith("concentration:") and i["status"] == "fail" for i in g["items"])
    assert c.post("/api/lab/gate", json={**body, "custom": None}).status_code == 422
    bad = copy.deepcopy(body)
    bad["custom"]["positions"][0]["symbol"] = "not a ticker!"
    assert c.post("/api/lab/gate", json=bad).status_code == 422


def test_review_date_must_be_plausible(tmp_path, pack):
    c, _, m = start(tmp_path, pack)
    r = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "review_date": "111111-11-05"})
    assert r.status_code == 422
    r = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "review_date": "2099-01-01"})
    assert r.status_code == 422 and "five years" in r.text


def test_bearish_thesis_flows_through(tmp_path, pack):
    prov = Counting([good_output(pack), review_json()])
    c = make(tmp_path, prov)
    thesis = "GOOG margins are close to their peak and growth will slow."
    r = c.post("/api/lab/skeptic", json={"ticker": "GOOG", "thesis": thesis, "lang": "en", "stance": "short"})
    assert r.json()["ok"] and "Thesis direction: bearish" in prov.fake.calls[0]["user"]
    # The cache is per direction: the same words as a bullish thesis are a different question.
    assert c.post("/api/lab/memos", json={"ticker": "GOOG", "thesis": thesis, "lang": "en"}).status_code == 409
    m = c.post("/api/lab/memos", json={"ticker": "GOOG", "thesis": thesis, "lang": "en", "stance": "short"}).json()
    assert m["stance"] == "short"
    m = c.put(f"/api/lab/memos/{m['id']}/answers", json={**ANSWERS, "target_weight_pct": 0}).json()
    assert m["status"] == "user_responded" and m["answers"]["target_weight_pct"] == 0
    c.post(f"/api/lab/memos/{m['id']}/review")
    main_calls = [c for c in prov.fake.calls if "summaries" not in c["schema"].get("properties", {})]
    assert "Thesis direction: bearish" in main_calls[1]["user"]
    pm = parse_memo(c.get(f"/api/lab/memos/{m['id']}/markdown").text)
    assert pm.stance == "short" and pm.target_weight == 0
