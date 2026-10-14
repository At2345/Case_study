"""Agentic forecasting: Research -> Process -> Forecast -> Inform.

Wraps the CPU-only dynamic forecast (`dynamic.item_forecast`) in an explicit
four-stage agent trace so the planner can see how the number was produced:
  research  - what the item's context says (history, order book, phase, promos)
  process   - how the method was chosen (holdout scoring) and reconciled
  forecast  - the 12-month dynamic forecast and band
  inform    - a deterministic brief, optionally enriched by the configured LLM
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.data.loaders import load
from app.forecasting import dynamic as dynamic_forecast
from app.forecasting.fallback import monthly_wide
from app.forecasting.stat_methods import METHODS

HOLDOUT = 3

INFORM_SYSTEM = (
    "You write short forecast briefs for a demand planner reviewing one item in a UK consumer-goods S&OP cycle. "
    "Use only the facts in the JSON the user sends. Do not add numbers, dates, causes or events that are not in the JSON. "
    "Write 2-3 sentences of plain business language: what the dynamic forecast says, why the method was chosen, "
    "and what the planner should check. No markdown, no lists, no greetings."
)


def run(svc, item_id: int, method: str = "auto", llm: Optional[Any] = None) -> Dict[str, Any]:
    row = svc.item_row(item_id)
    if row is None:
        raise KeyError("Item not found")
    research = _research(svc, item_id, row)
    forecast = dynamic_forecast.item_forecast(svc, item_id, method)
    processed = _process(svc, item_id, row, forecast)
    informed = _inform(svc, item_id, row, forecast, processed, research, llm)
    return {
        "item_id": int(item_id),
        "product_id": str(row["product_id"]),
        "channel": str(row["channel"]),
        "stages": ["research", "process", "forecast", "inform"],
        "research": research,
        "process": processed,
        "forecast": forecast,
        "inform": informed,
        "actions": _actions(item_id, row),
    }


# ------------------------------------------------------------------ research
def _research(svc, item_id: int, row) -> Dict[str, Any]:
    detail = svc.item_detail(item_id, {})
    values = [h["actual"] for h in detail["history"] if h["actual"] is not None]
    phasing = svc.phasing_detail(item_id, {})
    m1 = phasing[0] if phasing else {}
    last3 = float(np.mean(values[-3:])) if len(values) >= 3 else None
    prior3 = float(np.mean(values[-6:-3])) if len(values) >= 6 else None
    trend = round((last3 - prior3) / prior3 * 100.0, 1) if last3 is not None and prior3 else None
    return {
        "history_points": len(values),
        "nonzero_share": round(float(np.mean([v > 0 for v in values])), 3) if values else 0.0,
        "last3_mean": _clean(last3),
        "prior3_mean": _clean(prior3),
        "trend_pct": _clean(trend),
        "booked_m1": _clean(m1.get("booked")),
        "carry_in_m1": _clean(m1.get("carry_in")),
        "phase": str(row.get("phase", "")),
        "months_since_launch": _clean(row.get("months_since_launch")),
        "months_to_eol": _clean(row.get("months_to_eol")),
        "channel": str(row.get("channel", "")),
        "abc_category": str(row.get("abc_category", "")),
        "promo_overlap": _promo_overlap(item_id, svc.first_month),
    }


def _promo_overlap(item_id: int, first_month: str) -> List[Dict[str, Any]]:
    try:
        promo = load("promotions", columns=["item_id", "promotion_name", "date_from", "date_to", "promotion_type"])
    except Exception:
        return []
    start = pd.Timestamp(first_month)
    end = start + pd.DateOffset(months=12)
    rows = promo[(promo["item_id"] == item_id) & (promo["date_from"] < end) & (promo["date_to"] >= start)]
    return [{"name": str(r.promotion_name), "type": str(r.promotion_type),
             "from": str(pd.Timestamp(r.date_from).date()), "to": str(pd.Timestamp(r.date_to).date())}
            for r in rows.head(5).itertuples()]


# ------------------------------------------------------------------ process
def _process(svc, item_id: int, row, forecast: Dict[str, Any]) -> Dict[str, Any]:
    history = _history(svc, item_id)
    scores = _score_methods(history)
    chosen = forecast["method"]
    return {
        "method_scores": scores,
        "chosen_method": chosen,
        "chosen_reason": _choose_reason(chosen, history, row),
        "product_reconciliation": forecast.get("product") or {},
    }


def _history(svc, item_id: int) -> np.ndarray:
    ts = load("target_time_series", columns=["item_id", "date", "target"])
    wide = monthly_wide(ts, "item_id")
    if item_id not in wide.index:
        raise KeyError("No history for item")
    return wide.loc[[item_id], : pd.Timestamp(svc.cutoff)].to_numpy(dtype=float)


def _score_methods(history: np.ndarray) -> List[Dict[str, Any]]:
    """Hold out the last HOLDOUT observed months; WAPE of each method on them."""
    values = history[np.isfinite(history)]
    if len(values) < 2 * HOLDOUT:
        return []
    t_obs = int(np.isfinite(history).sum())
    train = history[:, : t_obs - HOLDOUT]
    actual = history[:, t_obs - HOLDOUT : t_obs]
    out = []
    for name, fn in METHODS.items():
        try:
            pred = fn(train)[:, :HOLDOUT]
        except Exception:
            continue
        denom = np.nansum(np.abs(actual))
        wape = float(np.nansum(np.abs(actual - pred)) / denom) if denom > 0 else None
        out.append({"method": name, "holdout_wape": _clean(wape)})
    return sorted(out, key=lambda r: (r["holdout_wape"] is None, r["holdout_wape"] or 0.0))


def _choose_reason(method: str, history: np.ndarray, row) -> str:
    nz = dynamic_forecast._nonzero_share(history)
    phase = str(row.get("phase", ""))
    if nz < 0.35:
        return "Demand is intermittent (nonzero share below 35%); Croston SBA handles zero-heavy series."
    if phase == "POST_EOL":
        return "The item is past its EOL month; a short moving average tracks the run-out decay."
    if np.isfinite(history).sum() >= 24:
        return "At least 24 observed months: the seasonal/MA combo captures both seasonality and level."
    return "Fewer than 24 observed months: a 6-month moving average is the most stable choice."


# ------------------------------------------------------------------ inform
def _inform(svc, item_id: int, row, forecast: Dict[str, Any], processed: Dict[str, Any],
            research: Dict[str, Any], llm: Optional[Any]) -> Dict[str, Any]:
    brief = _brief(row, forecast, processed, research)
    text, source = brief, "deterministic"
    if llm is not None:
        try:
            payload = {
                "item_id": item_id, "channel": research["channel"], "phase": research["phase"],
                "method": processed["chosen_method"], "first_month_units": forecast["rows"][0]["dynamic"],
                "delta_vs_current": forecast["rows"][0]["delta_to_current"],
                "booked_m1": research["booked_m1"], "promo_overlap": research["promo_overlap"],
            }
            generated = (llm.generate(INFORM_SYSTEM, str(payload)) or "").strip()
            if generated:
                text, source = generated, "llm"
        except Exception:
            pass
    return {"brief": brief, "text": text, "source": source}


def _brief(row, forecast: Dict[str, Any], processed: Dict[str, Any], research: Dict[str, Any]) -> str:
    first = forecast["rows"][0]
    delta = first.get("delta_to_current")
    delta_txt = f", {delta:+,g} units vs the current plan" if delta is not None else ""
    booked = research.get("booked_m1")
    booked_txt = f" The order book already holds {booked:g} units for the first month." if booked else ""
    promo = research.get("promo_overlap") or []
    promo_txt = f" {len(promo)} promotion(s) overlap the forecast window." if promo else ""
    return (
        f"The dynamic forecast for {research['channel']} item {forecast['item_id']} uses "
        f"{processed['chosen_method']} ({processed['chosen_reason']}) and plans "
        f"{first['dynamic']:g} units in the first month{delta_txt}. "
        f"History: {research['history_points']} observed months, {research['nonzero_share'] * 100:.0f}% nonzero."
        f"{booked_txt}{promo_txt} This recompute is CPU-only and does not change the audited consensus until a decision is recorded."
    )


# ------------------------------------------------------------------ actions
def _actions(item_id: int, row) -> List[Dict[str, Any]]:
    return [
        {"type": "use_dynamic_forecast", "item_id": int(item_id), "title": "Use as decision",
         "detail": "Record the first-month dynamic quantity in the audit ledger."},
        {"type": "review_phasing", "item_id": int(item_id), "title": "Review supply & phasing",
         "detail": "Check booked units and expected deliveries for this item."},
        {"type": "scenario_prompt", "item_id": int(item_id), "title": "Run as a scenario",
         "detail": "Open Scenarios with this item preloaded as a generation prompt."},
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
