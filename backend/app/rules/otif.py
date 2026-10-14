"""F-B2B-03 OTIF check against a configurable terms table (clauses are not in the data, Q4).

An order book month is at OTIF risk when the consensus plan covers less than `min_fill` of the units
already booked; the fine is `penalty_pct` of the sales value of the uncovered units.
Terms come from STORE_DIR/otif_terms.json ({"default": {...}, "channels": {...}}) or the defaults below.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_TERMS = {"min_fill": 0.95, "penalty_pct": 0.05}


@dataclass(frozen=True)
class OtifTerms:
    min_fill: float
    penalty_pct: float


def load_terms(store_dir: Path) -> Dict[str, Any]:
    path = Path(store_dir) / "otif_terms.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    return {"default": {**DEFAULT_TERMS, **cfg.get("default", {})}, "channels": cfg.get("channels", {"B2B wo Amazon": DEFAULT_TERMS}),
            "source": str(path) if path.exists() else "built-in assumption (Q4)"}


def terms_for(cfg: Dict[str, Any], channel: str) -> OtifTerms:
    t = {**cfg["default"], **cfg["channels"].get(channel, {})}
    return OtifTerms(float(t["min_fill"]), float(t["penalty_pct"]))


def check(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """rows: item_id, channel, month, booked, plan, sales_price. Returns only rows at risk, worst first."""
    out = []
    for r in rows:
        if r["channel"] not in cfg["channels"] or r["booked"] <= 0:
            continue
        t = terms_for(cfg, r["channel"])
        fill = min(r["plan"] / r["booked"], 1.0)
        if fill >= t.min_fill:
            continue
        short = r["booked"] - r["plan"]
        out.append({**r, "fill_rate": fill, "shortfall_units": short, "penalty_gbp": short * r["sales_price"] * t.penalty_pct,
                    "terms": asdict(t)})
    return sorted(out, key=lambda x: -x["penalty_gbp"])


def from_plan(svc, cons, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """OTIF risk of a consensus frame (item_id, product_id, channel, month, consensus_qty) against the order book."""
    import pandas as pd
    ph = svc.phasing_items
    if ph.empty:
        return {"terms": cfg, "rows": [], "total_penalty_gbp": 0.0}
    ph = ph.assign(month=pd.to_datetime(ph["month"]).dt.strftime("%Y-%m-%d"))
    j = cons.merge(ph[["item_id", "month", "booked_qty"]], on=["item_id", "month"], how="inner")
    price = svc.triage["sales_price"]
    rows = [{"item_id": int(r.item_id), "product_id": r.product_id, "channel": r.channel, "month": r.month,
             "booked": float(r.booked_qty), "plan": float(r.consensus_qty), "sales_price": float(price.get(r.item_id, 0.0))}
            for r in j.itertuples()]
    risk = check(rows, cfg)
    return {"terms": cfg, "rows": risk, "total_penalty_gbp": float(sum(r["penalty_gbp"] for r in risk))}
