"""Simulated data feeds for the demo MCP servers.

Derived from the live cycle tables (orderbook, items, amazon_inventory) so the demo
servers answer with realistic data. Loads are lazy and cached per process.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from app.data.loaders import load

_OPEN_STATUSES = ("", "partially_delivered")


def _num(v: Any, default: float = 0.0) -> float:
    """NaN-safe numeric conversion (NaN is truthy, so `v or 0` is not enough)."""
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _latest_inventory() -> pd.DataFrame:
    inv = load("amazon_inventory")
    if inv.empty:
        return inv
    latest = inv[inv["date"] >= inv["date"].max() - pd.Timedelta(days=7)] \
        .sort_values(["item_id", "date"]).groupby("item_id").tail(1)
    return latest


def open_pos(item_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Open order-book lines (not fully delivered, not fully cancelled)."""
    ob = load("orderbook")
    open_ = ob[(ob["cancellation_status"].isna() | (ob["cancellation_status"] != "cancelled_completely")) &
               (ob["delivered_quantity"].fillna(0) < ob["ordered_quantity"].fillna(0))]
    if item_id is not None:
        open_ = open_[open_["item_id"] == item_id]
    rows = []
    for _, r in open_.sort_values("decision_at").head(limit).iterrows():
        rows.append({
            "order_id": str(r["order_id"]), "item_id": int(r["item_id"]), "channel": r["channel"],
            "ordered_quantity": int(_num(r["ordered_quantity"])),
            "delivered_quantity": int(_num(r["delivered_quantity"])),
            "remaining": int(_num(r["ordered_quantity"]) - _num(r["delivered_quantity"])),
            "decision_at": str(r["decision_at"])[:10],
        })
    return rows


def stock_levels(item_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Latest Amazon inventory snapshot per item."""
    inv = _latest_inventory()
    if item_id is not None:
        inv = inv[inv["item_id"] == item_id]
    rows = []
    for _, r in inv.head(limit).iterrows():
        rows.append({
            "item_id": int(r["item_id"]),
            "sellable_on_hand_units": int(_num(r["sellable_on_hand_inventory_units"])),
            "open_purchase_order_units": int(_num(r["open_purchase_order_units"])),
            "out_of_stock_rate": _num(r["procurable_product_out_of_stock_rate"]),
            "vendor_confirmation_rate": _num(r["vendor_confirmation_rate"]),
        })
    return rows


def material_master(item_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Item master data (product, brand, ABC, prices, lifecycle)."""
    items = load("items")
    if item_id is not None:
        items = items[items["item_id"] == item_id]
    rows = []
    for _, r in items.head(limit).iterrows():
        rows.append({
            "item_id": int(r["item_id"]), "product_id": str(r["product_id"]), "channel": r["channel"],
            "brand": r["brand"], "category": r["category"], "abc_category": r["abc_category"],
            "launch_month": str(r["launch_month"])[:7] if pd.notna(r["launch_month"]) else None,
            "end_of_life_month": str(r["end_of_life_month"])[:7] if pd.notna(r["end_of_life_month"]) else None,
            "purchase_price_gbp": _num(r["purchase_price"]),
            "sales_price_gbp": _num(r["sales_price"]),
        })
    return rows


def supplier_lead_times(limit: int = 50) -> List[Dict[str, Any]]:
    """Lead times from the safety-stock artifact when present, else a deterministic fallback."""
    try:
        ss = load("safety_stock")
        cols = [c for c in ("item_id", "lead_time_days", "lead_times") if c in ss.columns]
        if cols:
            rows = []
            for _, r in ss.head(limit).iterrows():
                lt = r.get("lead_time_days")
                if lt is None and "lead_times" in ss.columns:
                    lt = r["lead_times"]
                rows.append({"item_id": int(r["item_id"]), "lead_time_days": int(lt or 0)})
            return rows
    except Exception:
        pass
    items = load("items")
    return [{"item_id": int(r["item_id"]), "lead_time_days": 30 + (int(r["item_id"]) % 45)}
            for _, r in items.head(limit).iterrows()]


def accounts(limit: int = 50) -> List[Dict[str, Any]]:
    """CRM accounts derived from the item master (brand/product family level)."""
    items = load("items")
    rows = []
    for (brand, family), g in list(items.groupby(["brand", "product_family"]))[:limit]:
        rows.append({
            "account_id": f"{brand}-{family}".replace(" ", "_")[:40],
            "name": f"{brand} {family}".strip(),
            "items": int(g["item_id"].nunique()),
            "channels": sorted(g["channel"].dropna().unique().tolist()),
        })
    return rows


def opportunities(account_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Open order value per customer/account from the order book."""
    ob = load("orderbook")
    open_ = ob[(ob["cancellation_status"].isna() | (ob["cancellation_status"] != "cancelled_completely")) &
               (ob["delivered_quantity"].fillna(0) < ob["ordered_quantity"].fillna(0))]
    items = load("items")[["item_id", "sales_price"]].drop_duplicates("item_id")
    open_ = open_.merge(items, on="item_id", how="left")
    open_["value_gbp"] = (open_["ordered_quantity"].fillna(0) - open_["delivered_quantity"].fillna(0)) * open_["sales_price"].fillna(0)
    open_ = open_.fillna({"value_gbp": 0.0})
    g = open_.groupby("customer_id").agg(value_gbp=("value_gbp", "sum"), lines=("order_id", "count")).reset_index()
    rows = []
    for _, r in g.sort_values("value_gbp", ascending=False).head(limit).iterrows():
        rows.append({"account_id": str(r["customer_id"]), "open_value_gbp": round(float(r["value_gbp"]), 2),
                     "open_lines": int(r["lines"])})
    return rows


def cases(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Support cases from late/cancelled order-book lines."""
    ob = load("orderbook")
    late = ob[(ob["cancellation_status"].notna()) | (ob["delivered_at"].notna() & ob["planned_delivery_at"].notna() &
                                                    (ob["delivered_at"] > ob["planned_delivery_at"]))]
    rows = []
    for _, r in late.head(limit).iterrows():
        rows.append({
            "case_id": f"CASE-{r['order_id']}"[:20],
            "item_id": int(r["item_id"]), "channel": r["channel"],
            "status": "open" if pd.isna(r["cancellation_status"]) or not r["cancellation_status"] else "cancelled",
            "summary": f"Delivery exception for item {int(r['item_id'])} ({r['channel']})",
        })
    return rows


def issues(query: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Jira issues derived from order-book exceptions (late/cancelled lines)."""
    ob = load("orderbook")
    flagged = ob[(ob["cancellation_status"].notna()) |
                 (ob["delivered_at"].notna() & ob["planned_delivery_at"].notna() & (ob["delivered_at"] > ob["planned_delivery_at"]))]
    rows = []
    for i, (_, r) in enumerate(flagged.head(limit).iterrows(), start=1):
        issue = {
            "key": f"OPS-{1000 + i}",
            "summary": f"Delivery exception item {int(r['item_id'])} ({r['channel']})",
            "status": "OPEN",
            "priority": "HIGH" if pd.notna(r["cancellation_status"]) else "MEDIUM",
            "item_id": int(r["item_id"]),
        }
        if query and query.lower() not in issue["summary"].lower():
            continue
        if status and status.upper() != issue["status"]:
            continue
        rows.append(issue)
    return rows


def text(payload: Any) -> str:
    return json.dumps(payload, default=str, indent=1)
