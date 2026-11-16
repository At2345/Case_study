"""Causal KPI driver tree (PR-1.1).

Root -> branches -> leaves. Every node carries a single named owner, a confidence rating
and a target health corridor, and its value is computed from the BI mart so the tree can
never drift from the dashboard.

Scope follows the plan's cap: 1 root, 3 to 5 branches, 3 to 5 leaves per branch.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.bi.finops import QueryTag
from app.bi.semantics import evaluate


def _node(node_id: str, label: str, metric: str, owner: str, confidence: str, signal: str,
          unit: str, corridor: Dict[str, float], **children: Any) -> Dict[str, Any]:
    node = {"id": node_id, "label": label, "metric": metric, "owner": owner,
            "confidence": confidence, "signal": signal, "unit": unit, "corridor": corridor}
    node.update(children)
    return node


# corridor: {"min": x} is healthy at or above x, {"max": x} is healthy at or below x.
TREE: List[Dict[str, Any]] = [
    _node("autopilot", "Autopilot coverage", "autopilot_share", "Head of Supply Chain",
          "high", "outcome", "share", {"min": 0.70},
          branches=[
              _node("accuracy", "Forecast accuracy", "ml_coverage", "Demand Planning Lead",
                    "high", "output", "share", {"min": 0.50},
                    leaves=[
                        _node("ml_coverage", "ML-chosen items", "ml_coverage", "Demand Planning Lead",
                              "high", "output", "share", {"min": 0.50}),
                        _node("fallback_share", "Median fallbacks", "fallback_share", "Demand Planning Lead",
                              "high", "output", "share", {"max": 0.40}),
                        _node("routed_share", "Routed methods", "routed_share", "Demand Planning Lead",
                              "medium", "output", "share", {"max": 0.20}),
                    ]),
              _node("risk", "Inventory risk", "total_exposure_gbp", "Inventory Manager",
                    "high", "outcome", "gbp", {"max": 1250000},
                    leaves=[
                        _node("oos_exposure", "OOS exposure", "oos_exposure_gbp", "Inventory Manager",
                              "high", "outcome", "gbp", {"max": 500000}),
                        _node("overstock_exposure", "Overstock exposure", "overstock_exposure_gbp",
                              "Inventory Manager", "high", "outcome", "gbp", {"max": 750000}),
                        _node("weeks_of_cover", "Avg weeks of cover", "avg_weeks_of_cover", "Inventory Manager",
                              "medium", "input", "weeks", {"max": 16}),
                        _node("oos_rate", "Avg OOS rate 28d", "avg_oos_rate_28d", "Inventory Manager",
                              "medium", "input", "share", {"max": 0.02}),
                    ]),
              _node("productivity", "Planning productivity", "exceptions_per_100", "S&OP Process Owner",
                    "medium", "output", "per100", {"max": 2.0},
                    leaves=[
                        _node("exceptions_per_100", "Exceptions per 100 SKUs", "exceptions_per_100",
                              "S&OP Process Owner", "high", "output", "per100", {"max": 2.0}),
                        _node("planner_led_share", "Planner-led share", "planner_led_share",
                              "S&OP Process Owner", "high", "output", "share", {"max": 0.20}),
                        _node("fva_points", "Forecast value added", "fva_points", "Head of Demand Planning",
                              "low", "outcome", "pts", {"min": 0}),
                    ]),
              _node("quality", "Data quality", "open_quality_warnings", "Data Steward",
                    "high", "input", "count", {"max": 0},
                    leaves=[
                        _node("quality_warnings", "Open quality warnings", "open_quality_warnings", "Data Steward",
                              "high", "input", "count", {"max": 0}),
                        _node("imputed_items", "Imputed items", "imputed_items", "Data Steward",
                              "high", "input", "count", {"max": 1000}),
                        _node("max_imputed_share", "Worst imputed share", "max_imputed_share", "Data Steward",
                              "high", "input", "share", {"max": 0.95}),
                    ]),
          ]),
]


def _metric_ids() -> List[str]:
    ids = [TREE[0]["metric"]]
    for branch in TREE[0]["branches"]:
        ids.append(branch["metric"])
        ids.extend(leaf["metric"] for leaf in branch["leaves"])
    return ids


def _facts(path: Path, tenant: str, tag: QueryTag) -> Dict[str, Optional[float]]:
    """Every number the tree needs, read from the governed metric registry."""
    return evaluate(path, _metric_ids(), tenant, tag)


def _health(value: Optional[float], corridor: Dict[str, float], watch: float = 0.10) -> str:
    """healthy / watch / breach / unknown against the node's target corridor."""
    if value is None:
        return "unknown"
    low, high = corridor.get("min"), corridor.get("max")
    if low is not None and value < low:
        return "breach"
    if high is not None and value > high:
        return "breach"
    if low is not None and value < low + abs(low) * watch:
        return "watch"
    if high is not None and value > high - abs(high) * watch:
        return "watch"
    return "healthy"


def _render(node: Dict[str, Any], facts: Dict[str, Optional[float]]) -> Dict[str, Any]:
    value = facts.get(node["metric"])
    out = {k: v for k, v in node.items() if k not in ("branches", "leaves")}
    out["value"] = value
    out["health"] = _health(value, node["corridor"])
    if "branches" in node:
        out["branches"] = [_render(branch, facts) for branch in node["branches"]]
    if "leaves" in node:
        out["leaves"] = [_render(leaf, facts) for leaf in node["leaves"]]
    return out


def build_tree(path: Path, tenant: str, tag: QueryTag) -> Dict[str, Any]:
    """Render the KPI driver tree with current values and health states."""
    facts = _facts(path, tenant, tag)
    root = _render(TREE[0], facts)
    leaves = sum(len(branch["leaves"]) for branch in root["branches"])
    return {
        "root": root,
        "counts": {"root": 1, "branches": len(root["branches"]), "leaves": leaves,
                   "total": 1 + len(root["branches"]) + leaves},
        "facts": facts,
    }
