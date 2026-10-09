"""Live check of the reference list against the real SEC (run by hand after changing the list):
every CIK must answer, and the name the SEC reports must contain the fragment we expect for it.

    python -c "import sys; sys.path.insert(0,'src'); from investment_api.reference_check import main; main()"
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from .app import create_app
from .reference import REFERENCE


def main(only: list[str] | None = None) -> int:
    client = TestClient(create_app())
    bad = 0
    for r in REFERENCE:
        if only and r["id"] not in only:
            continue
        resp = client.get(f"/api/lab/reference/{r['id']}")
        if resp.status_code != 200:
            print(f"FAIL {r['id']:<12} {r['cik']:>8}  HTTP {resp.status_code} {resp.text[:100]}")
            bad += 1
            continue
        b = resp.json()
        ok = r["expect"].upper() in b["filer"].upper()
        bad += not ok
        c = b["concentration"]
        print(f"{'ok  ' if ok else 'NAME'} {r['id']:<12} {r['cik']:>8}  {b['filer']:<42} {b['period']} (prev {b['previous_period']}) "
              f"positions={c['positions']} top1={c['top1']:.3f} top3={c['top3']:.3f}")
    print("ALL OK" if not bad else f"{bad} PROBLEM(S)")
    return bad
