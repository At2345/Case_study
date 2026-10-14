"""Channel allocation under a supply constraint (F-D2C-01).

For one product-month the available units are split across its channel items. The objective is total
profit = sum(unit_margin * allocated) - sum(unit_penalty * unmet). Because the problem is a single linear
constraint with box bounds and minimum-fill floors, filling channels in descending order of
(unit_margin + unit_penalty) after the floors is exactly optimal. Two reference strategies are reported
next to it so the planner can see the trade-off:

- `priority_wholesale`: the rule of thumb in the brief (Amazon 1P, then B2B, then D2C),
- `proportional`: the same fill rate for every channel.

Penalty parameters are business assumptions (not in the data); see AllocationConfig.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

PRIORITY_ORDER = {"amazon": 0, "B2B wo Amazon": 1, "D2C": 2}


@dataclass(frozen=True)
class AllocationConfig:
    chargeback_pct: float = 0.06       # Amazon down-confirmation chargeback, share of COGS per unmet unit (brief: 3-9%)
    otif_penalty_pct: float = 0.05     # assumed B2B OTIF fine, share of sales value per unmet unit (no data: open question Q4)
    d2c_churn_pct: float = 0.0         # optional D2C backorder cost, share of sales value per unmet unit
    amazon_floor_pct: float = 0.60     # minimum Amazon vendor confirmation (brief)
    b2b_floor_pct: float = 0.0         # minimum fill for critical B2B accounts (planner input)


@dataclass(frozen=True)
class ChannelDemand:
    channel: str
    item_id: int
    demand: float
    unit_margin: float
    unit_penalty: float
    floor_pct: float = 0.0


def build_demands(rows: List[Dict], cfg: AllocationConfig = AllocationConfig()) -> List[ChannelDemand]:
    """rows: item_id, channel, demand, sales_price, purchase_price (COGS)."""
    out = []
    for r in rows:
        margin = float(r["sales_price"]) - float(r["purchase_price"])
        ch = r["channel"]
        if ch == "amazon":
            penalty, floor = cfg.chargeback_pct * float(r["purchase_price"]), cfg.amazon_floor_pct
        elif ch == "B2B wo Amazon":
            penalty, floor = cfg.otif_penalty_pct * float(r["sales_price"]), cfg.b2b_floor_pct
        else:
            penalty, floor = cfg.d2c_churn_pct * float(r["sales_price"]), 0.0
        out.append(ChannelDemand(ch, int(r["item_id"]), float(r["demand"]), margin, penalty, floor))
    return out


def _margin_optimal(demands: List[ChannelDemand], supply: float):
    alloc = [0.0] * len(demands)
    floors = [d.floor_pct * d.demand for d in demands]
    scaled = False
    total_floor = sum(floors)
    if total_floor > supply:
        floors = [f * supply / total_floor for f in floors]
        scaled = True
    for i, f in enumerate(floors):
        alloc[i] = f
    left = supply - sum(alloc)
    for i in sorted(range(len(demands)), key=lambda k: -(demands[k].unit_margin + demands[k].unit_penalty)):
        take = min(demands[i].demand - alloc[i], left)
        if take > 0:
            alloc[i] += take
            left -= take
    return alloc, scaled


def _priority(demands: List[ChannelDemand], supply: float):
    alloc = [0.0] * len(demands)
    left = supply
    for i in sorted(range(len(demands)), key=lambda k: PRIORITY_ORDER.get(demands[k].channel, 99)):
        take = min(demands[i].demand, left)
        alloc[i] = take
        left -= take
    return alloc, False


def _proportional(demands: List[ChannelDemand], supply: float):
    total = sum(d.demand for d in demands)
    share = min(1.0, supply / total) if total > 0 else 0.0
    return [d.demand * share for d in demands], False


def _score(name: str, demands: List[ChannelDemand], alloc: List[float], scaled: bool) -> Dict:
    channels = []
    for d, a in zip(demands, alloc):
        unmet = max(0.0, d.demand - a)
        channels.append({
            "channel": d.channel, "item_id": d.item_id, "demand": d.demand, "allocated": a, "unmet": unmet,
            "fill_rate": (a / d.demand) if d.demand > 0 else 1.0,
            "gross_margin_gbp": a * d.unit_margin, "penalty_gbp": unmet * d.unit_penalty,
            "unit_margin": d.unit_margin, "unit_penalty": d.unit_penalty,
        })
    margin = sum(c["gross_margin_gbp"] for c in channels)
    penalty = sum(c["penalty_gbp"] for c in channels)
    return {
        "strategy": name, "channels": channels, "total_margin_gbp": margin, "total_penalty_gbp": penalty,
        "total_profit_gbp": margin - penalty, "shortage_units": sum(c["unmet"] for c in channels), "floors_scaled": scaled,
    }


def simulate(demands: List[ChannelDemand], supply: float) -> Dict:
    if supply < 0:
        raise ValueError("supply must be zero or more")
    if not demands:
        raise ValueError("no channel demand to allocate")
    scenarios = [
        _score(name, demands, *fn(demands, supply))
        for name, fn in (("margin_optimal", _margin_optimal), ("priority_wholesale", _priority), ("proportional", _proportional))
    ]
    best = max(s["total_profit_gbp"] for s in scenarios)
    for s in scenarios:
        s["profit_vs_best_gbp"] = s["total_profit_gbp"] - best
    return {
        "supply": supply, "total_demand": sum(d.demand for d in demands),
        "shortage": max(0.0, sum(d.demand for d in demands) - supply), "scenarios": scenarios,
    }
