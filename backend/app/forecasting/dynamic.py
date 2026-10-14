from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from app.data.loaders import load
from app.forecasting.fallback import monthly_wide
from app.forecasting.stat_methods import METHODS

METHOD_NAMES = tuple(["auto"] + list(METHODS))


def item_forecast(svc, item_id: int, method: str = "auto") -> Dict[str, Any]:
    if method not in METHOD_NAMES:
        raise ValueError(f"method must be one of {list(METHOD_NAMES)}")
    row = svc.item_row(item_id)
    if row is None:
        raise KeyError("Item not found")
    ts = load("target_time_series", columns=["item_id", "date", "target"])
    wide = monthly_wide(ts, "item_id")
    if item_id not in wide.index:
        raise KeyError("No history for item")
    history = wide.loc[[item_id], : pd.Timestamp(svc.cutoff)].to_numpy(dtype=float)
    chosen = _choose_method(history, row) if method == "auto" else method
    forecasts = {name: METHODS[name](history).ravel() for name in METHODS}
    selected = forecasts[chosen]
    months = [pd.Timestamp(m).strftime("%Y-%m-%d") for m in pd.date_range(pd.Timestamp(svc.first_month), periods=12, freq="MS")]
    current = {p["month"]: p for p in svc.item_detail(item_id, {})["plan"]}
    rows = []
    for i, month in enumerate(months):
        cur = current.get(month, {})
        rows.append({
            "month": month,
            "dynamic": _clean(selected[i]),
            "current_final": cur.get("final"),
            "current_ml": cur.get("ml"),
            "baseline": cur.get("baseline"),
            "delta_to_current": _clean(selected[i] - float(cur.get("final") or 0.0)) if cur.get("final") is not None and not np.isnan(selected[i]) else None,
        })
    methods = [{"method": name, "first_month": _clean(values[0]), "units_12m": _clean(np.nansum(values))} for name, values in forecasts.items()]
    product = product_context(svc, str(row["product_id"]), {item_id: selected})
    return {
        "item_id": int(item_id),
        "product_id": str(row["product_id"]),
        "channel": str(row["channel"]),
        "method": chosen,
        "requested_method": method,
        "methods": methods,
        "rows": rows,
        "history_points": int(np.isfinite(history).sum()),
        "nonzero_share": _nonzero_share(history),
        "explanation": _explain(chosen, history, row),
        "product": product,
    }


def product_context(svc, product_id: str, replacements: Dict[int, np.ndarray]) -> Dict[str, Any]:
    cons = svc.consensus({})
    rows = cons[cons["product_id"] == product_id].copy()
    for item_id, values in replacements.items():
        mask = rows["item_id"] == item_id
        idx = rows.loc[mask].sort_values("month").index[: len(values)]
        rows.loc[idx, "dynamic_qty"] = values[: len(idx)]
    rows["dynamic_qty"] = rows["dynamic_qty"].fillna(rows["consensus_qty"])
    by_month = rows.groupby("month").agg(current_units=("consensus_qty", "sum"), dynamic_units=("dynamic_qty", "sum")).reset_index()
    by_channel = rows.groupby("channel").agg(current_units=("consensus_qty", "sum"), dynamic_units=("dynamic_qty", "sum")).reset_index()
    return {
        "product_id": product_id,
        "items": int(rows["item_id"].nunique()),
        "months": [{"month": r.month, "current_units": float(r.current_units), "dynamic_units": float(r.dynamic_units), "delta": float(r.dynamic_units - r.current_units)} for r in by_month.itertuples()],
        "channels": [{"channel": r.channel, "current_units": float(r.current_units), "dynamic_units": float(r.dynamic_units), "delta": float(r.dynamic_units - r.current_units)} for r in by_channel.itertuples()],
    }


def _choose_method(history: np.ndarray, row) -> str:
    nz = _nonzero_share(history)
    phase = str(row.get("phase", ""))
    if nz < 0.35:
        return "croston_sba"
    if phase == "POST_EOL":
        return "ma3"
    if np.isfinite(history).sum() >= 24:
        return "combo_sn_ma12"
    return "ma6"


def _nonzero_share(history: np.ndarray) -> float:
    values = history[np.isfinite(history)]
    if len(values) == 0:
        return 0.0
    return float((values > 0).mean())


def _explain(method: str, history: np.ndarray, row) -> List[str]:
    return [
        f"Selected {method} using {int(np.isfinite(history).sum())} observed monthly demand points.",
        f"The item is in phase {row.get('phase')} and channel {row.get('channel')}.",
        "This recompute is CPU-only and does not change the audited consensus until a planner records a decision.",
    ]


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if np.isnan(value):
            return None
    except TypeError:
        pass
    if isinstance(value, (np.floating, float)):
        return float(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value
