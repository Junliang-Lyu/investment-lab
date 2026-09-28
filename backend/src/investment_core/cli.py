"""Command line entry point.

    python -m investment_core evaluate --portfolio P.json --rules R.yaml
    python -m investment_core gate --portfolio P.json --rules R.yaml --symbol MSFT --amount 500
    python -m investment_core review --portfolio P.json --rules R.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .engine import evaluate
from .gate import evaluate_trade
from .importers import load_context, load_portfolio, load_rule_set, merge_context
from .models import TradeProposal
from .review import build_review

ICON = {"pass": "PASS", "fail": "FAIL", "unknown": " ?? ", "not_applicable": " -- "}


def _parse_attest(values: list[str]) -> dict[str, bool]:
    out = {}
    for v in values:
        key, _, ans = v.partition("=")
        out[key] = ans.strip().lower() in {"y", "yes", "true", "1"}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="investment_core")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("evaluate", "gate", "review"):
        p = sub.add_parser(name)
        p.add_argument("--portfolio", required=True)
        p.add_argument("--rules", required=True)
        p.add_argument("--today", type=date.fromisoformat, default=None)
        p.add_argument("--json", action="store_true")
        p.add_argument("--context", action="append", default=[], help="YAML/JSON with assets, memos, watchlist, last_review_date, gate_records")
        p.add_argument("--previous", help="previous snapshot (portfolio file), enables UNCHECKED_TRADE")
        if name == "gate":
            p.add_argument("--symbol", required=True)
            p.add_argument("--side", choices=["buy", "sell"], default="buy")
            p.add_argument("--amount", type=float)
            p.add_argument("--quantity", type=float)
            p.add_argument("--price", type=float)
            p.add_argument("--attest", action="append", default=[], metavar="KEY=yes|no")
    args = ap.parse_args(argv)

    snapshot, ctx, meta = load_portfolio(args.portfolio)
    for path in args.context:
        ctx = merge_context(ctx, load_context(path))
    if args.previous:
        prev, _, _ = load_portfolio(args.previous)
        ctx = ctx.model_copy(update={"previous_snapshot": prev})
    if args.today:
        ctx = ctx.model_copy(update={"today": args.today})
    rules = load_rule_set(args.rules)

    if args.cmd == "evaluate":
        violations = evaluate(snapshot, rules, ctx)
        if args.json:
            print(json.dumps([v.model_dump(mode="json") for v in violations], indent=2))
        else:
            print(f"{meta.get('name') or args.portfolio} — rules {rules.version}")
            for v in violations or []:
                print(f"  [{v.severity.value:5}] {v.rule_code:28} {v.message}")
            if not violations:
                print("  no findings")
        return 0

    if args.cmd == "gate":
        proposal = TradeProposal(symbol=args.symbol.upper(), side=args.side, amount_usd=args.amount,
                                 quantity=args.quantity, price=args.price,
                                 attestations=_parse_attest(args.attest))
        result = evaluate_trade(snapshot, proposal, rules, ctx)
        if args.json:
            print(result.model_dump_json(indent=2))
        else:
            print(f"{result.side} {result.symbol} ${result.amount_usd:,.0f}: "
                  f"{result.weight_before * 100:.1f}% -> {result.weight_after * 100:.1f}% of net value")
            for i in result.items:
                print(f"  {ICON[i.status]} [{i.severity.value:5}] {i.section:30} {i.label}"
                      + (f" — {i.detail}" if i.detail else ""))
            print(f"overall: {result.overall}")
        return 0

    print(build_review(snapshot, rules, ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
