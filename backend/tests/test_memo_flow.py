"""Steps 5-7 of the memo SOP: parse the user's answers, review them, finalise."""

import json
from datetime import date

import pytest
from conftest import ctx as make_ctx, sat, snap
from test_ai import FIX, THESIS, FakeClient, good_output

from investment_ai import cli as ai_cli
from investment_ai import memo_status
from investment_ai.evidence import build_evidence
from investment_ai.ledger import Ledger
from investment_ai.memo_draft import render_memo
from investment_ai.memo_finalize import finalize
from investment_ai.memo_parse import parse_memo
from investment_ai.memo_review import (enforce_minimums, position_check, run_memo_review, validate_review)
from investment_ai.providers import FakeProvider
from investment_ai.theses import examples
from investment_ai.validate import validate_output
from investment_core.financials import build_financials
from investment_core.importers import load_rule_set
from investment_core.memo import TransitionError
from investment_core.models import MemoDecision

from conftest import FIXTURES


@pytest.fixture(scope="module")
def pack():
    fin = build_financials(json.loads(FIX.read_text(encoding="utf-8")), quarters=8)
    return fin, build_evidence("GOOG", fin)


def filled_memo(pack, conditions=("营业利润率连续两个季度低于 30%", "资本开支占经营现金流比连续两个季度超过 100%",
                                  "收入同比连续两个季度低于 15%"), e3="估值依旧低"):
    fin, p = pack
    obj, _ = validate_output(good_output(p), p, THESIS)
    md = render_memo("GOOG", THESIS, fin, p, obj, language="zh", today=date(2026, 9, 28))
    md = md.replace("我考虑配置这个标的，是因为：\n1.\n2.\n3.\n目标仓位占净值：____%",
                    "我考虑配置这个标的，是因为：\n1.收入增长在加快\n2.现金储备充足\n3.\n目标仓位占净值：_10___%")
    md = md.replace("```text\n1.\n2.\n3.\n```\n\nAI 建议", "```text\n" + "\n".join(f"{i}.{c}" for i, c in enumerate(conditions, 1))
                    + "\n```\n\nAI 建议")
    md = md.replace("E1: 我的回应：", "E1: 我的回应：接受风险，最多容忍两个季度").replace("E2: 我的回应：", "E2: 我的回应：同意")
    md = md.replace("E3: 我的回应：", f"E3: 我的回应：{e3}")
    md = md.replace("下次复盘日期：", "下次复盘日期：2026-10-30").replace("复盘重点：", "复盘重点：资本开支指引")
    return md


def review_json(verdicts=("risk_accepted", "not_refuted", "not_refuted")):
    return {
        "section_a": {"verdict": "pass", "issues": []},
        "responses": [{"counter": f"E{i}", "verdict": v, "comment": "说明"} for i, v in enumerate(verdicts, 1)],
        "fact_vs_inference": "现金储备充足需要来源。",
        "section_c": {"verdict": "clear", "issues": []},
        "section_d": {"verdict": "ok", "comment": "财报后复盘。"},
        "summary": "基本完整。",
    }


def test_parse_filled_memo(pack):
    pm = parse_memo(filled_memo(pack, e3=""), today=date(2026, 9, 28))
    assert pm.ticker == "GOOG" and pm.one_liner == THESIS and len(pm.bear) == 3
    assert pm.reasons == ["收入增长在加快", "现金储备充足"] and pm.target_weight == pytest.approx(0.10)
    assert pm.responses["E1"].startswith("接受风险") and pm.responses["E3"] == ""
    assert len(pm.invalidation) == 3 and pm.review_date == date(2026, 10, 30) and pm.review_focus == "资本开支指引"


def test_placeholder_conditions_not_counted(pack):
    pm = parse_memo(filled_memo(pack, conditions=("营业利润率低于 30%", "暂时没想好", "")))
    assert pm.invalidation == ["营业利润率低于 30%"] and len(pm.invalidation_raw) == 3


