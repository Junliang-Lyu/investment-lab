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
    """Monthly budget over the runs in the ledger file.

    Offline evals have their own budget (DESIGN §11: "离线 eval 用单独的 key 和预算，不计入"): a ledger made with
    eval_budget=True counts only surface="eval" runs against EVAL_MONTHLY_BUDGET_USD (default $10); the normal
    ledger counts everything else against LLM_MONTHLY_BUDGET_USD (default $5). Each eval run also has --max-usd.
    """

    def __init__(self, path: str | Path | None = None, monthly_budget_usd: float | None = None, *,
                 eval_budget: bool = False):
        self.path = Path(path) if path else DEFAULT_LEDGER
        self.eval_budget = eval_budget
        env_budget = os.environ.get("EVAL_MONTHLY_BUDGET_USD" if eval_budget else "LLM_MONTHLY_BUDGET_USD")
        default = 10.0 if eval_budget else 5.0
        self.monthly_budget = monthly_budget_usd if monthly_budget_usd is not None else float(env_budget or default)

    def runs(self) -> list[AIRun]:
        if not self.path.exists():
            return []
        return [AIRun.model_validate_json(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def spent_this_month(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return sum(r.cost_usd for r in self.runs() if (r.at.year, r.at.month) == (now.year, now.month)
                   and (r.surface == "eval") == self.eval_budget)

    def check(self, estimated_cost: float) -> None:
        spent = self.spent_this_month()
        if spent + estimated_cost > self.monthly_budget + 1e-9:
            name = "eval budget (EVAL_MONTHLY_BUDGET_USD)" if self.eval_budget else "LLM budget (LLM_MONTHLY_BUDGET_USD)"
            raise BudgetExceeded(f"monthly {name} ${self.monthly_budget:.2f} would be exceeded "
                                 f"(spent ${spent:.4f}, this call up to ${estimated_cost:.4f})")

    def amend(self, run: AIRun) -> None:
        """Update the validation of a run already recorded (spending is unchanged)."""
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i] and f'"id":"{run.id}"' in lines[i].replace(" ", ""):
                lines[i] = run.model_dump_json()
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return

    def record(self, run: AIRun) -> AIRun:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(run.model_dump_json() + "\n")
        return run
