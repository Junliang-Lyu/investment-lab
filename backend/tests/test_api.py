"""Public Lab API. EDGAR is faked with recorded fixtures; nothing touches private-data."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from investment_api.app import create_app
from investment_api.settings import Settings

FIX = Path(__file__).parent / "fixtures" / "edgar"


class FakeEdgar:
    KNOWN = {"GOOG", "GOOGL", "BRK-B", "ORCL"}
    calls: list = []  # the recorded facts file stands in for every known ticker

    def cik_for(self, t):
        if t not in self.KNOWN and t not in ("MSFT", "AMZN", "META", "NVDA", "TSLA", "MU", "COST", "V"):
            raise KeyError(t)
        return "0001652044"

    def company_facts(self, cik):
        FakeEdgar.calls.append(cik)
        return json.loads((FIX / "GOOG_companyfacts.json").read_text(encoding="utf-8"))

    def filings(self, cik, forms):
        raise RuntimeError("no network in tests")  # segments are optional


@pytest.fixture
def client():
    return TestClient(create_app(Settings(rate_per_minute=1000), client_factory=FakeEdgar))


def test_health_and_no_docs(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/docs").status_code == 404 and client.get("/api/openapi.json").status_code == 404


def test_demo_portfolios(client):
    body = client.get("/api/lab/demo-portfolios").json()
    ids = {p["id"] for p in body}
    assert ids == {"index-core", "concentrated-tech", "cash-heavy-starter"}
    tech = next(p for p in body if p["id"] == "concentrated-tech")
    assert tech["fictional"] and any(f["rule_code"] == "SATELLITE_MAX_WEIGHT" for f in tech["findings"])
    assert next(p for p in body if p["id"] == "index-core")["findings"] == []


def test_gate(client):
    r = client.post("/api/lab/gate", json={"portfolio_id": "concentrated-tech", "symbol": "amzn", "amount_usd": 800})
    body = r.json()
    assert r.status_code == 200 and body["symbol"] == "AMZN" and body["overall"] == "rule_breaks"
    r = client.post("/api/lab/gate", json={"portfolio_id": "index-core", "symbol": "ZZZZ", "amount_usd": 500})
    assert r.status_code == 200 and any(i["key"] == "memo_reviewed" and i["status"] == "fail" for i in r.json()["items"])


def test_gate_validation(client):
    assert client.post("/api/lab/gate", json={"portfolio_id": "nope", "symbol": "AAPL", "amount_usd": 1}).status_code == 404
    assert client.post("/api/lab/gate", json={"portfolio_id": "index-core", "symbol": "AAPL", "amount_usd": -5}).status_code == 422
    assert client.post("/api/lab/gate", json={"portfolio_id": "index-core", "symbol": "<script>", "amount_usd": 5}).status_code == 422
    assert client.post("/api/lab/gate", json={"portfolio_id": "index-core", "symbol": "VOO", "side": "sell",
                                              "amount_usd": 5}).status_code == 200
    assert client.post("/api/lab/gate", json={"portfolio_id": "index-core", "symbol": "AAPL", "side": "sell",
                                              "amount_usd": 5}).status_code == 422


def test_company_snapshot(client):
    body = client.get("/api/lab/companies/goog/snapshot?lang=zh").json()
    q = body["quarters"][-1]
    assert body["ticker"] == "GOOG" and q["metrics"]["revenue"]["value"] == 119_796e6
    assert q["metrics"]["revenue"]["label"] == "收入" and q["metrics"]["revenue"]["source"].startswith("https://www.sec.gov/")
    assert body["segments"] == []  # fake client has no segment data
    assert client.get("/api/lab/companies/XYZ/snapshot").status_code == 404  # not an SEC ticker


def test_theses(client):
    items = client.get("/api/lab/theses/GOOG?lang=zh").json()
    assert items and items[0]["id"] == "cloud-offsets-capex"


def test_rate_limit():
    c = TestClient(create_app(Settings(rate_per_minute=2), client_factory=FakeEdgar))
    codes = [c.get("/api/lab/companies").status_code for _ in range(3)]
    assert codes == [200, 200, 429] and c.get("/api/health").status_code == 200


def test_lab_never_reads_private_data(client, monkeypatch):
    import builtins
    real_open = builtins.open
    def guarded(path, *a, **k):
        assert "private-data" not in str(path), f"Lab endpoint touched {path}"
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", guarded)
    for url in ["/api/lab/demo-portfolios", "/api/lab/companies", "/api/lab/theses/GOOG"]:
        assert client.get(url).status_code == 200
    assert client.post("/api/lab/gate", json={"portfolio_id": "index-core", "symbol": "MSFT", "amount_usd": 500}).status_code == 200


class DownEdgar:
    def cik_for(self, t):
        raise OSError("name resolution failed")


def test_snapshot_sec_down_is_503_and_stale_cache_is_served():
    down = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=DownEdgar))
    r = down.get("/api/lab/companies/GOOG/snapshot")
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["detail"]

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return FakeEdgar() if calls["n"] == 1 else DownEdgar()

    c = TestClient(create_app(Settings(rate_per_minute=1000, snapshot_ttl_seconds=0), client_factory=flaky))
    first = c.get("/api/lab/companies/GOOG/snapshot")
    second = c.get("/api/lab/companies/GOOG/snapshot")  # expired, SEC down -> stale copy
    assert first.status_code == 200 and second.status_code == 200 and second.json() == first.json()


def test_custom_ticker_snapshot(client):
    r = client.get("/api/lab/companies/googl/snapshot?lang=en")  # not in the curated list, but a SEC ticker
    assert r.status_code == 200 and r.json()["ticker"] == "GOOGL"
    assert client.get("/api/lab/companies/BRK.B/snapshot").json()["ticker"] == "BRK-B"  # dots become dashes
    assert client.get("/api/lab/companies/not%20a%20ticker/snapshot").status_code in (404, 422)
    assert client.get("/api/lab/companies/TOOLONGTICKER1/snapshot").status_code == 422


def test_custom_ticker_limits_and_disk_cache(tmp_path):
    cached = tmp_path / "companyfacts_0001652044.json"
    cached.write_text("{}")
    c = TestClient(create_app(Settings(rate_per_minute=1000, cache_dir=tmp_path, custom_per_ip_daily=2),
                              client_factory=FakeEdgar))
    assert c.get("/api/lab/companies/GOOGL/snapshot").status_code == 200
    assert not cached.exists()  # custom companies leave nothing in the disk cache
    assert c.get("/api/lab/companies/GOOGL/snapshot").status_code == 200  # served from memory: not counted
    assert c.get("/api/lab/companies/BRK-B/snapshot").status_code == 200
    r = c.get("/api/lab/companies/ORCL/snapshot")  # the third new company from this visitor today
    assert r.status_code == 429
    assert c.get("/api/lab/companies/GOOG/snapshot").status_code == 200  # the curated list is not limited


def test_custom_ticker_memory_cache_is_bounded():
    c = TestClient(create_app(Settings(rate_per_minute=1000, custom_cache_max=1), client_factory=FakeEdgar))
    assert c.get("/api/lab/companies/GOOG/snapshot").status_code == 200  # curated: kept
    FakeEdgar.calls.clear()
    for t in ("GOOGL", "ORCL", "GOOGL"):  # ORCL pushes GOOGL out, so the last request builds it again
        assert c.get(f"/api/lab/companies/{t}/snapshot").status_code == 200
    assert len(FakeEdgar.calls) == 3
    FakeEdgar.calls.clear()
    assert c.get("/api/lab/companies/GOOG/snapshot").status_code == 200 and FakeEdgar.calls == []
