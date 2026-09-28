"""13F parsing, aggregation and quarter comparison. Golden data: Berkshire's filed information tables."""

import json
from datetime import date
from pathlib import Path

import pytest

from investment_core.thirteenf import Portfolio13F, aggregate, concentration, diff, parse_info_table
from investment_data import cli as data_cli
from investment_data.edgar import EdgarClient

NS = 'xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"'
FIX = Path(__file__).parent / "fixtures" / "13f"


def row(name, cusip, value, shares, put_call=None, cls="COM"):
    pc = f"<putCall>{put_call}</putCall>" if put_call else ""
    return (f"<infoTable><nameOfIssuer>{name}</nameOfIssuer><titleOfClass>{cls}</titleOfClass><cusip>{cusip}</cusip>"
            f"<value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType>"
            f"</shrsOrPrnAmt>{pc}<investmentDiscretion>SOLE</investmentDiscretion></infoTable>")


def table(*rows):
    return f"<informationTable {NS}>{''.join(rows)}</informationTable>".encode()


def pf(period, rows, filed=date(2026, 8, 14)):
    return Portfolio13F(filer="X", cik="1", period=period, filed=filed, accession="a",
                        holdings=aggregate(parse_info_table(table(*rows), filed)))


def test_parse_and_aggregate_lines():
    rows = parse_info_table(table(row("AAA", "111", 100, 10), row("AAA", "111", 50, 5), row("BBB", "222", 30, 3)),
                            date(2026, 8, 14))
    agg = aggregate(rows)
    assert len(rows) == 3 and [(h.issuer, h.value_usd, h.shares) for h in agg] == [("AAA", 150, 15), ("BBB", 30, 3)]


def test_value_units_before_2023_are_thousands():
    old = parse_info_table(table(row("AAA", "111", 100, 10)), date(2022, 11, 14))
    new = parse_info_table(table(row("AAA", "111", 100, 10)), date(2023, 2, 14))
    assert old[0].value_usd == 100_000 and new[0].value_usd == 100


def test_puts_and_calls_kept_separate():
    agg = aggregate(parse_info_table(table(row("AAA", "111", 100, 10), row("AAA", "111", 5, 1, "Put")), date(2026, 8, 14)))
    assert {h.put_call for h in agg} == {None, "Put"}


def test_diff_kinds_and_weights():
    prev = pf("2026-03-31", [row("AAA", "111", 100, 10), row("BBB", "222", 100, 10), row("CCC", "333", 100, 10)])
    cur = pf("2026-06-30", [row("AAA", "111", 300, 20), row("BBB", "222", 50, 5), row("DDD", "444", 50, 5)])
    kinds = {c.issuer: c.kind for c in diff(prev, cur)}
    assert kinds == {"AAA": "increased", "BBB": "decreased", "CCC": "exited", "DDD": "new"}
    aaa = next(c for c in diff(prev, cur) if c.issuer == "AAA")
    assert aaa.shares_change_pct == pytest.approx(1.0) and aaa.weight_after == pytest.approx(0.75)
    assert concentration(cur)["top1"] == pytest.approx(0.75)


def load_fixture(period):
    meta = {m["period"]: m for m in json.loads((FIX / "0001067983_meta.json").read_text())}[period]
    filed = date.fromisoformat(meta["filed"])
    return Portfolio13F(filer=meta["filer"], cik=meta["cik"], period=period, filed=filed, accession=meta["accession"],
                        holdings=aggregate(parse_info_table((FIX / meta["file"]).read_bytes(), filed)))


def test_berkshire_golden():
    """Q2 2026 filing; cross-checked with press coverage (new D.R. Horton, exit Constellation Brands,
    larger Alphabet and Delta stakes, smaller Bank of America)."""
    q1, q2 = load_fixture("2026-03-31"), load_fixture("2026-06-30")
    assert q2.total_value == pytest.approx(299.25e9, rel=1e-3)
    top = q2.top(1)[0]
    assert top.issuer == "APPLE INC" and q2.weight(top) == pytest.approx(0.220, abs=5e-4)
    changes = {(c.issuer, c.cusip): c for c in diff(q1, q2)}
    by_issuer = lambda name: [c for (i, _), c in changes.items() if i == name]
    assert by_issuer("D R HORTON INC")[0].kind == "new"
    assert by_issuer("CONSTELLATION BRANDS INC")[0].kind == "exited"
    assert all(c.kind == "increased" for c in by_issuer("ALPHABET INC"))
    assert by_issuer("DELTA AIR LINES INC")[0].kind == "increased"
    assert by_issuer("BANK OF AMER CORP")[0].kind == "decreased"


class FakeClient(EdgarClient):
    def __init__(self):
        super().__init__(user_agent="t t@example.com", fetch=self._fetch_fixture)
        self.meta = json.loads((FIX / "0001067983_meta.json").read_text())

    def _fetch_fixture(self, url, headers):
        if "submissions" in url:
            recent = {"form": ["13F-HR", "13F-HR/A", "13F-HR", "10-K"], "accessionNumber": [], "filingDate": [], "reportDate": []}
            ms = [self.meta[0], self.meta[1], self.meta[1], self.meta[1]]
            for m in ms:
                recent["accessionNumber"].append(m["accession"]); recent["filingDate"].append(m["filed"])
                recent["reportDate"].append(m["period"])
            return json.dumps({"name": "BERKSHIRE HATHAWAY INC", "filings": {"recent": recent}}).encode()
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "56757.xml"}, {"name": "x.txt"}]}}).encode()
        acc = url.split("/")[-2]
        m = next(m for m in self.meta if m["accession"].replace("-", "") == acc)
        return (FIX / m["file"]).read_bytes()


def test_cli_13f(capsys):
    ns = type("A", (), {"cmd": "13f", "who": "1067983", "top": 5, "json": False, "cache_dir": None})
    assert data_cli._thirteenf(ns, client=FakeClient()) == 0
    out = capsys.readouterr().out
    assert "APPLE INC" in out and "new" in out and "exited" in out and "US long positions only" in out
