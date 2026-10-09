"""FRED macro series (parsing, thinning, caching, failure behaviour) and the earnings calendar. No network."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from test_earnings import WithFilings

from investment_api.app import create_app
from investment_api.macro import SERIES, MacroService, parse_csv, weekly, year_over_year
from investment_api.settings import Settings

TODAY = date(2026, 10, 7)


def _csv(rows, head="observation_date"):
    return f"{head},X\n" + "\n".join(f"{d},{v}" for d, v in rows) + "\n"


def _daily(start=date(2016, 10, 10), end=TODAY, value=lambda i: 4.0 + (i % 50) / 100):
    out, d, i = [], start, 0
    while d <= end:
        if d.weekday() < 5:
            out.append((d.isoformat(), value(i)))
            i += 1
        d += timedelta(days=1)
    return out


def _monthly(start=date(2015, 1, 1), end=date(2026, 9, 1), value=lambda i: 100 + i * 0.3):
    out, d, i = [], start, 0
    while d <= end:
        out.append((d.isoformat(), value(i)))
        i += 1
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


def fake_fetch(calls=None, fail=()):
    def fetch(url):
        sid = url.split("id=")[1].split("&")[0]
        if calls is not None:
            calls.append(sid)
        if sid in fail:
            raise OSError("FRED down")
        if sid in ("DGS10", "DGS2", "T10Y2Y"):
            return _csv(_daily())
        return _csv(_monthly())
    return fetch


def test_parse_skips_missing_values_and_accepts_either_header():
    for head in ("observation_date", "DATE"):
        pts = parse_csv(_csv([("2026-10-01", "4.1"), ("2026-10-02", "."), ("2026-10-05", ""), ("2026-10-06", "4.3")], head))
        assert pts == [(date(2026, 10, 1), 4.1), (date(2026, 10, 6), 4.3)]
    assert parse_csv("") == [] and parse_csv("observation_date,X\n") == [] and parse_csv("<html>blocked</html>") == []
    assert parse_csv("observation_date,X\nnot-a-date,3\n2026-01-02,x\n") == []


def test_weekly_keeps_the_last_observation_of_each_week():
    pts = [(date(2026, 10, 5), 1.0), (date(2026, 10, 6), 2.0), (date(2026, 10, 9), 3.0), (date(2026, 10, 12), 4.0)]
    assert weekly(pts) == [(date(2026, 10, 9), 3.0), (date(2026, 10, 12), 4.0)]


def test_year_over_year_needs_the_same_month_a_year_before():
    pts = [(date(2025, 8, 1), 100.0), (date(2025, 9, 1), 101.0), (date(2026, 8, 1), 103.0), (date(2026, 9, 1), 104.03)]
    out = year_over_year(pts)
    assert [d for d, _ in out] == [date(2026, 8, 1), date(2026, 9, 1)]
    assert round(out[0][1], 2) == 3.0 and round(out[1][1], 2) == 3.0


def test_service_builds_every_series_and_caches():
    calls = []
    svc = MacroService(3600, fetch=fake_fetch(calls), today=lambda: TODAY)
    body = svc.get()
    assert body["source"] == "FRED" and body["missing"] == [] and [s["id"] for s in body["series"]] == [s["id"] for s in SERIES]
    by = {s["id"]: s for s in body["series"]}
    assert len(by["DGS10"]["points"]) < 600  # ten years of days thinned to weeks
    cpi = by["CPIAUCSL"]
    assert cpi["points"][0][0] >= "2016-10" and cpi["unit"] == "%" and 0 < cpi["latest"]["value"] < 10  # a rate, not the index level
    assert by["UNRATE"]["latest"]["date"] == "2026-09-01" and by["UNRATE"]["url"].endswith("/series/UNRATE")
    n = len(calls)
    assert svc.get() == body and len(calls) == n  # second call served from memory


def test_service_serves_stale_data_and_drops_unloadable_series():
    clock = [1000.0]
    import investment_api.macro as m
    orig = m.time.monotonic
    m.time.monotonic = lambda: clock[0]
    try:
        down = set()
        svc = MacroService(100, fetch=lambda u: fake_fetch(fail=down)(u), today=lambda: TODAY)
        first = svc.get()
        down.update(["DGS2", "UNRATE"])  # FRED fails for two series after the data has loaded
        clock[0] += 101
        again = svc.get()
        assert again["missing"] == [] and {s["id"] for s in again["series"]} == {s["id"] for s in first["series"]}
        # a series that never loaded is simply absent
        down2 = {"T10Y2Y"}
        fresh = MacroService(100, fetch=lambda u: fake_fetch(fail=down2)(u), today=lambda: TODAY).get()
        assert fresh["missing"] == ["T10Y2Y"] and len(fresh["series"]) == len(SERIES) - 1
        # a partial result is retried sooner than a full one, i.e. within the TTL
        clock[0] += 50
        down2.clear()
        svc2 = MacroService(10000, fetch=lambda u: fake_fetch(fail=down2)(u), today=lambda: TODAY)
        down2.add("DGS10")
        assert svc2.get()["missing"] == ["DGS10"]
        down2.clear()
        clock[0] += 901
        assert svc2.get()["missing"] == []
    finally:
        m.time.monotonic = orig


def test_service_with_nothing_loaded_returns_none_and_backs_off():
    calls = []
    svc = MacroService(3600, fetch=fake_fetch(calls, fail={s["id"] for s in SERIES}), today=lambda: TODAY)
    assert svc.get() is None
    n = len(calls)
    assert svc.get() is None and len(calls) == n  # not hammering FRED on every visit


def test_html_error_page_is_not_data():
    svc = MacroService(3600, fetch=lambda u: "<html>Access denied</html>", today=lambda: TODAY)
    assert svc.get() is None


def test_macro_endpoint():
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=WithFilings, macro_fetch=fake_fetch()))
    r = c.get("/api/lab/macro")
    assert r.status_code == 200 and len(r.json()["series"]) == len(SERIES)
    assert r.json()["attribution"].startswith("Federal Reserve Bank of St. Louis")


def test_macro_endpoint_unavailable_and_switched_off():
    down = fake_fetch(fail={s["id"] for s in SERIES})
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=WithFilings, macro_fetch=down))
    assert c.get("/api/lab/macro").status_code == 503
    calls = []
    c = TestClient(create_app(Settings(rate_per_minute=1000, macro_enabled=False), client_factory=WithFilings,
                              macro_fetch=fake_fetch(calls)))
    assert c.get("/api/lab/macro").status_code == 503 and calls == []  # the switch means no download at all


def test_macro_switch_and_ttl_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("LAB_MACRO_ENABLED", "0")
    monkeypatch.setenv("LAB_MACRO_TTL_SECONDS", "60")
    s = Settings.from_env()
    assert s.macro_enabled is False and s.macro_ttl_seconds == 60
    monkeypatch.delenv("LAB_MACRO_ENABLED")
    assert Settings.from_env().macro_enabled is True


class AllKnown(WithFilings):
    def cik_for(self, t):
        return "0001652044"


def test_calendar_lists_curated_companies_soonest_first():
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=AllKnown, macro_fetch=fake_fetch()))
    r = c.get("/api/lab/calendar")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["ticker"] for i in items] == Settings().curated  # equal dates keep the list order
    keys = [(i["past"], i["estimated"]) for i in items]
    assert keys == sorted(keys) and r.json()["today"]
    assert all(i["method"] in ("year_ago", "cadence") for i in items)


def test_calendar_puts_passed_dates_last():
    class Mixed(AllKnown):
        def filings(self, cik, forms):
            return super().filings(cik, forms) if self.__class__.n % 2 else self._old()

        n = 0

        def _old(self):
            return [{"form": "8-K", "filed": "2024-01-10", "items": "2.02", "accession": "z", "report_date": "",
                     "primary_document": ""}]

        def cik_for(self, t):
            Mixed.n += 1
            return "1"

    items = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=Mixed)).get("/api/lab/calendar").json()["items"]
    flags = [i["past"] for i in items]
    assert True in flags and False in flags and flags == sorted(flags)


def test_calendar_skips_a_company_that_cannot_be_read_and_fails_only_when_none_can():
    class OneBroken(AllKnown):
        def cik_for(self, t):
            if t == "MSFT":
                raise RuntimeError("SEC hiccup")
            return "1"

    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=OneBroken))
    items = c.get("/api/lab/calendar").json()["items"]
    assert len(items) == len(Settings().curated) - 1 and "MSFT" not in [i["ticker"] for i in items]

    from test_api import FakeEdgar  # filings() always raises
    c = TestClient(create_app(Settings(rate_per_minute=1000), client_factory=FakeEdgar))
    assert c.get("/api/lab/calendar").status_code == 503
