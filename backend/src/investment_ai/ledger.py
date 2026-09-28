"""Record of every model call (the local stand-in for the ai_runs table) and
the monthly budget check. See DESIGN §7 and §11.

Stored as JSON lines in private-data/ai_runs.jsonl because private runs may
contain holdings and theses.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_LEDGER = Path(__file__).resolve().parents[3] / "private-data" / "ai_runs.jsonl"


class BudgetExceeded(RuntimeError):
    pass


class AIRun(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    surface: str
    task: str
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    input: dict
    output: dict | None = None
    validation: dict | None = None
    status: str  # ok | invalid | error | budget_blocked
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None


class Ledger:
    def __init__(self, path: str | Path | None = None, monthly_budget_usd: float | None = None):
        self.path = Path(path) if path else DEFAULT_LEDGER
        env_budget = os.environ.get("LLM_MONTHLY_BUDGET_USD")
        self.monthly_budget = monthly_budget_usd if monthly_budget_usd is not None else float(env_budget or 5)

    def runs(self) -> list[AIRun]:
        if not self.path.exists():
            return []
        return [AIRun.model_validate_json(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def spent_this_month(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return sum(r.cost_usd for r in self.runs() if (r.at.year, r.at.month) == (now.year, now.month))

    def check(self, estimated_cost: float) -> None:
        spent = self.spent_this_month()
        if spent + estimated_cost > self.monthly_budget + 1e-9:
            raise BudgetExceeded(f"monthly LLM budget ${self.monthly_budget:.2f} would be exceeded "
                                 f"(spent ${spent:.4f}, this call up to ${estimated_cost:.4f})")

    def record(self, run: AIRun) -> AIRun:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(run.model_dump_json() + "\n")
        return run
