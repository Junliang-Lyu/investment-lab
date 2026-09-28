"""Quarterly extraction from companyfacts-shaped data.

Synthetic data follows the real SEC companyfacts structure. Golden tests on
recorded real filings live in test_financials_golden.py.
"""

from datetime import date

import pytest

from investment_core.financials import (
    build_financials,
    extract_facts,
    fiscal_label,
    quarterly_series,
    source_url,
)


def row(start, end, val, accn, form="10-Q", filed=None):
    r = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed or end, "fy": 2025, "fp": "Q?"}
    if start:
        r["start"] = start
    return r


def company(**concepts):
    return {"cik": 1234, "entityName": "Test Co",
            "facts": {"us-gaap": {c: {"units": {"USD": rows}} for c, rows in concepts.items()}}}


Q1, Q2, Q3, Q4 = ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"), ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")
A1, A2, A3, AK = "0000001234-25-000001", "0000001234-25-000002", "0000001234-25-000003", "0000001234-26-000001"
F1, F2, F3, FK = "2025-04-25", "2025-07-25", "2025-10-25", "2026-02-01"


def calendar_company():
    revenue = [row(*Q1, 100, A1, filed=F1), row(*Q2, 110, A2, filed=F2), row("2025-01-01", "2025-06-30", 210, A2, filed=F2),
               row(*Q3, 120, A3, filed=F3), row("2025-01-01", "2025-09-30", 330, A3, filed=F3),
               row("2025-01-01", "2025-12-31", 460, AK, "10-K", FK)]
    cost = [row(*Q1, 40, A1, filed=F1), row(*Q2, 44, A2, filed=F2), row(*Q3, 48, A3, filed=F3),
            row("2025-01-01", "2025-09-30", 132, A3, filed=F3), row("2025-01-01", "2025-12-31", 184, AK, "10-K", FK)]
    cfo = [row(*Q1, 30, A1, filed=F1), row("2025-01-01", "2025-06-30", 70, A2, filed=F2),
           row("2025-01-01", "2025-09-30", 115, A3, filed=F3), row("2025-01-01", "2025-12-31", 160, AK, "10-K", FK)]
    capex = [row(*Q1, 10, A1, filed=F1), row("2025-01-01", "2025-06-30", 22, A2, filed=F2),
             row("2025-01-01", "2025-09-30", 35, A3, filed=F3), row("2025-01-01", "2025-12-31", 50, AK, "10-K", FK)]
    op = [row(*Q1, 20, A1, filed=F1), row(*Q2, 22, A2, filed=F2), row(*Q3, 25, A3, filed=F3),
          row("2025-01-01", "2025-09-30", 67, A3, filed=F3), row("2025-01-01", "2025-12-31", 95, AK, "10-K", FK)]
    return company(Revenues=revenue, CostOfRevenue=cost, NetCashProvidedByUsedInOperatingActivities=cfo,
                   PaymentsToAcquirePropertyPlantAndEquipment=capex, OperatingIncomeLoss=op)


def values(fin, metric):
    return [q.metrics[metric].value if metric in q.metrics else None for q in fin.quarters]


def test_revenue_q4_derived_from_fy_minus_9m():
    fin = build_financials(calendar_company())
    assert values(fin, "revenue") == [100, 110, 120, 130]
    q4 = fin.quarters[-1].metrics["revenue"]
    assert q4.derived and "12M YTD" in q4.derivation and "9M YTD" in q4.derivation
    assert not fin.quarters[0].metrics["revenue"].derived


def test_cash_flow_quarters_from_ytd_differencing():
    fin = build_financials(calendar_company())
    assert values(fin, "cfo") == [30, 40, 45, 45]
    assert values(fin, "capex") == [10, 12, 13, 15]
    assert values(fin, "fcf") == [20, 28, 32, 30]
    assert sum(values(fin, "cfo")) == 160  # quarters add up to the fiscal year


def test_gross_profit_derived_from_cost_and_margins():
    fin = build_financials(calendar_company())
    assert values(fin, "gross_profit") == [60, 66, 72, 78]
    assert fin.quarters[0].metrics["gross_margin"].value == pytest.approx(0.6)
    assert fin.quarters[3].metrics["operating_margin"].value == pytest.approx(28 / 130)
    gp = fin.quarters[0].metrics["gross_profit"]
    assert gp.derived and "cost of revenue" in gp.derivation


def test_evidence_ids_and_sources():
    fin = build_financials(calendar_company())
    rev = fin.quarters[1].metrics["revenue"]
    assert rev.evidence_id == "0000001234:revenue:2025-06-30"
    assert rev.sources[0].accn == A2
    assert source_url("1234", A2) == "https://www.sec.gov/Archives/edgar/data/1234/000000123425000002/0000001234-25-000002-index.htm"


def test_direct_quarter_preferred_over_derived():
    data = company(Revenues=[row(*Q1, 100, A1), row(*Q2, 111, A2), row("2025-01-01", "2025-06-30", 210, A2)])
    q2 = quarterly_series(extract_facts(data, "Revenues"))[date(2025, 6, 30)]
    assert q2.value == 111 and not q2.derived


