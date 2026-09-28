"""Chinese Lab text: every label, detail and finding the demo can produce is translated."""

import re

import pytest
from fastapi.testclient import TestClient
from test_api import FakeEdgar

from investment_api.app import create_app
from investment_api.i18n import zh
from investment_api.settings import Settings

CJK = re.compile(r"[一-鿿]")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(Settings(rate_per_minute=10_000), client_factory=FakeEdgar))


def test_demo_portfolios_in_chinese(client):
    body = client.get("/api/lab/demo-portfolios?lang=zh").json()
    for p in body:
        assert CJK.search(p["name"]) and CJK.search(p["description"])
        for f in p["findings"]:
            assert CJK.search(f["message"]), f["message"]
    assert client.get("/api/lab/demo-portfolios").json()[0]["name"].isascii()


TRADES = [("concentrated-tech", "AMZN", "buy", 800), ("concentrated-tech", "NVDA", "buy", 800),
          ("concentrated-tech", "MSFT", "sell", 500), ("index-core", "VOO", "buy", 500),
          ("index-core", "AAPL", "buy", 1500), ("cash-heavy-starter", "MSFT", "buy", 900),
          ("cash-heavy-starter", "COST", "buy", 20000), ("index-core", "VXUS", "sell", 100)]


@pytest.mark.parametrize("pid,symbol,side,amount", TRADES)
def test_gate_items_in_chinese(client, pid, symbol, side, amount):
    body = client.post("/api/lab/gate?lang=zh", json={"portfolio_id": pid, "symbol": symbol, "side": side,
                                                        "amount_usd": amount}).json()
    for item in body["items"]:
        assert CJK.search(item["section"]), item
        if not item["label"].isupper():
            assert CJK.search(item["label"]), item["label"]
        if item.get("detail"):
            assert CJK.search(item["detail"]), item["detail"]


def test_nested_and_unknown_text():
    text = "Not made worse by this trade (already over limit: Top holdings (NVDA, MSFT) are 100.0% of invested capital, limit 90.0%)"
    assert zh(text) == "这笔交易没有让情况变差（原本已超限：前几大持仓（NVDA, MSFT）占已投入资金 100.0%，上限 90.0%）"
    assert zh("MSFT is 25.0% of net value, limit 15.0%; NVDA is 25.0% of net value, limit 15.0%") == \
        "MSFT 占净值 25.0%，上限 15.0%；NVDA 占净值 25.0%，上限 15.0%"
    assert zh("something new") == "something new" and zh(None) is None


def test_snapshot_note_in_chinese(client):
    body = client.get("/api/lab/companies/GOOG/snapshot?lang=zh").json()
    assert CJK.search(body["note"]) and CJK.search(body["quarters"][-1]["metrics"]["revenue"]["label"])
