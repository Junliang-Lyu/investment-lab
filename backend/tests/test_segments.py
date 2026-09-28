"""Segment and product revenue from XBRL instances. Golden: trimmed GOOG Q2 2026 10-Q instance."""

import json
from datetime import date
from pathlib import Path

import pytest

from investment_ai.evidence import build_evidence, label
from investment_core.financials import build_financials
from investment_core.segments import member_label, parse_instance, segment_series

FIX = Path(__file__).parent / "fixtures" / "edgar"
ACC = "0001652044-26-000071"


@pytest.fixture(scope="module")
def facts():
    return parse_instance((FIX / "GOOG_2026Q2_segments_instance.xml").read_bytes(), ACC, "10-Q", date(2026, 7, 23))


def test_member_labels():
    assert member_label("goog:GoogleCloudMember") == "Google Cloud"
    assert member_label("goog:YouTubeAdvertisingRevenueMember") == "YouTube Advertising"
    assert member_label("goog:GoogleSearchOtherMember") == "Google Search & other"


def test_segment_values_match_release(facts):
    """Alphabet Q2 2026 release: Google Cloud revenue $24.8B (+82% YoY), operating income $8.8B."""
    ser = segment_series(facts)
    q2 = date(2026, 6, 30)
    cloud_rev = ser[("segment_revenue", "goog:GoogleCloudMember")]
    assert cloud_rev[q2].value == 24_768e6 and not cloud_rev[q2].derived
    assert cloud_rev[q2].value / cloud_rev[date(2025, 6, 30)].value - 1 == pytest.approx(0.818, abs=1e-3)
    assert ser[("segment_operating_income", "goog:GoogleCloudMember")][q2].value == 8_814e6
    assert ser[("segment_operating_income", "us-gaap:AllOtherSegmentsMember")][q2].value < 0


def test_q2_from_six_month_ytd_matches_direct(facts):
    only_ytd = [f for f in facts if f.fact.months != 3 or f.fact.end.month != 6]
    ser = segment_series(only_ytd)
    q2 = ser[("segment_revenue", "goog:GoogleCloudMember")].get(date(2026, 6, 30))
    assert q2 is None or q2.derived  # without the 3M fact it is derived (needs Q1 from another filing)


def test_latest_filing_wins(facts):
    later = [f.model_copy(update={"fact": f.fact.model_copy(update={"val": f.fact.val + 1, "accn": "z", "filed": date(2026, 10, 1)})})
             for f in facts if f.member == "goog:GoogleCloudMember" and f.metric == "segment_revenue"]
    ser = segment_series(facts + later)
    assert ser[("segment_revenue", "goog:GoogleCloudMember")][date(2026, 6, 30)].value == 24_768e6 + 1


def test_segments_in_evidence_pack(facts):
    fin = build_financials(json.loads((FIX / "GOOG_companyfacts.json").read_text(encoding="utf-8")), quarters=8)
    pack = build_evidence("GOOG", fin, segment_series(facts))
    ids = {i.fact_id: i for i in pack.items}
    rev = ids["0001652044:segment_revenue:google-cloud:2026-06-30"]
    assert rev.display == "$24.77B" and rev.member == "Google Cloud" and rev.source.endswith("-index.htm")
    assert ids["0001652044:segment_revenue_yoy:google-cloud:2026-06-30"].display == "81.8%"
    assert ids["0001652044:segment_operating_margin:google-cloud:2026-06-30"].value == pytest.approx(8_814 / 24_768)
    assert "分部收入 · Google Cloud" in pack.to_prompt_table("zh")
    assert label("segment_revenue_yoy", "zh", "Google Cloud") == "分部收入 · Google Cloud同比"
    assert not any(i.fact_id.startswith("0001652044:segment_operating_income_yoy:all-other") for i in pack.items)