def test_latest_filing_wins_for_same_period():
    data = company(Revenues=[row(*Q1, 100, A1, filed="2025-04-25"),
                             row(*Q1, 101, "0000001234-26-000009", filed="2026-04-25")])
    facts = extract_facts(data, "Revenues")
    assert len(facts) == 1 and facts[0].val == 101


def test_ignores_other_forms_units_and_instant_facts():
    data = company(Revenues=[row(*Q1, 100, A1), row(*Q2, 999, "x", form="8-K"), row(None, "2025-06-30", 5, "y")])
    data["facts"]["us-gaap"]["Revenues"]["units"]["EUR"] = [row(*Q2, 7, "z")]
    assert [f.val for f in extract_facts(data, "Revenues")] == [100]


def test_concept_switch_across_years():
    old = [row("2024-10-01", "2024-12-31", 90, "a", "10-Q")]
    new = [row(*Q1, 100, A1)]
    fin = build_financials(company(Revenues=old, RevenueFromContractWithCustomerExcludingAssessedTax=new))
    assert values(fin, "revenue") == [90, 100]
    assert fin.quarters[1].metrics["revenue"].concept == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_non_calendar_fiscal_year_labels():
    """Micron-style year: starts late August, named after the year it ends."""
    s = "2025-08-29"
    rows = [row(s, "2025-11-27", 100, "m1", filed="2025-12-20"),
            row("2025-11-28", "2026-02-26", 110, "m2", filed="2026-03-20"), row(s, "2026-02-26", 210, "m2", filed="2026-03-20"),
            row("2026-02-27", "2026-05-28", 120, "m3", filed="2026-06-20"), row(s, "2026-05-28", 330, "m3", filed="2026-06-20"),
            row(s, "2026-09-03", 470, "mk", "10-K", "2026-10-20")]
    fin = build_financials(company(RevenueFromContractWithCustomerExcludingAssessedTax=rows))
    assert [q.fiscal_label for q in fin.quarters] == ["FY2026 Q1", "FY2026 Q2", "FY2026 Q3", "FY2026 Q4"]
    assert values(fin, "revenue") == [100, 110, 120, 140]


def test_calendar_labels():
    fin = build_financials(calendar_company())
    assert [q.fiscal_label for q in fin.quarters] == ["FY2025 Q1", "FY2025 Q2", "FY2025 Q3", "FY2025 Q4"]


def test_quarters_limit_takes_latest():
    fin = build_financials(calendar_company(), quarters=2)
    assert [str(q.end) for q in fin.quarters] == ["2025-09-30", "2025-12-31"]


def test_q4_from_sum_of_direct_quarters_when_no_9m_ytd():
    rows = [row(*Q1, 100, A1), row(*Q2, 110, A2), row(*Q3, 120, A3), row("2025-01-01", "2025-12-31", 460, AK, "10-K", FK)]
    q4 = quarterly_series(extract_facts(company(Revenues=rows), "Revenues"))[date(2025, 12, 31)]
    assert q4.value == 130 and "sum of quarters" in q4.derivation


def test_unsupported_company():
    fin = build_financials(company(InterestIncome=[row(*Q1, 5, A1)]))
    assert not fin.supported and fin.quarters == [] and "not supported" in fin.reason


def test_fiscal_label_none_without_ytd():
    facts = extract_facts(company(Revenues=[row(*Q1, 100, A1)]), "Revenues")
    assert fiscal_label(date(2025, 1, 1), date(2025, 3, 31), facts) is None



def instant(end, val, accn, form="10-Q", filed=None):
    return {"end": end, "val": val, "accn": accn, "form": form, "filed": filed or end}


def with_balance_sheet(data, noncurrent=True):
    g = data["facts"]["us-gaap"]
    g["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": [
        instant("2025-12-31", 50, AK, "10-K", FK), instant("2025-12-31", 49, "old", "10-Q", "2025-01-01"),
        {"start": "2025-01-01", "end": "2025-12-31", "val": 999, "accn": "x", "form": "10-K", "filed": FK}]}}
    g["MarketableSecuritiesCurrent"] = {"units": {"USD": [instant("2025-12-31", 30, AK, "10-K", FK)]}}
    if noncurrent:
        g["LongTermDebtNoncurrent"] = {"units": {"USD": [instant("2025-12-31", 20, AK, "10-K", FK)]}}
    g["LongTermDebtCurrent"] = {"units": {"USD": [instant("2025-12-31", 5, AK, "10-K", FK)]}}
    return data


def test_balance_sheet_metrics():
    fin = build_financials(with_balance_sheet(calendar_company()))
    q4 = fin.quarters[-1].metrics
    assert q4["cash"].value == 50  # latest filed wins; duration facts ignored
    assert q4["cash_and_investments"].value == 80
    assert q4["total_debt"].value == 25 and q4["net_cash"].value == 55
    assert "cash" not in fin.quarters[0].metrics  # no balance sheet reported for Q1 in this fixture


def test_no_total_debt_without_noncurrent_tag():
    fin = build_financials(with_balance_sheet(calendar_company(), noncurrent=False))
    q4 = fin.quarters[-1].metrics
    assert "cash_and_investments" in q4 and "total_debt" not in q4 and "net_cash" not in q4
