import json

import pytest

from investment_data import cli as data_cli
from investment_data.cli import trim_companyfacts
from investment_data.edgar import EdgarClient, EdgarConfigError, pad_cik

TICKERS = {"0": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
           "1": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."}}


class FakeFetch:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        for key, payload in self.routes.items():
            if key in url:
                return json.dumps(payload).encode()
        raise AssertionError(f"unexpected url {url}")


def client(fetch, **kw):
    return EdgarClient(user_agent="Test User test@example.com", fetch=fetch, **kw)


def test_user_agent_required(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(EdgarConfigError):
        EdgarClient()
    with pytest.raises(EdgarConfigError):
        EdgarClient(user_agent="no email here")


def test_user_agent_from_env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Env User env@example.com")
    assert EdgarClient().user_agent == "Env User env@example.com"


def test_cik_lookup_and_headers():
    fetch = FakeFetch({"company_tickers": TICKERS})
    c = client(fetch)
    assert c.cik_for("goog") == "0001652044"
    url, headers = fetch.calls[0]
    assert headers["User-Agent"] == "Test User test@example.com"
    with pytest.raises(KeyError):
        c.cik_for("NOPE")


def test_company_facts_url_and_cache(tmp_path):
    fetch = FakeFetch({"companyfacts/CIK0001652044": {"cik": 1652044}})
    c = client(fetch, cache_dir=tmp_path)
    assert c.company_facts(1652044) == {"cik": 1652044}
    assert c.company_facts("1652044") == {"cik": 1652044}
    assert len(fetch.calls) == 1  # second call served from cache
    assert (tmp_path / "companyfacts_0001652044.json").exists()


def test_rate_limit_sleeps_between_requests():
    t = [100.0]
    slept = []
    fetch = FakeFetch({"companyfacts": {}, "submissions": {}})
    c = client(fetch, clock=lambda: t[0], sleep=slept.append, min_interval=0.12)
    c.company_facts(1)
    c.submissions(1)
    assert slept and slept[0] == pytest.approx(0.12)


def test_pad_cik():
    assert pad_cik(320193) == "0000320193"


def test_trim_companyfacts():
    facts = {"cik": 1, "entityName": "X", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [{"end": "2022-12-31", "val": 1}, {"end": "2024-03-31", "val": 2}]}},
        "SomethingElse": {"units": {"USD": [{"end": "2024-03-31", "val": 3}]}},
    }}}
    out = trim_companyfacts(facts, "2023-01-01")
    assert list(out["facts"]["us-gaap"]) == ["Revenues"]
    assert out["facts"]["us-gaap"]["Revenues"]["units"]["USD"] == [{"end": "2024-03-31", "val": 2}]


def test_cli_financials_table(monkeypatch, capsys, tmp_path):
    from test_financials import calendar_company

    class FakeClient:
        def __init__(self, **kw):
            pass

        def cik_for(self, t):
            return "0000001234"

        def company_facts(self, cik):
            return calendar_company()

    monkeypatch.setattr(data_cli, "EdgarClient", FakeClient)
    assert data_cli.main(["financials", "TEST", "--quarters", "4"]) == 0
    out = capsys.readouterr().out
    assert "FY2025 Q4" in out and "sec.gov/Archives" in out

    assert data_cli.main(["record-fixture", "TEST", "--out", str(tmp_path), "--since", "2025-01-01"]) == 0
    assert (tmp_path / "TEST_companyfacts.json").exists()


def test_load_env_file(tmp_path, monkeypatch):
    from investment_data.config import load_env_file
    f = tmp_path / ".env"
    f.write_text('# comment\nSEC_USER_AGENT="File User file@example.com"\nEMPTY=\nALREADY=from-file\n', encoding="utf-8")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("ALREADY", "from-env")
    loaded = load_env_file(f)
    import os
    assert os.environ["SEC_USER_AGENT"] == "File User file@example.com"
    assert os.environ["ALREADY"] == "from-env" and "EMPTY" not in loaded
    monkeypatch.delenv("SEC_USER_AGENT")
    assert load_env_file(tmp_path / "missing.env") == {}
