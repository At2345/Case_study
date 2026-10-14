"""What-if scenarios: copy the consensus, apply edits, score value, margin, OTIF exposure and safety stock.

Edits are applied in order:
  {"type": "pct", "scope": "all"|"channel"|"product", "value": <channel or product_id>, "pct": 10, "month": null|"YYYY-MM-01"}
  {"type": "item", "item_id": 123, "month": "YYYY-MM-01", "qty": 500}
Safety stock scales with the plan: the conformal band is a ratio of the forecast, so sigma moves with month-1 units.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from app.rules import otif

MAX_EDITS = 200


def apply_edits(cons: pd.DataFrame, edits: List[Dict[str, Any]]) -> pd.DataFrame:
    out = cons.copy()
    for e in edits:
        month = out["month"] == e["month"] if e.get("month") else pd.Series(True, index=out.index)
        if e["type"] == "pct":
            scope = e.get("scope", "all")
            mask = month.copy()
            if scope == "channel":
                mask &= out["channel"] == e["value"]
            elif scope == "product":
                mask &= out["product_id"] == e["value"]
            out.loc[mask, "consensus_qty"] = (out.loc[mask, "consensus_qty"] * (1 + float(e["pct"]) / 100.0)).clip(lower=0)
        elif e["type"] == "item":
            mask = month & (out["item_id"] == int(e["item_id"]))
            out.loc[mask, "consensus_qty"] = max(float(e["qty"]), 0.0)
    return out


def validate_edits(edits: List[Dict[str, Any]], svc) -> None:
    if len(edits) > MAX_EDITS:
        raise ValueError(f"at most {MAX_EDITS} edits per scenario")
    for e in edits:
        if e.get("month") and not svc.has_month_any(e["month"]):
            raise ValueError(f"no plan for month {e['month']}")
        if e["type"] == "item" and not svc.has_item(int(e["item_id"])):
            raise ValueError(f"unknown item {e['item_id']}")
        if e["type"] == "pct" and e.get("scope", "all") != "all" and not e.get("value"):
            raise ValueError("a channel or product is required for a scoped % change")


def score(svc, cons: pd.DataFrame, terms: Dict[str, Any]) -> Dict[str, Any]:
    t = svc.triage[["item_id", "sales_price", "purchase_price"]].reset_index(drop=True)
    d = cons.merge(t, on="item_id", how="left")
    price, cost = d["sales_price"].fillna(0), d["purchase_price"].fillna(0)
    d["value"] = d["consensus_qty"] * price
    d["margin"] = d["consensus_qty"] * (price - cost)
    first = d[d["month"] == svc.first_month]
    by_channel = {c: {"units": float(g["consensus_qty"].sum()), "value_gbp": float(g["value"].sum())}
                  for c, g in d.groupby("channel")}
    by_month = [{"month": m, "units": float(g["consensus_qty"].sum()), "value_gbp": float(g["value"].sum())}
                for m, g in d.groupby("month")]
    ot = otif.from_plan(svc, cons, terms)
    ss = _safety(svc, first)
    return {
        "units_12m": float(d["consensus_qty"].sum()), "value_12m_gbp": float(d["value"].sum()),
        "margin_12m_gbp": float(d["margin"].sum()), "units_m1": float(first["consensus_qty"].sum()),
        "value_m1_gbp": float(first["value"].sum()), "otif_items_at_risk": len(ot["rows"]),
        "otif_penalty_gbp": ot["total_penalty_gbp"], **ss, "by_channel": by_channel, "by_month": by_month,
    }


def _safety(svc, first: pd.DataFrame) -> Dict[str, Any]:
    ss = svc.extras.get("safety")
    if ss is None:
        return {"safety_stock_units": None, "holding_cost_gbp": None}
    j = ss[["item_id", "final_m1", "ss_dynamic", "holding_dynamic_gbp"]].merge(
        first[["item_id", "consensus_qty"]], on="item_id", how="left")
    ratio = np.where(j["final_m1"] > 0, j["consensus_qty"].fillna(j["final_m1"]) / j["final_m1"].where(j["final_m1"] > 0), 1.0)
    return {"safety_stock_units": float((j["ss_dynamic"] * ratio).sum()), "holding_cost_gbp": float((j["holding_dynamic_gbp"] * ratio).sum())}


def compare(svc, base: pd.DataFrame, scenarios: List[Dict[str, Any]], terms: Dict[str, Any]) -> Dict[str, Any]:
    ref = score(svc, base, terms)
    cols = [{"id": "current", "name": "Current consensus", "metrics": ref, "delta": None}]
    keys = ["units_12m", "value_12m_gbp", "margin_12m_gbp", "units_m1", "value_m1_gbp", "otif_items_at_risk",
            "otif_penalty_gbp", "safety_stock_units", "holding_cost_gbp"]
    for s in scenarios:
        m = score(svc, apply_edits(base, s["edits"]), terms)
        delta = {k: (None if m[k] is None or ref[k] is None else m[k] - ref[k]) for k in keys}
        cols.append({"id": s["id"], "name": s["name"], "created_by": s.get("created_by"), "edits": s["edits"], "metrics": m, "delta": delta})
    return {"cutoff": svc.cutoff, "first_month": svc.first_month, "columns": cols, "metric_keys": keys}
