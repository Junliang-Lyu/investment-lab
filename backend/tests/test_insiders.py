"""Form 4 insider trades: parsing, summary, the endpoint."""

from datetime import date

from fastapi.testclient import TestClient
from test_api import FakeEdgar

from investment_api.app import create_app
from investment_api.settings import Settings
from investment_core.insiders import parse_form4, summarize
from investment_data.insiders import load_form4s


def form4(name="Doe Jane", title="Chief Financial Officer", director=0, officer=1, ten=0, plan=0, txns=(), foot=""):
    rows = ""
    for day, code, shares, price, ad, after, owner in txns:
        pr = f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>" if price is not None else ""
        rows += f"""<nonDerivativeTransaction><securityTitle><value>Class A</value></securityTitle>
<transactionDate><value>{day}</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>{shares}</value></transactionShares>{pr}
<transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>{owner}</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction>"""
    return f"""<?xml version="1.0"?><ownershipDocument><schemaVersion>X0508</schemaVersion><documentType>4</documentType>
<issuer><issuerCik>0001652044</issuerCik><issuerName>Alphabet Inc.</issuerName><issuerTradingSymbol>GOOG</issuerTradingSymbol></issuer>
{"<aff10b5One>1</aff10b5One>" if plan else ""}
<reportingOwner><reportingOwnerId><rptOwnerCik>1</rptOwnerCik><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>{director}</isDirector><isOfficer>{officer}</isOfficer><isTenPercentOwner>{ten}</isTenPercentOwner>
<officerTitle>{title}</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>{rows}</nonDerivativeTable><footnotes><footnote id="F1">{foot}</footnote></footnotes></ownershipDocument>""".encode()


TODAY = date(2026, 10, 1)
SALE = ("2026-09-10", "S", 1000, 150.5, "D", 9000, "D")
BUY = ("2026-08-05", "P", "500", "140", "A", 2500, "D")
GRANT = ("2026-08-01", "A", 4000, 0, "A", 13000, "D")
EXERCISE = ("2026-08-01", "M", 4000, 20, "A", 13000, "D")
OLD = ("2025-03-01", "S", 100, 100, "D", 1, "D")


def test_parse_reads_owner_roles_plan_and_rows():
    f = parse_form4(form4(director=1, plan=1, txns=[SALE, GRANT]))
    assert f.owners[0].name == "Doe Jane" and f.owners[0].roles() == ["Chief Financial Officer", "Director"]
    assert f.plan_10b5_1 and len(f.txns) == 2
    assert f.txns[0].code == "S" and f.txns[0].shares == 1000 and f.txns[0].price == 150.5 and not f.txns[0].acquired
    assert f.txns[0].after == 9000 and f.txns[0].direct


def test_parse_finds_the_plan_in_a_footnote_and_ignores_junk():
    assert parse_form4(form4(txns=[SALE], foot="Sold under a Rule 10b5-1 trading plan.")).plan_10b5_1
    assert not parse_form4(form4(txns=[SALE], foot="Weighted average price.")).plan_10b5_1
    assert parse_form4(b"<html>not xml") is None and parse_form4(b"<other/>") is None
    bad = form4(txns=[("not-a-date", "S", 1, 1, "D", 1, "D"), ("2026-09-01", "S", 0, 1, "D", 1, "D")])
    assert parse_form4(bad).txns == []


def test_summary_counts_only_open_market_trades_in_the_window():
    f1 = parse_form4(form4(txns=[SALE, GRANT, EXERCISE, OLD]))
    f2 = parse_form4(form4(name="Roe Rick", title="", director=1, officer=0, txns=[BUY]))
    s = summarize([f1, f2], TODAY)
    assert [t["side"] for t in s["trades"]] == ["sell", "buy"]  # newest first
    assert s["buys"] == {"trades": 1, "people": 1, "shares": 500.0, "value": 70000.0, "planned": 0}
    assert s["sells"]["shares"] == 1000 and s["sells"]["value"] == 150500.0
    assert s["other_transactions"] == 2  # the grant and the exercise are counted, not listed (the old one is out of window)
    assert s["trades"][1]["roles"] == ["Director"] and s["trades"][0]["roles"] == ["Chief Financial Officer"]


def test_a_purchase_code_with_a_disposal_flag_is_not_a_trade_and_duplicates_collapse():
    odd = parse_form4(form4(txns=[("2026-09-01", "P", 10, 10, "D", 1, "D")]))
    assert summarize([odd], TODAY)["trades"] == [] and summarize([odd], TODAY)["other_transactions"] == 1
    a, b = parse_form4(form4(txns=[SALE])), parse_form4(form4(txns=[SALE]))
    assert len(summarize([a, b], TODAY)["trades"]) == 1


