from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[3]

# Companies the public Lab can look up. A fixed list keeps EDGAR traffic and
# abuse bounded; extend deliberately.
CURATED = ["GOOG", "MSFT", "AAPL", "AMZN", "META", "NVDA", "TSLA", "MU", "COST", "V"]


class Settings(BaseModel):
    fixtures_dir: Path = REPO / "fixtures"
    cache_dir: Path = REPO / "data-cache" / "edgar"
    allowed_origins: list[str] = Field(default_factory=list)
    api_docs: bool = False
    rate_per_minute: int = 60
    snapshot_ttl_seconds: int = 6 * 3600
    curated: list[str] = Field(default_factory=lambda: list(CURATED))
    # Public AI skeptic (off unless LAB_SKEPTIC_ENABLED=1 and ANTHROPIC_API_KEY is set).
    skeptic_enabled: bool = False
    lab_data_dir: Path = REPO / "data-cache" / "lab"
    lab_daily_budget_usd: float = 0.5
    monthly_budget_usd: float = 5.0
    skeptic_per_ip_daily: int = 3
    retention_days: int = 30
    # Lab memo workflow (DESIGN §11.5); on whenever the skeptic is on.
    memo_retention_days: int = 180
    memo_per_ip_daily: int = 10
    memo_review_per_ip_daily: int = 3
    memo_max: int = 5000
    # Snapshots of companies outside the curated list (any SEC-registered US ticker): bounded per visitor and per day.
    custom_per_ip_daily: int = 20
    custom_global_daily: int = 150
    custom_cache_max: int = 40
    # Quarter explanations on the company page: a visitor quota of their own, and part of the day's budget stays
    # reserved for the skeptic (explanations stop when less than this is left today).
    explain_per_ip_daily: int = 12
    explain_reserve_usd: float = 0.15

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
        kw = dict(allowed_origins=origins, api_docs=os.environ.get("API_DOCS") == "1")
        if os.environ.get("EDGAR_CACHE_DIR"):
            kw["cache_dir"] = Path(os.environ["EDGAR_CACHE_DIR"])
        if os.environ.get("FIXTURES_DIR"):
            kw["fixtures_dir"] = Path(os.environ["FIXTURES_DIR"])
        if os.environ.get("RATE_PER_MINUTE"):
            kw["rate_per_minute"] = int(os.environ["RATE_PER_MINUTE"])
        kw["skeptic_enabled"] = os.environ.get("LAB_SKEPTIC_ENABLED") == "1"
        for env, field, cast in [("LAB_DATA_DIR", "lab_data_dir", Path), ("LAB_DAILY_BUDGET_USD", "lab_daily_budget_usd", float),
                                 ("LLM_MONTHLY_BUDGET_USD", "monthly_budget_usd", float),
                                 ("LAB_SKEPTIC_PER_IP_DAILY", "skeptic_per_ip_daily", int),
                                 ("LAB_RETENTION_DAYS", "retention_days", int),
                                 ("LAB_MEMO_RETENTION_DAYS", "memo_retention_days", int),
                                 ("LAB_MEMO_PER_IP_DAILY", "memo_per_ip_daily", int),
                                 ("LAB_MEMO_REVIEW_PER_IP_DAILY", "memo_review_per_ip_daily", int),
                                 ("LAB_MEMO_MAX", "memo_max", int),
                                 ("LAB_CUSTOM_PER_IP_DAILY", "custom_per_ip_daily", int),
                                 ("LAB_CUSTOM_GLOBAL_DAILY", "custom_global_daily", int),
                                 ("LAB_EXPLAIN_PER_IP_DAILY", "explain_per_ip_daily", int),
                                 ("LAB_EXPLAIN_RESERVE_USD", "explain_reserve_usd", float)]:
            if os.environ.get(env):
                kw[field] = cast(os.environ[env])
        return cls(**kw)
