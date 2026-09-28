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
        return cls(**kw)
