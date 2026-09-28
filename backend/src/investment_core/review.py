"""Portfolio review report (deterministic parts). See docs/DESIGN.md §6.5.

Follows the fixed output of personal_investment_system_v0/组合复盘_SOP.md.
The per-holding thesis re-test (SOP §9) is left for the user to write.
"""

from __future__ import annotations

from collections import defaultdict

from .engine import evaluate
from .models import Context, RuleSet, Sleeve, Snapshot


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def build_review(snapshot: Snapshot, rule_set: RuleSet, ctx: Context | None = None) -> str:
    ctx = ctx or Context()
    s = snapshot
    nav, invested = s.net_liquidation, s.invested
    positions = sorted(s.positions, key=lambda p: p.market_value, reverse=True)
    violations = evaluate(s, rule_set, ctx)

    lines = [f"# Portfolio review — {s.as_of}", "", f"Rule set: `{rule_set.version}` ({rule_set.status})", ""]

    lines += ["## Account snapshot", "",
              f"- Net liquidation: ${nav:,.2f}",
              f"- Cash: ${s.cash:,.2f} ({_pct(s.cash / nav)})",
              f"- Invested: ${invested:,.2f} ({_pct(invested / nav)})"]
    pnl = [p.unrealized_pnl for p in s.positions if p.unrealized_pnl is not None]
    if pnl:
        lines.append(f"- Unrealized P&L: ${sum(pnl):,.2f}")
    lines.append("")

    lines += ["## Holdings", "", "| Symbol | Sleeve | Market value | % of net value | % of invested |",
              "|---|---|---:|---:|---:|"]
    for p in positions:
        lines.append(f"| {p.symbol} | {ctx.asset(p.symbol).sleeve.value} | ${p.market_value:,.2f} | "
                     f"{_pct(s.weight_nav(p.market_value))} | {_pct(s.weight_invested(p.market_value))} |")
    lines.append("")

    top3 = sum(p.market_value for p in positions[:3])
    by_sleeve: dict[str, float] = defaultdict(float)
    by_tag: dict[str, float] = defaultdict(float)
    for p in s.positions:
        a = ctx.asset(p.symbol)
        by_sleeve[a.sleeve.value] += p.market_value
        for t in a.exposure_tags:
            by_tag[t] += p.market_value
    lines += ["## Concentration and exposure", ""]
    if positions:
        largest = positions[0]
        lines += [f"- Largest holding: {largest.symbol}, {_pct(s.weight_nav(largest.market_value))} of net value, "
                  f"{_pct(s.weight_invested(largest.market_value))} of invested",
                  f"- Top 3: {_pct(s.weight_nav(top3))} of net value, {_pct(s.weight_invested(top3))} of invested"]
    for sleeve in Sleeve:
        if by_sleeve.get(sleeve.value):
            lines.append(f"- {sleeve.value.capitalize()}: {_pct(s.weight_nav(by_sleeve[sleeve.value]))} of net value")
    for tag, mv in sorted(by_tag.items(), key=lambda kv: -kv[1]):
        lines.append(f"- Exposure `{tag}`: {_pct(s.weight_nav(mv))} of net value")
    lines.append("")

    lines += ["## Drawdown scenarios", ""]
    if positions:
        largest = positions[0]
        for drop in (0.10, 0.20, 0.30):
            impact = largest.market_value * drop / nav
            lines.append(f"- {largest.symbol} falls {int(drop * 100)}%: net value -{_pct(impact)}")
    else:
        lines.append("- No positions")
    lines.append("")

    lines += ["## Rule findings", ""]
    if violations:
        for v in violations:
            lines.append(f"- [{v.severity.value}] `{v.rule_code}` {v.message}")
    else:
        lines.append("- None")
    lines.append("")

    lines += ["## Holding thesis re-test (write by hand)", ""]
    for p in positions:
        if ctx.asset(p.symbol).sleeve == Sleeve.SATELLITE:
            lines += [f"### {p.symbol}", "", "- Original thesis:", "- Do current facts still support it:",
                      "- New risks:", "- Invalidation condition triggered:", "- Research needed:",
                      "- Status: keep watching / research needed / weak logic / review triggered", ""]

    lines += ["## Next review focus", "", "1.", "2.", "3.", ""]
    return "\n".join(lines)
