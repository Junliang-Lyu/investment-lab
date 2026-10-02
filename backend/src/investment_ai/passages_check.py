"""Check what text the Lab skeptic can quote for each company (needs SEC access and SEC_USER_AGENT).

    python -m investment_ai.passages_check            # the curated Lab companies
    python -m investment_ai.passages_check GOOG TSLA  # selected tickers

Prints, per company, how many paragraphs come from the 10-K / 10-Q, the latest earnings press release and the
risk-factor changes against the prior year, so a company whose exhibit could not be found is easy to spot.
"""

from __future__ import annotations

import sys

from investment_api.settings import CURATED
from investment_data.cli import DEFAULT_CACHE
from investment_data.config import load_env_file
from investment_data.edgar import EdgarClient

from .lab_pack import load_lab_company


def main(argv: list[str]) -> int:
    load_env_file()  # SEC_USER_AGENT from investment-lab/.env, like the other commands
    client = EdgarClient(cache_dir=DEFAULT_CACHE)
    bad = 0
    for t in [a.upper() for a in argv] or CURATED:
        try:
            co = load_lab_company(client, t)
        except Exception as e:  # keep going: one company must not hide the others
            print(f"{t:6} ERROR {type(e).__name__}: {e}")
            bad += 1
            continue
        rel = co.release[0] if co.release else None
        print(f"{t:6} 10-K/10-Q passages {len(co.passages):4}  release {len(co.release):3}"
              f"{' (' + rel.url.rsplit('/', 1)[-1] + ')' if rel and rel.url else ''}  new risks {len(co.new_risks):2}")
        if not co.release:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