def test_a_trade_without_a_price_has_no_value_and_a_plan_trade_is_marked():
    f = parse_form4(form4(plan=1, txns=[("2026-09-01", "S", 10, None, "D", 1, "I")]))
    t = summarize([f], TODAY)["trades"][0]
    assert t["value"] is None and t["plan_10b5_1"] and t["direct"] is False
    assert summarize([f], TODAY)["sells"]["planned"] == 1


class FakeForms(FakeEdgar):
    docs = {"0001-26-1": form4(txns=[SALE, GRANT]), "0001-26-2": form4(name="Roe Rick", title="", director=1,
                                                                       officer=0, txns=[BUY])}
    reads: list = []

    def filings(self, cik, forms):
        assert forms == ("4",)
        return [{"form": "4", "accession": "0001-26-1", "filed": "2026-09-12", "report_date": "", "items": "",
                 "primary_document": "xslF345X05/wf-form4.xml"},
                {"form": "4", "accession": "0001-26-2", "filed": "2026-08-07", "report_date": "", "items": "",
                 "primary_document": "doc4.xml"},
                {"form": "4", "accession": "0001-24-9", "filed": "2024-01-01", "report_date": "", "items": "",
                 "primary_document": "old.xml"}]

    def filing_file(self, cik, acc, name):
        FakeForms.reads.append((acc, name))
        return self.docs[acc]

    def document_url(self, cik, acc, name):
        return f"https://www.sec.gov/{acc}/{name}"


def _app(factory):
    return TestClient(create_app(Settings(rate_per_minute=1000), client_factory=factory))


def test_loader_reads_the_plain_xml_and_skips_old_filings():
    forms, meta = load_form4s(FakeForms(), "0001652044", date(2026, 10, 1))
    assert len(forms) == 2 and meta == {"filings": 2, "read": 2, "failed": 0, "truncated": False}
    assert ("0001-26-1", "wf-form4.xml") in FakeForms.reads  # the rendering prefix is dropped
    assert forms[0].url.endswith("0001-26-1/wf-form4.xml") and forms[0].filed == date(2026, 9, 12)


def test_loader_cap_and_failures():
    forms, meta = load_form4s(FakeForms(), "0001652044", date(2026, 10, 1), cap=1)
    assert len(forms) == 1 and meta["truncated"] and meta["filings"] == 2

    class OneBroken(FakeForms):
        def filing_file(self, cik, acc, name):
            if acc == "0001-26-1":
                raise OSError("boom")
            return super().filing_file(cik, acc, name)
    forms, meta = load_form4s(OneBroken(), "0001652044", date(2026, 10, 1))
    assert len(forms) == 1 and meta["failed"] == 1

    class AllBroken(FakeForms):
        def filing_file(self, cik, acc, name):
            return b"junk"
    try:
        load_form4s(AllBroken(), "0001652044", date(2026, 10, 1))
        assert False
    except RuntimeError:
        pass


def test_endpoint(monkeypatch):
    import investment_api.lab as lab

    class FixedNow(lab.datetime):
        @classmethod
        def now(cls, tz=None):
            return lab.datetime(2026, 10, 1, tzinfo=tz)
    monkeypatch.setattr(lab, "datetime", FixedNow)
    c = _app(FakeForms)
    r = c.get("/api/lab/companies/GOOG/insiders")
    assert r.status_code == 200
    b = r.json()
    assert b["ticker"] == "GOOG" and b["buys"]["trades"] == 1 and b["sells"]["trades"] == 1 and b["read"] == 2
    assert b["trades"][0]["url"].startswith("https://www.sec.gov/") and b["window_end"] == "2026-10-01"
    assert c.get("/api/lab/companies/ORCL/insiders").status_code == 200  # any SEC-registered company
    assert c.get("/api/lab/companies/XYZQ/insiders").status_code == 404
    assert c.get("/api/lab/companies/1bad/insiders").status_code == 422


def test_endpoint_when_sec_is_down_and_with_no_filings():
    assert _app(FakeEdgar).get("/api/lab/companies/GOOG/insiders").status_code == 503  # filings() raises

    class NoForms(FakeEdgar):
        def filings(self, cik, forms):
            return []
    r = _app(NoForms).get("/api/lab/companies/GOOG/insiders")
    assert r.status_code == 200 and r.json()["trades"] == [] and r.json()["filings"] == 0
