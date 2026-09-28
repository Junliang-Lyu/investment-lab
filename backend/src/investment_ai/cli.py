"""Memo draft command.

    python -m investment_ai memo-draft GOOG --thesis "..." [--provider anthropic|gemini] [--lang zh|en]
    python -m investment_ai memo-draft GOOG --thesis "..." --dry-run      # show prompt, no API call
    python -m investment_ai memo-from-run <run_id>                       # re-render a logged run, no API call
    python -m investment_ai theses GOOG                                  # example theses for people without a view
    python -m investment_ai memo-review <memo.md> [--from-json F] [--portfolio P --rules R --context C ...]
    python -m investment_ai memo-finalize <memo.md> --decision watchlist|paper|eligible_for_gate [--reason "..."]

Private drafts are written to private-data/memos/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from investment_core.financials import build_financials
from investment_data.cli import DEFAULT_CACHE
from investment_data.config import load_env_file
from investment_data.edgar import EdgarClient

from investment_core.importers import load_context, load_portfolio, load_rule_set, merge_context
from investment_core.memo import TransitionError
from investment_core.models import MemoDecision

from . import memo_status
from .evidence import build_evidence
from .memo_finalize import finalize, render_final, write_final_into_memo
from .memo_parse import parse_memo
from .memo_review import (evaluate_supplied_review, position_check, render_review, run_memo_review,
                          write_review_into_memo)
from .theses import examples
from .ledger import Ledger
from .memo_draft import render_memo
from .providers import make_provider
from .research import evaluate_supplied, run_research_skeptic, system_prompt, user_prompt
from .validate import validate_output

PRIVATE_MEMOS = Path(__file__).resolve().parents[3] / "private-data" / "memos"
DEFAULT_KEYWORDS = ["capital expenditures", "depreciation", "competition", "artificial intelligence",
                    "regulatory", "operating loss"]


def build_pack(client, ticker: str, thesis: str = "", keywords: list[str] | None = None, fundamentals: bool = True,
               quarters: int = 8, k: int = 18):
    """Financials + (optionally) segment numbers and retrieved filing passages. Returns (fin, pack)."""
    import re as _re
    from investment_core.filing_text import retrieve_diverse
    from investment_core.segments import segment_series
    from investment_data.filing_text import load_passages
    from investment_data.segments import load_segment_facts

    cik = client.cik_for(ticker)
    fin = build_financials(client.company_facts(cik), quarters=quarters)
    if not fundamentals or not fin.supported:
        return fin, build_evidence(ticker, fin)
    series = segment_series(load_segment_facts(client, cik))
    terms = (keywords or []) + _re.findall(r"[A-Za-z][A-Za-z0-9]+", thesis) + DEFAULT_KEYWORDS
    passages = retrieve_diverse(load_passages(client, cik), terms, k=k)
    return fin, build_evidence(ticker, fin, series, passages)


def main(argv: list[str] | None = None, *, provider=None, client=None, ledger=None) -> int:
    ap = argparse.ArgumentParser(prog="investment_ai")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("memo-draft")
    m.add_argument("ticker")
    m.add_argument("--thesis", required=True, help="your one-line thesis (memo SOP Step 1)")
    m.add_argument("--provider", default="anthropic", choices=["anthropic", "gemini"])
    m.add_argument("--surface", default="private", choices=["private", "lab"])
    m.add_argument("--lang", default="zh", choices=["zh", "en"])
    m.add_argument("--quarters", type=int, default=8)
    m.add_argument("--out", default=str(PRIVATE_MEMOS))
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--from-json", help="validate and render an output written in a chat session (no API call)")
    m.add_argument("--keywords", default="", help="comma-separated English keywords for filing passages, e.g. 'Waymo,TPU'")
    m.add_argument("--no-fundamentals", action="store_true", help="financial totals only (no segments or filing text)")
    th = sub.add_parser("theses")
    th.add_argument("ticker")
    th.add_argument("--lang", default="zh", choices=["zh", "en"])
    rv = sub.add_parser("memo-review")
    rv.add_argument("memo")
    rv.add_argument("--provider", default="anthropic", choices=["anthropic", "gemini"])
    rv.add_argument("--from-json")
    rv.add_argument("--lang", default="zh", choices=["zh", "en"])
    rv.add_argument("--portfolio")
    rv.add_argument("--rules")
    rv.add_argument("--context", action="append", default=[])
    rv.add_argument("--no-fundamentals", action="store_true")
    fz = sub.add_parser("memo-finalize")
    fz.add_argument("memo")
    fz.add_argument("--decision", required=True, choices=["watchlist", "paper", "eligible_for_gate"])
    fz.add_argument("--reason")
    fz.add_argument("--lang", default="zh", choices=["zh", "en"])
    r = sub.add_parser("memo-from-run")
    r.add_argument("run_id")
    r.add_argument("--quarters", type=int, default=8)
    r.add_argument("--out", default=str(PRIVATE_MEMOS))
    ev = sub.add_parser("eval-skeptic", help="run the adversarial eval set against a model (about $0.03-0.06 per case)")
    ev.add_argument("--provider", default="anthropic", choices=["anthropic", "gemini"])
    ev.add_argument("--only", default="", help="comma-separated case ids")
    ev.add_argument("--limit", type=int, default=0)
    ev.add_argument("--out", default="", help="results directory (default fixtures/evals/results)")
    ev.add_argument("--resume", action="store_true", help="continue from partial.json in the results directory")
    ev.add_argument("--smoke", action="store_true", help="only the 8 cases marked smoke (results go to results/smoke)")
    ev.add_argument("--max-usd", type=float, default=2.0,
                    help="stop starting new cases once this much has been spent, including resumed cases (default $2)")
    ev.add_argument("--max-minutes", type=float, default=8.0,
                    help="stop starting new cases after this many minutes (default 8); rerun with --resume")
    er = sub.add_parser("eval-memo-review", help="eval the Lab AI review of memo answers (about $0.01-0.02 per case)")
    er.add_argument("--provider", default="anthropic", choices=["anthropic", "gemini"])
    er.add_argument("--only", default="", help="comma-separated case ids")
    er.add_argument("--limit", type=int, default=0)
    er.add_argument("--out", default="", help="results directory (default fixtures/evals/results/review)")
    er.add_argument("--resume", action="store_true", help="continue from partial.json in the results directory")
    er.add_argument("--max-usd", type=float, default=0.5, help="stop starting new cases once this much has been spent")
    er.add_argument("--max-minutes", type=float, default=8.0, help="stop starting new cases after this many minutes")
    args = ap.parse_args(argv)
    load_env_file()

    if args.cmd in ("eval-skeptic", "eval-memo-review"):
        if args.cmd == "eval-skeptic":
            from . import evals
        else:
            from . import evals_review as evals
        cases = evals.load_cases()
        if args.only:
            wanted = set(args.only.split(","))
            cases = [c for c in cases if c["id"] in wanted]
        if args.limit:
            cases = cases[:args.limit]
        smoke = getattr(args, "smoke", False)
        if smoke:
            cases = [c for c in cases if c.get("smoke")]
        out_dir = Path(args.out) if args.out else (evals.RESULTS / "smoke" if smoke else evals.RESULTS)
        partial = out_dir / "partial.json"
        done = []
        if args.resume and partial.exists():
            done = json.loads(partial.read_text(encoding="utf-8"))["cases"]
            print(f"resuming: {len(done)} case(s) already done")
        elif partial.exists():
            print(f"note: {partial} exists; pass --resume to continue it (starting over)")
        client = client or EdgarClient(cache_dir=DEFAULT_CACHE)
        companies = {}

        def pack_for(ticker, thesis):  # exactly the Lab's evidence: numbers, segments and retrieved filing text
            from .lab_pack import lab_pack, load_lab_company
            if ticker not in companies:
                companies[ticker] = load_lab_company(client, ticker)
            return lab_pack(companies[ticker], thesis)

        def checkpoint(rows):
            out_dir.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps({"cases": rows}, ensure_ascii=False), encoding="utf-8")

        report = evals.run_eval(cases, pack_for, provider or make_provider(args.provider), ledger or Ledger(eval_budget=True),
                                done=done, checkpoint=checkpoint, max_minutes=args.max_minutes, max_usd=args.max_usd)
        s = report["summary"]
        if not report["complete"]:
            print(f"INCOMPLETE: {s['cases']}/{len(cases)} cases done, ${s['total_cost_usd']:.2f} so far. "
                  "Run the same command again with --resume (raise --max-usd only if you mean to spend more).")
            return 4
        path = evals.write_results(report, out_dir)
        partial.unlink(missing_ok=True)
        print(json.dumps({k: v for k, v in s.items() if k != "by_category"}, indent=1))
        print(f"{'PASSED' if s['passed'] else 'FAILED'} - results: {path}")
        return 0 if s["passed"] else 3

    if args.cmd == "theses":
        client = client or EdgarClient(cache_dir=DEFAULT_CACHE)
        name = build_financials(client.company_facts(client.cik_for(args.ticker)), quarters=1).name
        for t in examples(args.ticker, name, args.lang):
            print(f"[{t['angle']}] {t['text']}")
        return 0

    if args.cmd == "memo-review":
        path = Path(args.memo)
        pm = parse_memo(path.read_text(encoding="utf-8"))
        client = client or EdgarClient(cache_dir=DEFAULT_CACHE)
        fin, pack = build_pack(client, pm.ticker, pm.one_liner, None, not args.no_fundamentals)
        ledger = ledger or Ledger()
        if args.from_json:
            result = evaluate_supplied_review(pm, pack, json.loads(Path(args.from_json).read_text(encoding="utf-8")),
                                              ledger, args.lang)
        else:
            result = run_memo_review(pm, pack, provider or make_provider(args.provider), ledger, language=args.lang)
        if not result.ok:
            print(f"No review written: {result.error}")
            if result.report:
                print(result.report.feedback())
            return 2
        snapshot = ctx = rules = None
        if args.portfolio and args.rules:
            snapshot, ctx, _ = load_portfolio(args.portfolio)
            for cpath in args.context:
                if Path(cpath).exists():
                    ctx = merge_context(ctx, load_context(cpath))
                else:
                    print(f"note: context file not found, skipped: {cpath}")
            rules = load_rule_set(args.rules)
        position = position_check(pm.ticker, pm.target_weight, snapshot, rules, ctx, args.lang)
        write_review_into_memo(path, render_review(result, position, result.runs[-1], args.lang))
        entry = memo_status.upsert(pm.ticker, status="reviewed", invalidation_count=len(pm.invalidation),
                                   review_date=pm.review_date, has_unresolved_review_issues=result.unresolved)
        print(f"review written to {path}; unresolved={result.unresolved}; status: {entry}")
        return 0

    if args.cmd == "memo-finalize":
        path = Path(args.memo)
        pm = parse_memo(path.read_text(encoding="utf-8"))
        st = memo_status.load().get(pm.ticker, {})
        reviewed = "## Step 6 AI" in path.read_text(encoding="utf-8")
        review_passed = (not st.get("has_unresolved_review_issues", True)) if reviewed else None
        try:
            memo = finalize(pm, MemoDecision(args.decision), review_passed=review_passed, reason=args.reason)
        except TransitionError as e:
            print(f"Cannot finalise: {e}")
            return 2
        write_final_into_memo(path, render_final(memo, args.reason, args.lang))
        entry = memo_status.upsert(pm.ticker, status="final", decision=args.decision,
                                   invalidation_count=len(pm.invalidation), review_date=pm.review_date)
        print(f"finalised: {args.decision}; status: {entry}")
        return 0

    if args.cmd == "memo-from-run":
        ledger = ledger or Ledger()
        run = next((x for x in ledger.runs() if x.id == args.run_id), None)
        if run is None or not run.output:
            print(f"run {args.run_id} not found or has no output")
            return 1
        ticker, thesis = run.input["ticker"], run.input["thesis"]
        client = client or EdgarClient(cache_dir=DEFAULT_CACHE)
        fin, pack = build_pack(client, ticker, thesis, run.input.get("keywords") or None,
                               run.input.get("fundamentals", False), args.quarters)
        output, report = validate_output(run.output, pack, user_thesis=thesis)
        if not report.ok:
            print("run does not pass current validation:\n" + report.feedback())
            return 2
        md = render_memo(ticker, thesis, fin, pack, output, run, language=run.input.get("language", "zh"))
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{ticker}_memo_draft_{run.at.date()}_{run.id[:8]}.md"
        path.write_text(md, encoding="utf-8")
        print(f"wrote {path}")
        return 0

    client = client or EdgarClient(cache_dir=DEFAULT_CACHE)
    kw = [x.strip() for x in args.keywords.split(",") if x.strip()]
    fin, pack = build_pack(client, args.ticker, args.thesis, kw, not args.no_fundamentals, args.quarters)
    if not fin.supported:
        print(f"{args.ticker}: not supported — {fin.reason}")
        return 1

    if args.dry_run:
        print(system_prompt(args.lang))
        print("-----")
        print(user_prompt(pack, args.thesis, args.lang))
        return 0

    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        result = evaluate_supplied(pack, args.thesis, data, ledger or Ledger(), surface=args.surface, language=args.lang,
                                   meta={"keywords": kw, "fundamentals": not args.no_fundamentals})
    else:
        result = run_research_skeptic(pack, args.thesis, provider or make_provider(args.provider), ledger or Ledger(),
                                      surface=args.surface, language=args.lang,
                                      meta={"keywords": kw, "fundamentals": not args.no_fundamentals})
    last = result.runs[-1] if result.runs else None
    if not result.ok:
        print(f"No draft written: {result.error}")
        if result.report and not result.report.ok:
            print(result.report.feedback())
        return 2
    md = render_memo(args.ticker, args.thesis, fin, pack, result.output, last, language=args.lang)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.ticker.upper()}_memo_draft_{date.today()}.md"
    path.write_text(md, encoding="utf-8")
    spent = sum(r.cost_usd for r in result.runs)
    print(f"wrote {path} ({len(result.runs)} call(s), ${spent:.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
