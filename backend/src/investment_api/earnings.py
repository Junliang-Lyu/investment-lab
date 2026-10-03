"""Estimated date of a company's next earnings release, from the dates of its past earnings 8-Ks (Item 2.02).
An estimate only: companies announce the real date themselves, usually a few weeks ahead."""

from __future__ import annotations

from datetime import date, timedelta


def estimate_next_earnings(filed: list[date], today: date) -> dict | None:
    """`filed` are filing dates of 8-Ks that report results. The release one quarter after the latest one fell, last
    year, on the date of the filing three places back in the list; the same weekday a year on is the estimate.
    Without enough history, or when that does not fit a quarter's rhythm, the latest date plus 91 days."""
    days = sorted(set(filed), reverse=True)
    if not days:
        return None
    last = days[0]
    est, method = None, "cadence"
    if len(days) >= 4:
        cand = days[3] + timedelta(days=364)
        if timedelta(days=50) <= cand - last <= timedelta(days=130):
            est, method = cand, "year_ago"
    if est is None:
        est = last + timedelta(days=91)
    return {"last_reported": last.isoformat(), "estimated": est.isoformat(), "method": method, "past": est < today}
