"""Snapshot evaluation. See docs/DESIGN.md §6.2."""

from __future__ import annotations

from .models import Context, RuleSet, Snapshot, Violation
from .rules import RULES


class UnknownRuleError(ValueError):
    pass


def evaluate(snapshot: Snapshot, rule_set: RuleSet, ctx: Context | None = None,
             only: list[str] | None = None) -> list[Violation]:
    """Evaluate a snapshot against a rule set. Pure and deterministic.

    Unknown rule codes fail loudly so that a typo in a rule file never
    silently disables a check.
    """
    ctx = ctx or Context()
    out: list[Violation] = []
    for rule in rule_set.rules:
        if rule.code not in RULES:
            raise UnknownRuleError(f"unknown rule code: {rule.code}")
        if not rule.enabled or (only is not None and rule.code not in only):
            continue
        out.extend(RULES[rule.code](snapshot, rule, ctx))
    out.sort(key=lambda v: (-v.severity.rank, v.rule_code, v.symbol or ""))
    return out
