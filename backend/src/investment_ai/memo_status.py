"""Memo status store used as a --context file by the rule engine.

private-data/memo_status.yaml, format: {memos: [{symbol, status, decision, ...}]}
(the same "memos" key load_context() already reads).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

DEFAULT_STATUS = Path(__file__).resolve().parents[3] / "private-data" / "memo_status.yaml"


def load(path: Path | None = None) -> dict[str, dict]:
    path = path or DEFAULT_STATUS
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {m["symbol"]: m for m in data.get("memos", [])}


def upsert(symbol: str, path: Path | None = None, **fields) -> dict:
    path = path or DEFAULT_STATUS
    memos = load(path)
    entry = {**memos.get(symbol, {"symbol": symbol}), **{k: v for k, v in fields.items() if v is not None}}
    for k, v in list(entry.items()):
        if isinstance(v, date):
            entry[k] = v.isoformat()
    memos[symbol] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Written by investment_ai (memo-review / memo-finalize). Pass with --context to the rule engine.\n"
    path.write_text(header + yaml.safe_dump({"memos": list(memos.values())}, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return entry
