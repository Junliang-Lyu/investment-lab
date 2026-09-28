from datetime import date

import pytest
from conftest import FIXTURES, codes

from investment_core import evaluate, evaluate_trade
from investment_core.cli import main
from investment_core.gate import BUY_ATTESTATIONS
from investment_core.importers import load_assets, load_portfolio, parse_simple_csv
from investment_core.models import TradeProposal
from investment_core.review import build_review

DEMOS = sorted((FIXTURES / "demo_portfolios").glob("*.json"))


def test_three_demo_portfolios_exist():
    assert {p.stem for p in DEMOS} == {"index-core", "concentrated-tech", "cash-heavy-starter"}


@pytest.mark.parametrize("path", DEMOS, ids=lambda p: p.stem)
def test_demo_portfolios_are_fictional_and_consistent(path):
    s, c, meta = load_portfolio(path)
    assert meta["fictional"] is True
    assert s.net_liquidation == pytest.approx(s.cash + s.invested)
    for p in s.positions:
        assert p.symbol in c.assets, f"{p.symbol} missing from assets"


def test_index_core_is_clean(demo_rules):
    s, c, _ = load_portfolio(FIXTURES / "demo_portfolios" / "index-core.json")
    assert evaluate(s, demo_rules, c) == []


def test_concentrated_tech_shows_breaks(demo_rules):
    s, c, _ = load_portfolio(FIXTURES / "demo_portfolios" / "concentrated-tech.json")
    found = codes(evaluate(s, demo_rules, c))
    for expected in ("SATELLITE_MAX_WEIGHT", "SINGLE_MAX_WEIGHT_NAV", "MEMO_REQUIRED_ABOVE",
                     "EXPOSURE_MAX_WEIGHT", "REVIEW_OVERDUE", "MISSING_INVALIDATION"):
        assert expected in found


def test_concentrated_tech_watchlisted_amzn_passes_paper_checks(demo_rules):
    s, c, _ = load_portfolio(FIXTURES / "demo_portfolios" / "concentrated-tech.json")
    r = evaluate_trade(s, TradeProposal(symbol="AMZN", amount_usd=800), demo_rules, c)
    it = {i.key: i for i in r.items}
    assert it["paper_weeks"].status == "pass" and it["paper_reviews"].status == "pass"
    assert it["concentration:EXPOSURE_MAX_WEIGHT"].status == "fail"  # theme gets more concentrated


def test_cash_heavy_new_position_fails_paper_checks(demo_rules):
    s, c, _ = load_portfolio(FIXTURES / "demo_portfolios" / "cash-heavy-starter.json")
    r = evaluate_trade(s, TradeProposal(symbol="V", amount_usd=1_200, attestations={k: True for k in BUY_ATTESTATIONS}),
                       demo_rules, c)
    it = {i.key: i for i in r.items}
    assert it["paper_weeks"].status == "fail"
    assert it["first_live_size"].status == "fail"
    assert it["memo_reviewed"].status == "fail"  # memo only at user_responded
    assert r.overall == "rule_breaks"


def test_simple_csv():
    text = """symbol,quantity,market_value,unrealized_pnl
AAAA,10,"1,500",-20
CASH,,8500,
"""
    s = parse_simple_csv(text, date(2026, 9, 25))
    assert s.cash == 8_500 and s.net_liquidation == 10_000
    assert s.positions[0].market_value == 1_500 and s.positions[0].unrealized_pnl == -20


def test_simple_csv_explicit_nav_and_bad_header():
    s = parse_simple_csv("symbol,quantity,market_value\nNAV,,10050\nCASH,,50\nAAAA,1,10000", date(2026, 9, 25))
    assert s.net_liquidation == 10_050
    with pytest.raises(ValueError):
        parse_simple_csv("ticker,value\nA,1", date(2026, 9, 25))


def test_review_report_sections(demo_rules):
    s, c, _ = load_portfolio(FIXTURES / "demo_portfolios" / "concentrated-tech.json")
    md = build_review(s, demo_rules, c)
    for heading in ("## Account snapshot", "## Holdings", "## Concentration and exposure",
                    "## Drawdown scenarios", "## Rule findings", "## Holding thesis re-test", "## Next review focus"):
        assert heading in md
    assert "NVDA falls 30%: net value -7.5%" in md


@pytest.mark.parametrize("cmd", ["evaluate", "review"])
def test_cli_runs(cmd, capsys):
    rc = main([cmd, "--portfolio", str(FIXTURES / "demo_portfolios" / "concentrated-tech.json"),
               "--rules", str(FIXTURES / "rules" / "demo.yaml")])
    assert rc == 0 and capsys.readouterr().out.strip()


def test_cli_gate_json(capsys):
    rc = main(["gate", "--portfolio", str(FIXTURES / "demo_portfolios" / "index-core.json"),
               "--rules", str(FIXTURES / "rules" / "demo.yaml"), "--symbol", "msft", "--amount", "500",
               "--attest", "not_fomo=yes", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and '"symbol": "MSFT"' in out


def test_load_assets(tmp_path):
    f = tmp_path / "assets.yaml"
    f.write_text("assets:\n  - {symbol: AAAA, sleeve: satellite, exposure_tags: [t1]}\n", encoding="utf-8")
    assets = load_assets(f)
    assert assets["AAAA"].sleeve.value == "satellite" and assets["AAAA"].exposure_tags == ["t1"]


def test_cli_context_and_previous(tmp_path, capsys):
    prev = tmp_path / "prev.json"
    prev.write_text('{"as_of": "2026-06-01", "net_liquidation": 10000, "cash": 9000,'
                    ' "positions": [{"symbol": "AAAA", "quantity": 10, "market_value": 1000}]}', encoding="utf-8")
    now = tmp_path / "now.json"
    now.write_text('{"as_of": "2026-09-25", "net_liquidation": 10000, "cash": 8000,'
                   ' "positions": [{"symbol": "AAAA", "quantity": 20, "market_value": 2000}]}', encoding="utf-8")
    ctx_file = tmp_path / "context.yaml"
    ctx_file.write_text("assets:\n  - {symbol: AAAA, sleeve: satellite, exposure_tags: [t1]}\n"
                        "last_review_date: 2026-09-20\n", encoding="utf-8")
    rc = main(["evaluate", "--portfolio", str(now), "--rules", str(FIXTURES / "rules" / "v0_draft.yaml"),
               "--context", str(ctx_file), "--previous", str(prev)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "UNCHECKED_TRADE" in out
    assert "UNCLASSIFIED_POSITION" not in out and "REVIEW_OVERDUE" not in out
