"""Earnings press release and new-risk passages (DESIGN §11.6): pure parts plus the loader with a fake client."""

from investment_ai.lab_pack import LabCompany, lab_pack
from investment_ai.evidence import EvidencePack
from investment_core.filing_text import Passage, build_release_passages, new_risk_passages
from investment_data.filing_text import load_new_risks, load_release_passages, release_file

RELEASE = """<html><body>
<p>Alphabet Inc. today announced financial results for the quarter ended June 30, 2026, and said that demand for its
cloud services and artificial intelligence products continued to grow across every region it serves.</p>
<p>Short line.</p>
<table><tr><td>Revenues</td><td>119,800</td></tr></table>
<p>Management said it expects capital expenditures to remain elevated as it builds out data center capacity to meet
customer demand, and that depreciation will keep rising as a result of the investment.</p>
</body></html>"""


def para(n, item, text):
    return Passage(source_id=f"acc:{item}:{n}", item=item, text=text, url="https://example/10k.htm")


OLD = [para(1, "item1a", "Competition in cloud services is intense and pricing pressure could reduce our margins over time "
                         "as customers negotiate harder and rivals invest heavily in new capacity."),
       para(2, "item1a", "We face risks from legal proceedings and regulatory actions that could require us to change how we "
                         "operate our advertising and search businesses in several important markets.")]
NEW = [para(1, "item1a", "Competition in cloud services is intense and pricing pressure could reduce our margins over time "
                         "as customers negotiate harder and rivals invest heavily in new capacity."),
       para(2, "item1a", "Our use of generative artificial intelligence products creates new risks, including inaccurate output, "
                         "intellectual property claims and shortages of the specialised chips we depend on to train models."),
       para(3, "item7", "Management discussion paragraph about operating results and liquidity that is not a risk factor at all "
                        "and should never be flagged as a new risk by this comparison logic whatsoever.")]


def test_release_paragraphs():
    ps = build_release_passages(RELEASE, "0001-26-1", "https://example/ex99.htm")
    assert [p.source_id for p in ps] == ["0001-26-1:ex99:1", "0001-26-1:ex99:2"]
    assert all(p.item == "ex99" and "119,800" not in p.text for p in ps)


def test_new_risks_are_the_unmatched_paragraphs_of_the_risk_section_only():
    out = new_risk_passages(NEW, OLD)
    assert [p.source_id for p in out] == ["acc:item1a_new:2"] and out[0].item == "item1a_new"
    assert new_risk_passages(NEW, []) != [] and new_risk_passages([], OLD) == []


def test_release_file_choice():
    names = ["0001-index.htm", "R1.htm", "goog-20260630.htm", "goog-20260630xex99.htm", "Financial_Report.xlsx"]
    assert release_file(names, "goog-20260630.htm") == "goog-20260630xex99.htm"
    assert release_file(["a8k.htm", "pressrelease_q2.htm"], "a8k.htm") == "pressrelease_q2.htm"
    assert release_file(["a8k.htm"], "a8k.htm") is None
    nvda = ["nvda-20260826.htm", "q2fy27cfocommentary.htm", "q2fy27pr.htm", "R1.htm"]
    assert release_file(nvda, "nvda-20260826.htm") == "q2fy27pr.htm"


def test_press_release_text_laid_out_in_tables_is_kept():
    from investment_core.filing_text import build_release_passages
    prose = " ".join(["Net revenue in the quarter grew because payments volume and cross border volume increased"] * 2)
    html = f"<html><body><table><tr><td>Highlights</td></tr><tr><td>{prose}.</td></tr><tr><td>$1.2</td><td>14%</td></tr></table></body></html>"
    ps = build_release_passages(html, "acc")
    assert len(ps) == 1 and ps[0].text.startswith("Net revenue") and ps[0].item == "ex99"


class FakeClient:
    def __init__(self, with_release=True):
        self.with_release = with_release

    def filings(self, cik, forms):
        rows = {"8-K": [{"form": "8-K", "accession": "0001-26-1", "items": "5.02", "primary_document": "a.htm"},
                        {"form": "8-K", "accession": "0001-26-2", "items": "2.02,9.01", "primary_document": "b.htm"}],
                "10-K": [{"form": "10-K", "accession": "0001-26-9", "items": "", "primary_document": "new.htm"},
                         {"form": "10-K", "accession": "0001-25-9", "items": "", "primary_document": "old.htm"}]}
        return [r for f in forms for r in rows.get(f, [])]

    def filing_files(self, cik, accession):
        return ["b.htm", "ex99.htm"] if self.with_release else ["b.htm"]

    def filing_file(self, cik, accession, name):
        if name == "ex99.htm":
            return RELEASE.encode()
        return ("<html><body><div>Item 1A. Risk Factors</div><p>" + OLD[0].text + "</p><p>" + OLD[1].text +
                "</p><div>Item 1B. Unresolved Staff Comments</div></body></html>").encode()

    def document_url(self, cik, accession, name):
        return f"https://example/{accession}/{name}"


def test_loaders():
    rel = load_release_passages(FakeClient(), "1")
    assert rel and rel[0].source_id.startswith("0001-26-2:ex99:") and rel[0].url.endswith("/0001-26-2/ex99.htm")
    assert load_release_passages(FakeClient(with_release=False), "1") == []
    risks = load_new_risks(FakeClient(), "1", NEW)
    assert [p.item for p in risks] == ["item1a_new"]


def test_lab_pack_adds_release_and_new_risks_without_duplicates():
    pack = EvidencePack(ticker="GOOG", company="Alphabet", cik="1", items=[])
    rel = build_release_passages(RELEASE, "acc", "https://example/ex99.htm")
    co = LabCompany(ticker="GOOG", pack=pack, passages=NEW, release=rel, new_risks=new_risk_passages(NEW, OLD))
    got = lab_pack(co, "cloud demand and capital expenditures").passages
    items = [p.item for p in got]
    assert "ex99" in items and items.count("ex99") <= 3
    texts = [p.text[:200] for p in got]
    assert len(texts) == len(set(texts))  # a risk paragraph already retrieved is not added a second time
