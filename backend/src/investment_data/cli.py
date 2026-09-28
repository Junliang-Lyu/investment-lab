"""Command line for public data.

    python -m investment_data financials GOOG --quarters 8
    python -m investment_data record-fixture GOOG --out tests/fixtures/edgar
    python -m investment_data 13f BRK-B --top 15            # latest 13F holdings and changes vs prior quarter
    python -m investment_data record-13f BRK-B --out tests/fixtures/13f --quarters 2

Requires SEC_USER_AGENT="<name> <email>" (environment or investment-lab/.env). Responses are cached in ../data-cache/edgar.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from investment_core.financials import DISPLAY_METRICS, INSTANT_CONCEPTS, METRIC_CONCEPTS, build_financials, source_url

from .config import load_env_file
from .edgar import EdgarClient

DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data-cache" / "edgar"


def _fmt(metric: str, value: float) -> str:
    if metric.endswith("margin"):
        return f"{value * 100:.1f}%"
    return f"{value / 1e9:,.2f}B"


def trim_companyfacts(facts: dict, since: str) -> dict:
    """Keep only the concepts we use and facts ending on/after `since` (for test fixtures)."""
    concepts = {c for cs in [*METRIC_CONCEPTS.values(), *INSTANT_CONCEPTS.values()] for c in cs}
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = {"cik": facts.get("cik"), "entityName": facts.get("entityName"), "facts": {"us-gaap": {}}}
    for name in concepts & set(gaap):
        units = {u: [r for r in rows if r.get("end", "") >= since] for u, rows in gaap[name]["units"].items()}
        out["facts"]["us-gaap"][name] = {"units": {u: rows for u, rows in units.items() if rows}}
    return out


def _thirteenf(args, client: EdgarClient | None = None) -> int:
    from investment_core.thirteenf import concentration, diff
    from .thirteenf import load_portfolios, resolve_cik

    client = client or EdgarClient(cache_dir=args.cache_dir)
    if args.cmd == "record-13f":
        cik = resolve_cik(client, args.who)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        meta = []
        for p in load_portfolios(client, args.who, args.quarters):
            name = f"{cik}_{p.period}.xml"
            (out / name).write_bytes(client.info_table(cik, p.accession))
            meta.append({"file": name, "filer": p.filer, "cik": cik, "period": str(p.period), "filed": str(p.filed),
                         "accession": p.accession})
        (out / f"{cik}_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print(f"wrote {len(meta)} filings to {out}")
        return 0

    ps = load_portfolios(client, args.who, 2)
    if not ps:
        print("no 13F-HR filings found")
        return 1
    cur = ps[0]
    if args.json:
        print(json.dumps({"current": cur.model_dump(mode="json"),
                          "changes": [c.model_dump(mode="json") for c in diff(ps[1], cur)] if len(ps) > 1 else []},
                         indent=1))
        return 0
    conc = concentration(cur)
    print(f"{cur.filer} (CIK {cur.cik}) — 13F for {cur.period}, filed {cur.filed}, accession {cur.accession}")
    print(f"  {int(conc['positions'])} positions, ${cur.total_value / 1e9:,.1f}B; top 1 {conc['top1']:.1%}, "
          f"top 5 {conc['top5']:.1%}, top 10 {conc['top10']:.1%}")
    print("  Top holdings:")
    for h in cur.top(args.top):
        pc = f" {h.put_call}" if h.put_call else ""
        print(f"    {h.issuer[:32]:32} {h.title_class[:14]:14}{pc} {cur.weight(h):6.1%}  ${h.value_usd / 1e9:7.2f}B")
    if len(ps) > 1:
        print(f"  Changes vs {ps[1].period} (by share count; values also move with prices):")
        for c in diff(ps[1], cur):
            if c.kind == "unchanged":
                continue
            pct = f"{c.shares_change_pct:+.1%}" if c.shares_change_pct is not None else ""
            print(f"    {c.kind:9} {c.issuer[:32]:32} {c.cusip[-3:]} {pct:>8}  weight {c.weight_before:5.1%} -> {c.weight_after:5.1%}")
    print("  Note: 13F lists US long positions only, filed up to 45 days after quarter end. Amendments not merged.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="investment_data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("financials")
    f.add_argument("ticker")
    f.add_argument("--quarters", type=int, default=8)
    f.add_argument("--json", action="store_true")
    f.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    r = sub.add_parser("record-fixture")
    r.add_argument("ticker")
    r.add_argument("--out", required=True)
    r.add_argument("--since", default="2023-01-01")
    r.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    t = sub.add_parser("13f")
    t.add_argument("who", help="CIK or ticker, e.g. BRK-B")
    t.add_argument("--top", type=int, default=15)
    t.add_argument("--json", action="store_true")
    t.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    rt = sub.add_parser("record-13f")
    rt.add_argument("who")
    rt.add_argument("--out", required=True)
    rt.add_argument("--quarters", type=int, default=2)
    rt.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    args = ap.parse_args(argv)
    load_env_file()

    if args.cmd in ("13f", "record-13f"):
        return _thirteenf(args)

    client = EdgarClient(cache_dir=args.cache_dir)
    cik = client.cik_for(args.ticker)
    facts = client.company_facts(cik)

    if args.cmd == "record-fixture":
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{args.ticker.upper()}_companyfacts.json"
        path.write_text(json.dumps(trim_companyfacts(facts, args.since), indent=1), encoding="utf-8")
        print(f"wrote {path}")
        return 0

    fin = build_financials(facts, quarters=args.quarters)
    if args.json:
        print(fin.model_dump_json(indent=2))
        return 0
    print(f"{fin.name} (CIK {fin.cik})")
    if not fin.supported:
        print(f"  not supported: {fin.reason}")
        return 0
    header = ["period end", "fiscal"] + DISPLAY_METRICS
    print("  " + " | ".join(f"{h:>14}" for h in header))
    for q in fin.quarters:
        cells = [str(q.end), q.fiscal_label or "?"]
        for m in DISPLAY_METRICS:
            mv = q.metrics.get(m)
            cells.append((_fmt(m, mv.value) + ("*" if mv.derived and not m.endswith(("margin", "fcf")) else ""))
                         if mv else "-")
        print("  " + " | ".join(f"{c:>14}" for c in cells))
    print("  * derived (year-to-date differencing or revenue - cost); formulas in --json. Margins and FCF are always computed")
    latest = fin.quarters[-1].metrics["revenue"]
    print(f"  latest revenue source: {source_url(fin.cik, latest.sources[0].accn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
