"""Example theses for users without a view (DESIGN §11)."""

from __future__ import annotations

from pathlib import Path

import yaml

FILE = Path(__file__).resolve().parents[3] / "fixtures" / "example_theses.yaml"


def examples(ticker: str, company: str, language: str = "zh", path: Path | None = None) -> list[dict]:
    data = yaml.safe_load((path or FILE).read_text(encoding="utf-8"))
    items = data.get("by_ticker", {}).get(ticker.upper(), []) + data.get("generic", [])
    return [{"id": i["id"], "angle": i["angle"], "text": i[language].format(company=company)} for i in items]