def test_review_validation(pack):
    pm = parse_memo(filled_memo(pack))
    review, rep = validate_review(review_json(), pm, pack[1])
    assert rep.ok
    bad = review_json(); bad["summary"] = "估值合理，建议买入。"
    assert validate_review(bad, pm, pack[1])[1].forbidden
    bad = review_json(); bad["summary"] = "市盈率只有 18.5 倍。"
    assert validate_review(bad, pm, pack[1])[1].ungrounded
    bad = review_json(); bad["responses"] = bad["responses"][::-1]
    assert not validate_review(bad, pm, pack[1])[1].ok


def test_code_enforced_minimums(pack):
    pm = parse_memo(filled_memo(pack, conditions=("营业利润率低于 30%", "暂时没想好", ""), e3=""))
    review, _ = validate_review(review_json(("refuted", "refuted", "refuted")), pm, pack[1])
    fixed = enforce_minimums(review, pm)
    assert fixed.responses[2].verdict == "not_refuted"  # E3 was empty
    assert fixed.section_c.verdict == "needs_revision" and "只有 1 条" in fixed.section_c.issues[0]


def test_run_review_with_retry(pack, tmp_path):
    pm = parse_memo(filled_memo(pack))
    bad = review_json(); bad["summary"] = "建议加仓"
    res = run_memo_review(pm, pack[1], FakeProvider([bad, review_json()]), Ledger(tmp_path / "r.jsonl"))
    assert res.ok and [r.status for r in res.runs] == ["invalid", "ok"] and res.unresolved


def test_position_check_uses_rule_engine(demo_rules):
    s = snap(8_000, ("GOOG", 2_000))
    c = make_ctx(sat("GOOG", "us_megacap_tech"))
    lines = position_check("GOOG", 0.25, s, demo_rules, c)
    assert "目标 25%" in lines[0] and any("SINGLE" in l or "limit" in l for l in lines[1:])
    assert "不需要加仓检查" in position_check("GOOG", 0.10, s, demo_rules, c)[1]
    assert "跳过" in position_check("GOOG", 0.25, None, None, None)[0]


def test_finalize_state_machine(pack):
    pm = parse_memo(filled_memo(pack))
    with pytest.raises(TransitionError):  # unresolved review + eligible without a reason
        finalize(pm, MemoDecision.ELIGIBLE_FOR_GATE, review_passed=False, reason=None)
    m = finalize(pm, MemoDecision.ELIGIBLE_FOR_GATE, review_passed=False, reason="accept the open points")
    assert m.decision == MemoDecision.ELIGIBLE_FOR_GATE and m.events[-1].override_reason
    assert finalize(pm, MemoDecision.WATCHLIST, review_passed=False, reason=None).decision == MemoDecision.WATCHLIST
    short = parse_memo(filled_memo(pack, conditions=("营业利润率低于 30%", "暂时没想好", "")))
    with pytest.raises(TransitionError) as e:
        finalize(short, MemoDecision.WATCHLIST, review_passed=True, reason=None)
    assert any("§C" in x for x in e.value.missing)


