"""LLM layer: evidence pack, validator, task runner, providers, memo rendering.

No real API calls: providers are faked. Evidence comes from the recorded
public GOOG filing fixture.
"""

import json
from pathlib import Path

import pytest

from investment_ai import cli as ai_cli
from investment_ai.evidence import build_evidence
from investment_ai.ledger import AIRun, Ledger
from investment_ai.memo_draft import render_memo
from investment_ai.providers import AnthropicProvider, FakeProvider, GeminiProvider
from investment_ai.research import run_research_skeptic
from investment_ai.validate import extract_numbers, forbidden_hits, validate_output
from investment_core.financials import build_financials

FIX = Path(__file__).parent / "fixtures" / "edgar" / "GOOG_companyfacts.json"
THESIS = "Cloud growth keeps overall margins stable while AI capex rises"


@pytest.fixture(scope="module")
def fin():
    return build_financials(json.loads(FIX.read_text(encoding="utf-8")), quarters=8)


@pytest.fixture(scope="module")
def pack(fin):
    return build_evidence("GOOG", fin)


def item(pack, fact_suffix):
    return next(i for i in pack.items if i.fact_id.endswith(fact_suffix))


def good_output(pack):
    rev = item(pack, ":revenue:2026-06-30")
    yoy = item(pack, ":revenue_yoy:2026-06-30")
    capex = item(pack, ":capex:2026-06-30")
    fcf = item(pack, ":fcf:2026-06-30")
    out = {
        "thesis_restated": "Cloud growth offsets rising AI spending.",
        "bull_case": [
            {"claim": f"Revenue reached {rev.display}, up {yoy.display} year over year.", "type": "fact",
             "evidence_refs": [rev.fact_id, yoy.fact_id]},
            {"claim": "Scale lets the company fund heavy investment internally.", "type": "inference", "evidence_refs": []},
        ],
        "bear_case": [
            {"claim": f"Capex of {capex.display} pushed free cash flow to {fcf.display}.", "type": "fact",
             "evidence_refs": [capex.fact_id, fcf.fact_id], "breaks_assumption": "Spending stays affordable"},
            {"claim": "Search share could erode to AI assistants.", "type": "to_verify", "evidence_refs": [],
             "breaks_assumption": "Core cash engine is stable"},
            {"claim": "Regulatory remedies may limit distribution deals.", "type": "to_verify", "evidence_refs": [],
             "breaks_assumption": "Distribution advantages persist"},
        ],
        "weakest_assumption": "That AI capex earns an adequate return.",
        "invalidation_suggestions": [
            {"condition": "Free cash flow stays negative", "observable_metric": "quarterly FCF", "threshold": "two quarters"},
            {"condition": "Revenue growth slows sharply", "observable_metric": "revenue YoY", "threshold": None},
            {"condition": "Operating margin falls", "observable_metric": "operating margin", "threshold": None},
        ],
        "verify_questions": [
            {"question": "What drove the jump in net income versus operating income?", "where_to_check": "10-Q other income note"},
            {"question": "How is capex split between servers and buildings?", "where_to_check": "earnings call"},
        ],
    }
    plain = ["It spends a lot on equipment and the cash coming in has not caught up yet.",
             "People may use AI assistants instead of searching, which would hurt its main business.",
             "Regulators could force changes to the deals that put its search in front of people."]
    for c, text, cat in zip(out["bear_case"], plain, ["cash conversion", "AI assistants", "regulation"]):
        c["plain_summary"], c["angle"] = text, cat
    for c, text, cat in zip(out["bull_case"], ["Sales keep growing quickly.", "It is big enough to pay for its own bets."],
                            ["revenue growth", "balance sheet"]):
        c["plain_summary"], c["angle"] = text, cat
    return out


# --- evidence -----------------------------------------------------------------

def test_evidence_pack_has_values_and_growth(pack):
    rev = item(pack, ":revenue:2026-06-30")
    assert rev.display == "$119.80B" and rev.source.startswith("https://www.sec.gov/Archives/")
    assert item(pack, ":revenue_yoy:2026-06-30").unit == "ratio"
    assert "fact_id | period" in pack.to_prompt_table()


# --- number extraction and forbidden content ------------------------------------------

def test_extract_numbers_units_and_exemptions():
    nums = extract_numbers("Revenue $119.80B in FY2026 Q2 (2026-06-30), margin 61.6%, 1198亿, 3 risks since 2025, 10-K")
    kinds = {(n.kind, round(n.value, 4)) for n in nums}
    assert ("usd", 119.8e9) in kinds and ("ratio", 0.616) in kinds and ("usd", 1198e8) in kinds
    assert len(nums) == 3


