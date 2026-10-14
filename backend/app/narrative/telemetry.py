"""Whitelisted, rounded facts about one item: the only input the language model ever receives."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _units(v: Any) -> Optional[int]:
    f = _num(v)
    return None if f is None else int(round(f))


def _one(v: Any, scale: float = 1.0) -> Optional[float]:
    f = _num(v)
    return None if f is None else round(f * scale, 1)


def _drop_none(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


SIGNALS = {
    "glance_mom_pct": ("glance_views_change_14d_pct", lambda v: _one(v, 100.0)),
    "p70_gap_pct": ("p70_vs_sell_out_pct", lambda v: _one(v, 100.0)),
    "oos_rate_28d": ("out_of_stock_rate_28d_pct", lambda v: _one(v, 100.0)),
    "on_hand": ("on_hand_units", _units),
    "open_po": ("open_po_units", _units),
    "weeks_of_cover": ("weeks_of_cover", _one),
}


def build_telemetry(detail: Dict[str, Any], phasing: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the telemetry JSON from an item-detail payload and its phasing rows.

    Free text (the deterministic narrative) and identifiers are deliberately left out.
    """
    item = detail["item"]
    first = detail["plan"][0] if detail.get("plan") else None
    out: Dict[str, Any] = {
        "item": _drop_none({
            "channel": item.get("channel"), "abc_category": item.get("abc_category"), "phase": item.get("phase"),
            "months_since_launch": _one(item.get("months_since_launch")), "months_to_eol": _one(item.get("months_to_eol")),
            "sales_price": _num(item.get("sales_price")), "risk_type": item.get("risk_type"),
            "risk_flags": list(item.get("risk_flags") or []),
        }),
    }
    if first is not None:
        base, plan = _num(first.get("baseline")), _num(first.get("consensus"))
        change = None if base is None or plan is None else round((plan - base) / max(base, 1.0) * 100.0, 1)
        out["plan"] = _drop_none({
            "month": first.get("month"), "baseline_units": _units(base), "ml_units": _units(first.get("ml")),
            "plan_units": _units(plan), "change_vs_baseline_pct": change, "value_delta_gbp": _units(item.get("value_delta_gbp")),
        })
    tele = detail.get("telemetry") or {}
    out["signals"] = _drop_none({name: fn(tele.get(src)) for src, (name, fn) in SIGNALS.items() if src in tele})
    if phasing:
        p = phasing[0]
        out["order_book"] = {
            "booked_units": _units(p["booked"]), "moving_to_later_months_units": _units(p["booked_shifted"]),
            "carry_in_units": _units(p["carry_in"]), "expected_delivery_units": _units(p["expected_delivery"]),
        }
    return out