def test_cli_review_and_finalize(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(memo_status, "DEFAULT_STATUS", tmp_path / "memo_status.yaml")
    memo = tmp_path / "GOOG_memo.md"
    memo.write_text(filled_memo(pack), encoding="utf-8")
    rj = tmp_path / "review.json"; rj.write_text(json.dumps(review_json()), encoding="utf-8")
    ledger = Ledger(tmp_path / "r.jsonl")
    assert ai_cli.main(["memo-review", str(memo), "--from-json", str(rj), "--no-fundamentals"], client=FakeClient(), ledger=ledger) == 0
    assert ai_cli.main(["memo-review", str(memo), "--from-json", str(rj), "--no-fundamentals"], client=FakeClient(), ledger=ledger) == 0
    md = memo.read_text(encoding="utf-8")
    assert md.count("## Step 6 AI 审查") == 1 and "§B E2：未驳倒" in md and "跳过仓位检查" in md
    st = memo_status.load(tmp_path / "memo_status.yaml")["GOOG"]
    assert st["status"] == "reviewed" and st["has_unresolved_review_issues"] is True and st["invalidation_count"] == 3
    assert ai_cli.main(["memo-finalize", str(memo), "--decision", "eligible_for_gate"]) == 2
    assert ai_cli.main(["memo-finalize", str(memo), "--decision", "paper"]) == 0
    assert "## Step 7 定稿" in memo.read_text(encoding="utf-8")
    assert memo_status.load(tmp_path / "memo_status.yaml")["GOOG"]["decision"] == "paper"


def test_memo_status_feeds_rule_engine(tmp_path, draft_rules):
    from investment_core import evaluate
    from investment_core.importers import load_context
    path = tmp_path / "memo_status.yaml"
    memo_status.upsert("GOOG", path=path, status="final", decision="paper", invalidation_count=3)
    c = load_context(path)
    s = snap(8_800, ("GOOG", 1_200))
    found = [v.rule_code for v in evaluate(s, draft_rules, c.model_copy(update={"assets": {"GOOG": sat("GOOG")}}))]
    assert "MEMO_REQUIRED_ABOVE" not in found and "MISSING_INVALIDATION" not in found


def test_example_theses():
    items = examples("GOOG", "Alphabet Inc.", "zh")
    assert items[0]["id"] == "cloud-offsets-capex" and any("Alphabet Inc." in i["text"] for i in items)
    assert all("{company}" not in i["text"] for i in examples("XYZ", "X Corp", "en"))


def test_empty_response_blocks_finalize(pack):
    pm = parse_memo(filled_memo(pack, e3=""))
    with pytest.raises(TransitionError) as e:
        finalize(pm, MemoDecision.WATCHLIST, review_passed=True, reason=None)
    assert any("§B" in x for x in e.value.missing)


def test_review_output_repairs(pack):
    """Tool-use slips seen with Haiku: fields written as markup inside a string, objects as JSON strings."""
    import json as _json
    from investment_ai.memo_review import review_schema, validate_review
    pm = parse_memo(filled_memo(pack))
    good = review_json()
    slipped = {k: good[k] for k in ("section_a", "responses")}
    slipped["fact_vs_inference"] = (good["fact_vs_inference"] + "</fact_vs_inference>\n"
                                    f'<parameter name="section_c">{_json.dumps(good["section_c"], ensure_ascii=False)}\n'
                                    f'<parameter name="section_d">{_json.dumps(good["section_d"], ensure_ascii=False)}\n'
                                    f'<parameter name="summary">{good["summary"]}')
    review, report = validate_review(slipped, pm, pack[1])
    assert report.ok and review.section_c.verdict == "clear" and review.summary == good["summary"]
    assert review.fact_vs_inference == good["fact_vs_inference"]
    as_strings = {**good, "section_a": _json.dumps(good["section_a"]), "responses": _json.dumps(good["responses"])}
    assert validate_review(as_strings, pm, pack[1])[1].ok
    assert "$defs" not in _json.dumps(review_schema())


def test_review_flat_shape_text_block_and_numbers(pack):
    from investment_ai.memo_review import review_schema, validate_review
    pm = parse_memo(filled_memo(pack))
    g = review_json()
    flat = {"section_a_verdict": "pass", "section_a_issues": [], "responses": g["responses"],
            "fact_vs_inference": g["fact_vs_inference"], "section_c_verdict": "needs_revision",
            "section_c_issues": ["Say which margin, e.g. operating margin below 25%, not 30%."],
            "section_d_verdict": "ok", "section_d_comment": "2026-10-29 is after the next earnings report.",
            "summary": "OK."}
    review, report = validate_review(flat, pm, pack[1])
    assert report.ok and review.section_c.verdict == "needs_revision" and review.section_d.verdict == "ok"
    assert set(review_schema()["properties"]) >= {"section_a_verdict", "responses", "summary"}
    # Fields the model wrote in a text block after the tool input are recovered.
    only_a = {k: flat[k] for k in ("section_a_verdict", "section_a_issues")}
    text = "".join(f'<parameter name="{k}">{json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}\n'
                   for k, v in flat.items() if k not in only_a)
    assert validate_review(only_a, pm, pack[1], text)[1].ok
    # An unsupported figure stated as a fact is still rejected; a suggested threshold is not.
    bad = {**flat, "summary": "Cloud margin is 57.3% this quarter."}
    assert "57.3%" in validate_review(bad, pm, pack[1])[1].ungrounded
    ok = {**flat, "summary": "A clearer condition would be, for example, cloud margin below 20%."}
    assert validate_review(ok, pm, pack[1])[1].ok