def test_forbidden_hits():
    assert forbidden_hits("We recommend buying now")
    assert forbidden_hits("建议买入，目标价 200")
    assert forbidden_hits("price target of $300")
    assert not forbidden_hits("The sell-off in 2022 hurt sentiment; investors held on.")


# --- validator ------------------------------------------------------------------

def test_valid_output_passes(pack):
    obj, report = validate_output(good_output(pack), pack, THESIS)
    assert report.ok, report.feedback()
    assert len(obj.bear_case) == 3


def test_ungrounded_number_flagged(pack):
    out = good_output(pack)
    out["bull_case"][1]["claim"] = "Search has 90% market share and trades at 25x earnings."
    _, report = validate_output(out, pack, THESIS)
    assert not report.ok and "90%" in report.ungrounded and "25x" in report.ungrounded


def test_thesis_numbers_may_be_restated(pack):
    out = good_output(pack)
    out["thesis_restated"] = "Cloud growth above 30% offsets AI spending."
    assert not validate_output(out, pack, THESIS)[1].ok
    assert validate_output(out, pack, "I expect cloud growth above 30%")[1].ok


def test_advice_flagged(pack):
    out = good_output(pack)
    out["weakest_assumption"] = "估值合理，建议买入。"
    _, report = validate_output(out, pack, THESIS)
    assert not report.ok and report.forbidden


def test_fact_needs_known_refs(pack):
    out = good_output(pack)
    out["bull_case"][0]["evidence_refs"] = ["made-up-id"]
    out["bear_case"][1]["type"] = "fact"
    _, report = validate_output(out, pack, THESIS, relabel=False)
    assert "made-up-id" in report.bad_refs and any("fact without" in b for b in report.bad_refs)


def test_schema_errors(pack):
    out = good_output(pack)
    out["bear_case"] = out["bear_case"][:2]
    obj, report = validate_output(out, pack, THESIS)
    assert obj is None and report.errors


# --- task runner ------------------------------------------------------------------

def test_retry_then_success(pack, tmp_path):
    bad = good_output(pack)
    bad["weakest_assumption"] = "Trades at 25x earnings."
    fake = FakeProvider([bad, good_output(pack)])
    ledger = Ledger(tmp_path / "runs.jsonl", monthly_budget_usd=5)
    res = run_research_skeptic(pack, THESIS, fake, ledger, language="en")
    assert res.ok and [r.status for r in res.runs] == ["invalid", "ok"]
    assert "failed validation" in fake.calls[1]["user"] and "25x" in fake.calls[1]["user"]
    assert len(ledger.runs()) == 2


def test_fails_closed(pack, tmp_path):
    bad = good_output(pack)
    bad["weakest_assumption"] = "建议买入"
    res = run_research_skeptic(pack, THESIS, FakeProvider([bad, bad]), Ledger(tmp_path / "r.jsonl"))
    assert not res.ok and res.output is None and res.report.forbidden


def test_thesis_required(pack, tmp_path):
    with pytest.raises(ValueError):
        run_research_skeptic(pack, "  ", FakeProvider([]), Ledger(tmp_path / "r.jsonl"))


