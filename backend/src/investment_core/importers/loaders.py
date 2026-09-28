from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import yaml

from ..models import (
    Asset,
    Context,
    GateRecord,
    MemoSummary,
    Position,
    RuleSet,
    Snapshot,
    WatchEntry,
)


def load_rule_set(path: str | Path) -> RuleSet:
    with open(path, encoding="utf-8") as f:
        return RuleSet.model_validate(yaml.safe_load(f))


def load_assets(path: str | Path) -> dict[str, Asset]:
    """Load an asset classification file: {assets: [{symbol, sleeve, exposure_tags, ...}]}."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {a["symbol"]: Asset.model_validate(a) for a in data.get("assets", [])}


def context_from_dict(data: dict) -> Context:
    """Build a Context from the shared keys of portfolio and context files."""
    return Context(
        assets={a["symbol"]: Asset.model_validate(a) for a in data.get("assets", []) or []},
        memos={m["symbol"]: MemoSummary.model_validate(m) for m in data.get("memos", []) or []},
        watchlist={w["symbol"]: WatchEntry.model_validate(w) for w in data.get("watchlist", []) or []},
        last_review_date=data.get("last_review_date"),
        gate_records=[GateRecord.model_validate(g) for g in data.get("gate_records", []) or []],
    )


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return (json.load(f) if path.suffix == ".json" else yaml.safe_load(f)) or {}


def load_context(path: str | Path) -> Context:
    """Load a context file (YAML/JSON) with assets, memos, watchlist, last_review_date, gate_records."""
    return context_from_dict(_read(Path(path)))


def merge_context(base: Context, extra: Context) -> Context:
    """Overlay `extra` on `base`: dict entries are merged, scalars/lists replaced when set."""
    return base.model_copy(update={
        "assets": base.assets | extra.assets,
        "memos": base.memos | extra.memos,
        "watchlist": base.watchlist | extra.watchlist,
        "last_review_date": extra.last_review_date or base.last_review_date,
        "gate_records": base.gate_records + extra.gate_records,
    })


def load_portfolio(path: str | Path) -> tuple[Snapshot, Context, dict]:
    """Load a portfolio file (JSON or YAML) into a snapshot and context.

    Format: see fixtures/demo_portfolios/*.json. Returns (snapshot, context, meta)
    where meta holds display fields such as name, description and price_date.
    """
    data = _read(Path(path))
    snapshot = Snapshot(
        as_of=data["as_of"],
        net_liquidation=data["net_liquidation"],
        cash=data["cash"],
        positions=[Position.model_validate(p) for p in data.get("positions", [])],
        source=data.get("source", "fixture"),
    )
    meta = {k: data.get(k) for k in ("id", "name", "description", "price_date", "fictional")}
    return snapshot, context_from_dict(data), meta


def parse_simple_csv(text: str, as_of: date) -> Snapshot:
    """Parse the simple CSV format: symbol,quantity,market_value[,cost_basis,unrealized_pnl,currency].

    A row with symbol CASH gives cash (market_value column). A row with symbol
    NAV gives net liquidation; otherwise it is cash + sum of market values.
    """
    reader = csv.DictReader(io.StringIO(text.strip()))
    required = {"symbol", "quantity", "market_value"}
    if not reader.fieldnames or not required <= {h.strip() for h in reader.fieldnames}:
        raise ValueError(f"CSV must have columns {sorted(required)}")

    def num(v: str | None) -> float | None:
        v = (v or "").strip().replace(",", "").replace("$", "")
        return float(v) if v else None

    cash, nav, positions = 0.0, None, []
    for row in reader:
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        symbol = row["symbol"].upper()
        mv = num(row.get("market_value")) or 0.0
        if symbol == "CASH":
            cash += mv
        elif symbol == "NAV":
            nav = mv
        else:
            positions.append(Position(
                symbol=symbol,
                quantity=num(row.get("quantity")) or 0.0,
                market_value=mv,
                cost_basis=num(row.get("cost_basis")),
                unrealized_pnl=num(row.get("unrealized_pnl")),
                currency=row.get("currency") or "USD",
            ))
    if nav is None:
        nav = cash + sum(p.market_value for p in positions)
    return Snapshot(as_of=as_of, net_liquidation=nav, cash=cash, positions=positions, source="csv")
