"""Pure domain logic for the investment discipline system.

No IO, no database, no network. Everything here is deterministic and testable.
See docs/DESIGN.md §6.
"""

from .engine import evaluate
from .gate import evaluate_trade
from .models import (
    Asset,
    Context,
    Position,
    Rule,
    RuleSet,
    Severity,
    Sleeve,
    Snapshot,
    TradeProposal,
    Violation,
)

__all__ = [
    "Asset",
    "Context",
    "Position",
    "Rule",
    "RuleSet",
    "Severity",
    "Sleeve",
    "Snapshot",
    "TradeProposal",
    "Violation",
    "evaluate",
    "evaluate_trade",
]