def test_budget_blocks_before_calling(pack, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PRICE_IN", "1000")
    fake = FakeProvider([good_output(pack)])
    res = run_research_skeptic(pack, THESIS, fake, Ledger(tmp_path / "r.jsonl", monthly_budget_usd=0.01))
    assert not res.ok and res.runs[0].status == "budget_blocked" and fake.calls == []


def test_ledger_monthly_spend(tmp_path):
    ledger = Ledger(tmp_path / "r.jsonl", monthly_budget_usd=1)
    base = dict(surface="private", task="t", provider="p", model="m", prompt_version="v", input_hash="h",
                input={}, status="ok")
    ledger.record(AIRun(cost_usd=0.4, **base))
    ledger.record(AIRun(cost_usd=0.5, **base))
    assert ledger.spent_this_month() == pytest.approx(0.9)
    with pytest.raises(Exception):
        ledger.check(0.2)


# --- providers ------------------------------------------------------------------

def test_anthropic_request_and_parse():
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return json.dumps({"model": "claude-x", "content": [{"type": "tool_use", "name": "submit", "input": {"a": 1}}],
                           "usage": {"input_tokens": 100, "output_tokens": 50}}).encode()

    p = AnthropicProvider(api_key="k", model="claude-x", post=post)
    res = p.complete_json("sys", "user", {"type": "object"}, 500)
    assert res.data == {"a": 1} and res.tokens_in == 100
    assert seen["headers"]["x-api-key"] == "k" and seen["body"]["tool_choice"]["name"] == "submit"
    assert res.cost_usd((1.0, 5.0)) == pytest.approx((100 * 1 + 50 * 5) / 1e6)


def test_gemini_request_and_parse():
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(url=url, headers=headers)
        return json.dumps({"candidates": [{"content": {"parts": [{"text": '{"a": 2}'}]}}],
                           "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}).encode()

    res = GeminiProvider(api_key="g", model="gemini-x", post=post).complete_json("s", "u", {}, 100)
    assert res.data == {"a": 2} and "key=" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "g"


def test_missing_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(Exception):
        AnthropicProvider()
    with pytest.raises(Exception):
        GeminiProvider()


# --- memo rendering and CLI ---------------------------------------------------------

def test_render_memo_layout(fin, pack):
    obj, _ = validate_output(good_output(pack), pack, THESIS)
    md = render_memo("GOOG", THESIS, fin, pack, obj, language="zh")
    for n in range(1, 13):
        assert f"## {n}." in md
    assert "§A" in md and "§B" in md and "§C" in md and "§D" in md
    assert "E1: 我的回应：" in md and "不构成投资建议" in md
    assert "https://www.sec.gov/Archives/" in md and "[事实]" in md and "[待验证]" in md


class FakeClient:
    def cik_for(self, t):
        return "0001652044"

    def company_facts(self, cik):
        return json.loads(FIX.read_text(encoding="utf-8"))


def test_cli_memo_draft(pack, tmp_path, capsys):
    rc = ai_cli.main(["memo-draft", "GOOG", "--thesis", THESIS, "--out", str(tmp_path), "--lang", "en", "--no-fundamentals"],
                     provider=FakeProvider([good_output(pack)]), client=FakeClient(),
                     ledger=Ledger(tmp_path / "r.jsonl"))
    assert rc == 0
    files = list(tmp_path.glob("GOOG_memo_draft_*.md"))
    assert files and "## 5." in files[0].read_text(encoding="utf-8")


def test_cli_dry_run(capsys):
    assert ai_cli.main(["memo-draft", "GOOG", "--thesis", THESIS, "--dry-run", "--no-fundamentals"], client=FakeClient()) == 0
    out = capsys.readouterr().out
    assert "<thesis>" in out and "EVIDENCE" in out


def test_claim_number_must_match_its_own_refs(pack):
    out = good_output(pack)
    other = item(pack, ":revenue:2025-06-30")
    out["bull_case"][0]["claim"] = f"Revenue reached {other.display}."  # refs point at 2026-06-30
    _, report = validate_output(out, pack, THESIS)
    assert other.display in report.ungrounded


def test_uncited_table_value_is_pointed_at_in_the_feedback(pack):
    """A number that is in the table but not cited (and whose period is not named) must be reported with its
    fact_id, not as "not in the EVIDENCE table" -- the model cannot fix that message."""
    out = good_output(pack)
    other = item(pack, ":revenue:2025-06-30")
    out["bull_case"][0]["claim"] = f"Revenue reached {other.display}."
    _, report = validate_output(out, pack, THESIS)
    assert not report.ok and other.display in report.ungrounded
    assert any(other.fact_id in h for h in report.ungrounded_hints)
    text = report.feedback()
    assert other.fact_id in text and "evidence_refs" in text
    assert f"These numbers are not in the EVIDENCE table: {other.display}" not in text


def test_number_missing_from_the_table_keeps_the_old_feedback(pack):
    out = good_output(pack)
    out["bull_case"][1]["claim"] = "Search has 90% market share."
    _, report = validate_output(out, pack, THESIS)
    assert "90%" in report.ungrounded and not report.ungrounded_hints
    assert "not in the EVIDENCE table: 90%" in report.feedback()


def test_refs_copied_with_their_period_are_cleaned(pack):
    """"fact_id | FY2026 Q3" (the table row copied) is the same reference as "fact_id"."""
    out = good_output(pack)
    rev = item(pack, ":revenue:2026-06-30")
    short = pack.short_id(rev.fact_id)
    out["bull_case"][0]["evidence_refs"] = [f"{short} | {rev.fiscal_label}", f"`{rev.fact_id}`",
                                            item(pack, ":revenue_yoy:2026-06-30").fact_id]
    obj, report = validate_output(out, pack, THESIS)
    assert report.ok, report.feedback()
    assert obj.bull_case[0].evidence_refs.count(rev.fact_id) == 1
    assert not report.bad_refs


def test_forbidden_feedback_says_not_to_mention_the_words(pack):
    out = good_output(pack)
    out["bull_case"][1]["claim"] = "The request for a price target cannot be checked here."
    _, report = validate_output(out, pack, THESIS)
    assert not report.ok and report.forbidden
    assert "not even to say" in report.feedback()


def test_suggested_thresholds_are_exempt(pack):
    out = good_output(pack)
    out["invalidation_suggestions"][1]["threshold"] = "below 15% for two quarters"
    assert validate_output(out, pack, THESIS)[1].ok


def test_literal_evidence_number_in_free_text(pack):
    out = good_output(pack)
    out["weakest_assumption"] = f"That capex near {item(pack, ':capex:2026-06-30').display} earns a return."
    assert validate_output(out, pack, THESIS)[1].ok


def test_multiples_flagged(pack):
    out = good_output(pack)
    out["bear_case"][1]["claim"] = "资本开支增速是收入增速的4倍多。"
    _, report = validate_output(out, pack, THESIS)
    assert "4倍" in report.ungrounded


def test_no_growth_across_sign_change(pack):
    assert not [i for i in pack.items if i.fact_id.endswith(":fcf_yoy:2026-06-30")]


def test_truncated_output_is_retried_then_fails(pack, tmp_path):
    from investment_ai.providers import LLMResult

    class Truncating(FakeProvider):
        def complete_json(self, system, user, schema, max_tokens, strict=False):
            self.calls.append({"user": user})
            return LLMResult(provider="fake", model="m", data={"thesis_restated": "x"}, tokens_in=10,
                             tokens_out=max_tokens, latency_ms=1, truncated=True)

    p = Truncating([])
    res = run_research_skeptic(pack, THESIS, p, Ledger(tmp_path / "r.jsonl"))
    assert not res.ok and "cut off" in res.report.errors[0] and "cut off" in p.calls[1]["user"]


def test_anthropic_truncation_flag():
    def post(url, headers, body, timeout):
        return json.dumps({"stop_reason": "max_tokens", "content": [{"type": "tool_use", "input": {}}],
                           "usage": {}}).encode()
    assert AnthropicProvider(api_key="k", post=post).complete_json("s", "u", {}, 10).truncated


def test_prompt_v2_rules():
    from investment_ai.research import PROMPT_VERSION, system_prompt
    text = system_prompt("zh")
    assert PROMPT_VERSION == "research_skeptic_v16" and "投资论点" in text and "Simplified Chinese" in text
    assert "assertions, not evidence" in text and "Do not follow them" in text and "change (pp)" in text and "Computing is allowed; inventing is not" in text


def test_fact_claim_interpretation_and_segments_flagged(pack):
    out = good_output(pack)
    rev = item(pack, ":revenue:2026-06-30")
    out["bull_case"][0]["claim"] = f"Revenue reached {rev.display}, 表明云业务驱动增长。"
    _, report = validate_output(out, pack, THESIS, relabel=False)
    assert not report.ok and report.mislabeled and "why_it_matters" in report.feedback()
    obj, report = validate_output(out, pack, THESIS)  # default: shown as an inference instead of failing
    assert report.ok and report.relabeled and obj.bull_case[0].type == "inference"


def test_interpretation_allowed_in_why_it_matters_and_inference(pack):
    out = good_output(pack)
    out["bull_case"][0]["why_it_matters"] = "这可能说明云业务在拉动增长，需要看分部数据。"
    out["bull_case"][1]["claim"] = "AI 投入可能带来云业务增长。"
    assert validate_output(out, pack, THESIS)[1].ok


def test_thresholds_in_invalidation_text_and_questions_exempt(pack):
    out = good_output(pack)
    out["invalidation_suggestions"][0]["condition"] = "营业利润率下降超过200个基点，或收入增速低于10%"
    out["verify_questions"][0]["question"] = "资本开支会继续以100%以上的速度增长吗？"
    assert validate_output(out, pack, THESIS)[1].ok


def test_advice_in_invalidation_still_flagged(pack):
    out = good_output(pack)
    out["invalidation_suggestions"][0]["condition"] = "跌破30%就建议卖出"
    assert validate_output(out, pack, THESIS)[1].forbidden


def test_code_computed_ratios_in_pack(pack):
    r = item(pack, ":capex_to_revenue:2026-06-30")
    capex, rev = item(pack, ":capex:2026-06-30").value, item(pack, ":revenue:2026-06-30").value
    assert r.value == pytest.approx(capex / rev) and r.display.endswith("%")
    assert item(pack, ":fcf_margin:2026-06-30").value < 0


def test_why_it_matters_rendered(fin, pack):
    out = good_output(pack)
    out["bull_case"][0]["why_it_matters"] = "规模带来投入能力。"
    obj, _ = validate_output(out, pack, THESIS)
    assert "[推断] 规模带来投入能力。" in render_memo("GOOG", THESIS, fin, pack, obj, language="zh")


def test_ranges_and_cited_rounding_in_reasoning(pack):
    out = good_output(pack)
    rev_yoy = item(pack, ":revenue_yoy:2026-06-30")  # cited by bull_case[0]
    rounded = f"{round(rev_yoy.value * 100)}%"
    out["weakest_assumption"] = f"收入增速（约 {rounded}）能否持续。"
    assert validate_output(out, pack, THESIS)[1].ok
    out["weakest_assumption"] = "收入增速 5-6% 就足够。"  # not cited, not literal
    assert not validate_output(out, pack, THESIS)[1].ok


def test_why_it_matters_may_use_literal_pack_values(pack):
    out = good_output(pack)
    out["bull_case"][1]["why_it_matters"] = f"资本开支占收入比已到 {item(pack, ':capex_to_revenue:2026-06-30').display}。"
    assert validate_output(out, pack, THESIS)[1].ok


def test_prompt_table_uses_labels(pack):
    table = pack.to_prompt_table("zh")
    assert "资本开支占经营现金流比" in table and "收入同比" in table
    assert "revenue YoY" in pack.to_prompt_table("en")


def test_cli_memo_from_run(pack, tmp_path, capsys):
    ledger = Ledger(tmp_path / "r.jsonl")
    res = run_research_skeptic(pack, THESIS, FakeProvider([good_output(pack)]), ledger)
    rid = res.runs[-1].id
    assert ai_cli.main(["memo-from-run", rid, "--out", str(tmp_path)], client=FakeClient(), ledger=ledger) == 0
    assert list(tmp_path.glob(f"GOOG_memo_draft_*_{rid[:8]}.md"))
    assert ai_cli.main(["memo-from-run", "nope", "--out", str(tmp_path)], client=FakeClient(), ledger=ledger) == 1


def test_earnings_quality_ratio(pack):
    r = item(pack, ":net_to_operating_income:2026-06-30")
    assert r.value > 2  # net income far above operating income in FY2026 Q2


def test_cli_from_json(pack, tmp_path):
    f = tmp_path / "out.json"
    f.write_text(json.dumps(good_output(pack)), encoding="utf-8")
    ledger = Ledger(tmp_path / "r.jsonl")
    assert ai_cli.main(["memo-draft", "GOOG", "--thesis", THESIS, "--from-json", str(f), "--out", str(tmp_path), "--no-fundamentals"],
                       client=FakeClient(), ledger=ledger) == 0
    run = ledger.runs()[-1]
    assert run.provider == "claude-session" and run.cost_usd == 0 and run.status == "ok"
    bad = good_output(pack); bad["weakest_assumption"] = "建议买入"
    f.write_text(json.dumps(bad), encoding="utf-8")
    assert ai_cli.main(["memo-draft", "GOOG", "--thesis", THESIS, "--from-json", str(f), "--out", str(tmp_path), "--no-fundamentals"],
                       client=FakeClient(), ledger=ledger) == 2


def test_percentage_point_changes_are_citable(pack):
    pp = [i for i in pack.items if i.unit == "pp"]
    assert pp and all(i.display.endswith(" pp") for i in pp)
    gm = next(i for i in pp if i.metric == "operating_margin_chg_yoy" and i.period_end == "2026-06-30")
    out = good_output(pack)
    out["bull_case"][0] = {"claim": f"Operating margin changed by {abs(gm.value):.1f} percentage points year over year "
                                    f"in {gm.fiscal_label}.", "type": "fact", "evidence_refs": [gm.fact_id]}
    assert validate_output(out, pack, THESIS)[1].ok
    recent = pack.recent(5)
    assert len({i.period_end for i in recent.items}) == 5 and len(recent.items) < len(pack.items)
