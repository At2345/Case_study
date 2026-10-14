from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


CHANNEL_ALIASES = {
    "amazon": "amazon",
    "b2b": "B2B wo Amazon",
    "d2c": "D2C",
}


def _pct(prompt: str) -> float:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", prompt)
    if match:
        return max(-100.0, min(500.0, float(match.group(1))))
    q = prompt.lower()
    mag = 20.0 if any(w in q for w in ("severe", "aggressive", "strong")) else 5.0 if any(w in q for w in ("mild", "small")) else 10.0
    if any(w in q for w in ("promotion", "campaign", "upside", "growth", "increase", "lift", "boost")):
        return 15.0 if "promotion" in q or "campaign" in q else mag
    if any(w in q for w in ("downside", "conservative", "risk", "reduce", "reduction", "decline", "soft", "weak")):
        return -mag
    return 10.0


def _channel(prompt: str) -> Optional[str]:
    q = prompt.lower()
    for token, channel in CHANNEL_ALIASES.items():
        if token in q:
            return channel
    return None


def _product(prompt: str, svc) -> Optional[str]:
    match = re.search(r"\bPROD[_-]?(\d+)\b", prompt, re.IGNORECASE)
    if match:
        return f"PROD_{match.group(1)}"
    item = _item(prompt, svc)
    if item is not None:
        return None
    q = prompt.lower()
    if "high-margin" in q or "high margin" in q:
        frame = svc.triage[svc.triage["in_scope"]].copy()
        frame["margin"] = frame["sales_price"].fillna(0) - frame["purchase_price"].fillna(0)
        ranked = frame.groupby("product_id")["margin"].mean().sort_values(ascending=False)
        if not ranked.empty:
            return str(ranked.index[0])
    if "largest" in q or "top product" in q:
        frame = svc.triage[svc.triage["in_scope"]].copy()
        frame["value"] = frame["final_h"].fillna(0) * frame["sales_price"].fillna(0)
        ranked = frame.groupby("product_id")["value"].sum().sort_values(ascending=False)
        if not ranked.empty:
            return str(ranked.index[0])
    return None


def _item(prompt: str, svc) -> Optional[int]:
    match = re.search(r"\bitem\s+(\d+)\b", prompt, re.IGNORECASE)
    if match and svc.has_item(int(match.group(1))):
        return int(match.group(1))
    return None


def _month(prompt: str, svc) -> Optional[str]:
    q = prompt.lower()
    if any(w in q for w in ("first month", "next month", "month 1", "m1")):
        return svc.first_month
    match = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])(?:[-/]01)?\b", prompt)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-01"
    return None


def generate(prompt: str, svc) -> Dict[str, Any]:
    text = prompt.strip()
    if not text:
        raise ValueError("Enter a scenario prompt.")
    pct = _pct(text)
    product = _product(text, svc)
    channel = None if product else _channel(text)
    month = _month(text, svc)
    item = None if product or channel else _item(text, svc)
    if item is not None:
        base_qty = _item_qty(svc, item, month)
        qty = max(0.0, base_qty * (1 + pct / 100.0))
        edit = {"type": "item", "item_id": item, "month": month or svc.first_month, "qty": round(qty)}
        name = _name(text, pct, "item", str(item))
        description = _description(text, edit)
        return {"name": name, "description": description, "edits": [edit], "explanation": _explanation(edit)}
    if product:
        scope, value = "product", product
    elif channel:
        scope, value = "channel", channel
    else:
        scope, value = "all", None
    edit = {"type": "pct", "scope": scope, "value": value, "pct": pct, "month": month}
    name = _name(text, pct, scope, value)
    description = _description(text, edit)
    return {"name": name, "description": description, "edits": [edit], "explanation": _explanation(edit)}


def _name(prompt: str, pct: float, scope: str, value: Optional[str]) -> str:
    words = " ".join(prompt.split())[:48]
    prefix = "Upside" if pct >= 0 else "Downside"
    target = "all demand" if scope == "all" else str(value)
    return f"{prefix}: {target}" if not words else words[:80]


def _item_qty(svc, item_id: int, month: Optional[str]) -> float:
    detail = svc.item_detail(item_id, {})
    target = month or svc.first_month
    for p in detail.get("plan", []):
        if p.get("month") == target and p.get("final") is not None:
            return float(p["final"])
    return 0.0


def _description(prompt: str, edit: Dict[str, Any]) -> str:
    if edit["type"] == "item":
        return f"Generated from prompt: {prompt.strip()}. Sets item {edit['item_id']} to {edit['qty']:g} units in {edit['month']}."
    month = f" in {edit['month']}" if edit.get("month") else " across all planning months"
    target = "all items" if edit["scope"] == "all" else edit["value"]
    return f"Generated from prompt: {prompt.strip()}. Applies {edit['pct']:+g}% to {target}{month}."


def _explanation(edit: Dict[str, Any]) -> List[str]:
    if edit["type"] == "item":
        return [
            f"The scenario sets item {edit['item_id']} to {edit['qty']:g} units in {edit['month']}.",
            "It uses existing scenario validation and can be edited before or after saving.",
            "Compare it against the current consensus before using it in planning decisions.",
        ]
    target = "all items" if edit["scope"] == "all" else edit["value"]
    month = edit.get("month") or "all months"
    direction = "increases" if edit["pct"] >= 0 else "reduces"
    return [
        f"The scenario {direction} demand for {target} by {abs(edit['pct']):g}% in {month}.",
        "It uses existing scenario validation and can be edited before or after saving.",
        "Compare it against the current consensus before using it in planning decisions.",
    ]


def suggest(svc, decisions: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Data-driven scenario proposals mined from the live cycle: top value-at-risk items,
    OTIF exposure and safety-stock opportunities. Each proposal is a ready-to-generate prompt."""
    queue = svc.top_queue(decisions or {}, 20)
    proposals: List[Dict[str, Any]] = []
    if queue:
        overstock = [r for r in queue if r.get("risk_type") == "OVERSTOCK_RISK"][:1]
        oos = [r for r in queue if r.get("risk_type") == "OOS_RISK"][:1]
        if overstock:
            r = overstock[0]
            proposals.append({
                "id": "top_overstock",
                "title": "Trim the top overstock risk",
                "prompt": f"Reduce demand for item {r['item_id']} by 15% next month to trim overstock exposure",
                "rationale": f"Item {r['item_id']} carries the largest overstock value delta in the queue.",
            })
        if oos:
            r = oos[0]
            proposals.append({
                "id": "top_oos",
                "title": "Protect the top stockout risk",
                "prompt": f"Increase demand for item {r['item_id']} by 15% next month to cover stockout risk",
                "rationale": f"Item {r['item_id']} carries the largest stockout value delta in the queue.",
            })
    return proposals
