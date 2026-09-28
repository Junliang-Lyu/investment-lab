"""Filing narrative: HTML to text, sections, retrieval, quotes in claims."""

import json
from datetime import date
from pathlib import Path

import pytest

from investment_ai.evidence import build_evidence
from investment_ai.memo_draft import render_memo
from investment_ai.validate import validate_output
from investment_core.filing_text import (Passage, build_passages, html_to_text, paragraphs, quote_found, retrieve,
                                         split_sections)
from investment_core.financials import build_financials
from test_ai import THESIS, good_output

FIX = Path(__file__).parent / "fixtures" / "edgar"
LONG = " ".join(["word"] * 30)

HTML = f"""<html><body>
<div style="display:none"><ix:header>HIDDEN HEADER TEXT</ix:header></div>
<p>Table of Contents</p><p>Item 1. Business</p><p>Item 1A. Risk Factors</p><p>Item 7. Management's Discussion</p>
<p>ITEM 1. BUSINESS</p>
<p>In Other Bets, our fully autonomous driving technology company, Waymo, is now providing fully autonomous, paid ride-hailing services to customers in multiple cities. {LONG}</p>
<table><tr><td>Revenue 123,456 should be skipped</td></tr></table>
<p>Short line.</p>
<p>ITEM 1A. RISK FACTORS</p>
<p>We face intense competition in AI, and our investments in technical infrastructure may not generate the returns we expect. {LONG}</p>
<p>ITEM 1B. UNRESOLVED STAFF COMMENTS</p><p>None. {LONG}</p>
<p>ITEM 7. MANAGEMENT&#8217;S DISCUSSION AND ANALYSIS</p>
<p>We continue to invest in capital expenditures as we scale our technical infrastructure, in particular for AI. {LONG}</p>
<p>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES</p><p>Market risk text. {LONG}</p>
</body></html>"""


def test_html_to_text_skips_tables_and_hidden():
    text = html_to_text(HTML)
    assert "HIDDEN HEADER" not in text and "should be skipped" not in text and "Waymo" in text


def test_sections_ignore_table_of_contents():
    secs = split_sections(html_to_text(HTML))
    assert "Waymo" in secs["item1"] and "intense competition" in secs["item1a"]
    assert "capital expenditures" in secs["item7"] and "Market risk" not in secs["item7"]
    assert "None." not in secs["item1a"]


def test_passages_and_retrieval():
    ps = build_passages(HTML, "0000000000-26-000001", "https://example/doc.htm")
    assert [p.source_id for p in ps] == ["0000000000-26-000001:item1:1", "0000000000-26-000001:item1a:1",
                                         "0000000000-26-000001:item7:1"]
    top = retrieve(ps, ["Waymo autonomous"], k=1)
    assert top[0].item == "item1" and retrieve(ps, ["zzzz"]) == []
    assert paragraphs("short\n\n" + LONG) == [LONG]


def test_quote_found_normalizes_typography():
    text = "Management’s view — we continue to invest in capital expenditures."
    assert quote_found("management's view - we continue to invest", text)
    assert not quote_found("we plan to cut capital expenditures", text)


@pytest.fixture(scope="module")
def pack_with_text():
    fin = build_financials(json.loads((FIX / "GOOG_companyfacts.json").read_text(encoding="utf-8")), quarters=8)
    passages = [Passage(source_id="acc:item1:11", item="item1", url="https://example/10k.htm",
                        text="In Other Bets, our fully autonomous driving technology company, Waymo, is now providing "
                             "fully autonomous, paid ride-hailing services to customers in multiple cities."),
                Passage(source_id="acc:item7:45", item="item7", url="https://example/10k.htm",
                        text="Other Bets operating loss of $7.5 billion for the year ended December 31, 2025 included a "
                             "$2.1 billion employee compensation charge recognized in the fourth quarter for Waymo.")]
    return fin, build_evidence("GOOG", fin, passages=passages)


def with_waymo_claim(pack, quote, claim="Waymo 已在多个城市提供付费的全自动驾驶网约车服务。"):
    out = good_output(pack)
    out["bull_case"][1] = {"claim": claim, "type": "fact", "evidence_refs": [], "quotes": [{"source_id": quote[0], "text": quote[1]}]}
    return out


def test_fact_with_verbatim_quote_passes(pack_with_text):
    _, pack = pack_with_text
    out = with_waymo_claim(pack, ("acc:item1:11", "Waymo, is now providing fully autonomous, paid ride-hailing services"))
    obj, rep = validate_output(out, pack, THESIS)
    assert rep.ok, rep.feedback()


def test_altered_quote_rejected(pack_with_text):
    _, pack = pack_with_text
    out = with_waymo_claim(pack, ("acc:item1:11", "Waymo is profitable and operates in every major city"))
    rep = validate_output(out, pack, THESIS)[1]
    assert not rep.ok and rep.bad_quotes
    out = with_waymo_claim(pack, ("acc:item9:1", "Waymo, is now providing fully autonomous, paid ride-hailing services"))
    assert validate_output(out, pack, THESIS)[1].bad_quotes


def test_numbers_from_quote_are_grounded(pack_with_text):
    _, pack = pack_with_text
    q = ("acc:item7:45", "Other Bets operating loss of $7.5 billion for the year ended December 31, 2025")
    assert validate_output(with_waymo_claim(pack, q, "2025 年 Other Bets 营业亏损为 $7.5 billion。"), pack, THESIS)[1].ok
    assert not validate_output(with_waymo_claim(pack, q, "2025 年 Other Bets 营业亏损为 $9.9 billion。"), pack, THESIS)[1].ok


def test_segment_fact_without_support_still_flagged(pack_with_text):
    _, pack = pack_with_text
    out = good_output(pack)
    out["bull_case"][1] = {"claim": "Waymo 的收入在快速增长。", "type": "fact", "evidence_refs": [item_id(pack)], "quotes": []}
    assert validate_output(out, pack, THESIS, relabel=False)[1].mislabeled
    obj, report = validate_output(out, pack, THESIS)
    assert report.relabeled and obj.bull_case[1].type == "inference"


def item_id(pack):
    return next(i.fact_id for i in pack.items if i.fact_id.endswith(":revenue:2026-06-30"))


def test_memo_shows_quotes_with_links(pack_with_text):
    fin, pack = pack_with_text
    out = with_waymo_claim(pack, ("acc:item1:11", "Waymo, is now providing fully autonomous, paid ride-hailing services"))
    obj, _ = validate_output(out, pack, THESIS)
    md = render_memo("GOOG", THESIS, fin, pack, obj, language="zh")
    assert "原文：“Waymo, is now providing" in md and "(https://example/10k.htm)" in md


def test_prompt_includes_passages(pack_with_text):
    from investment_ai.research import user_prompt
    _, pack = pack_with_text
    assert "PASSAGES" in user_prompt(pack, THESIS) and "[acc:item1:11]" in user_prompt(pack, THESIS)


def test_retrieve_diverse_dedupes_and_spreads():
    from investment_core.filing_text import retrieve_diverse
    ps = build_passages(HTML, "a", None)
    dup = Passage(source_id="b:item1:1", item="item1", text=ps[0].text)
    out = retrieve_diverse(ps + [dup], ["Waymo", "competition", "capital expenditures"], k=5, per_term=1)
    assert [p.source_id for p in out] == ["a:item1:1", "a:item1a:1", "a:item7:1"]


def test_heading_formats_seen_in_real_10ks():
    html = """<html><body>
    <table><tr><td></td><td></td></tr><tr><td>Item 1.</td><td>Business</td></tr></table>
    <p>We seek to be the most customer-centric company. """ + "Word " * 40 + """</p>
    <p>Item 1</p><p>A running page header must not end the section. """ + "More " * 40 + """</p>
    <p>Item 1A—Risk Factors</p><p>Competition is intense. """ + "Risk " * 40 + """</p>
    <p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>"""
    secs = split_sections(html_to_text(html))
    assert "customer-centric" in secs["item1"] and "running page header" in secs["item1"]
    assert "Competition is intense" in secs["item1a"] and "Unresolved" not in secs["item1a"]
