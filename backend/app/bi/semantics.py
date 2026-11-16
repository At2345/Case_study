"""Governed metric registry (PR-4.1) with edge-case guards (PR-4.2).

Every metric formula lives here exactly once. The KPI tree, the metrics API and any
future consumer read from this registry, so a formula change cannot drift between
them. Each definition declares its grain and aggregation so the compiler can refuse
the two classic traps: fan-out across grains, and summing a semi-additive
point-in-time metric across time.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.bi.finops import QueryTag, record
from app.bi.tenancy import scoped

_ITEM = "in_scope = 1"


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    owner: str
    unit: str
    table: str
    grain: str
    aggregation: str
    numerator: str = ""
    denominator: str = ""
    expression: str = ""
    where: str = ""
    scale: float = 1.0
    semi_additive: bool = False
    version: str = "1.0.0"
    description: str = ""


METRICS: Dict[str, Metric] = {m.id: m for m in [
    # ---- cycle grain
    Metric("in_scope_skus", "In-scope SKUs", "Head of Supply Chain", "count",
           "cycle_summary", "cycle", "sum", expression="in_scope_skus",
           description="SKUs the cycle planned."),
    Metric("autopilot_share", "Autopilot coverage", "Head of Supply Chain", "share",
           "cycle_summary", "cycle", "ratio",
           numerator="autopilot_approved_skus", denominator="in_scope_skus",
           description="Share of in-scope SKUs approved without a planner touch."),
    Metric("planner_led_share", "Planner-led share", "S&OP Process Owner", "share",
           "cycle_summary", "cycle", "ratio",
           numerator="planner_led_skus", denominator="in_scope_skus",
           description="Share of in-scope SKUs a planner reviewed by hand."),
    Metric("exceptions_per_100", "Exceptions per 100 SKUs", "S&OP Process Owner", "per100",
           "cycle_summary", "cycle", "ratio",
           numerator="exceptions_requiring_review", denominator="in_scope_skus", scale=100.0,
           description="Review load normalised to 100 in-scope SKUs."),
    Metric("planned_value_gbp", "Planned value", "Head of Demand Planning", "gbp",
           "cycle_summary", "cycle", "sum", expression="planned_value_gbp"),
    Metric("net_value_variance_gbp", "Net value vs baseline", "Head of Demand Planning", "gbp",
           "cycle_summary", "cycle", "sum", expression="net_value_variance_gbp"),

    # ---- item grain
    Metric("ml_coverage", "ML-chosen items", "Demand Planning Lead", "share",
           "triage_items", "item", "ratio",
           numerator="CASE WHEN source = 'ml' THEN 1 ELSE 0 END", denominator="1",
           where=_ITEM, description="Share of in-scope items where the ML model supplied the number."),
    Metric("fallback_share", "Median fallbacks", "Demand Planning Lead", "share",
           "triage_items", "item", "ratio",
           numerator="CASE WHEN source LIKE 'fallback:%' THEN 1 ELSE 0 END", denominator="1",
           where=_ITEM, description="Share of in-scope items that fell back to a median."),
    Metric("routed_share", "Routed methods", "Demand Planning Lead", "share",
           "triage_items", "item", "ratio",
           numerator="CASE WHEN source LIKE 'routed:%' THEN 1 ELSE 0 END", denominator="1",
           where=_ITEM, description="Share of in-scope items routed to a specialised method."),
    Metric("oos_exposure_gbp", "OOS exposure", "Inventory Manager", "gbp",
           "triage_items", "item", "sum",
           expression="CASE WHEN risk_type = 'OOS_RISK' THEN var_gbp ELSE 0 END", where=_ITEM,
           description="Value at risk from out-of-stock exposure."),
    Metric("overstock_exposure_gbp", "Overstock exposure", "Inventory Manager", "gbp",
           "triage_items", "item", "sum",
           expression="CASE WHEN risk_type = 'OVERSTOCK_RISK' THEN var_gbp ELSE 0 END", where=_ITEM,
           description="Value at risk from overstock exposure."),
    Metric("total_exposure_gbp", "Total value at risk", "Inventory Manager", "gbp",
           "triage_items", "item", "sum",
           expression="CASE WHEN risk_type IN ('OOS_RISK', 'OVERSTOCK_RISK') THEN var_gbp ELSE 0 END",
           where=_ITEM, description="OOS plus overstock exposure."),
    Metric("avg_weeks_of_cover", "Avg weeks of cover", "Inventory Manager", "weeks",
           "triage_items", "item", "avg", expression="weeks_of_cover", where=_ITEM,
           semi_additive=True,
           description="Point-in-time stock position; never sum across months."),
    Metric("avg_oos_rate_28d", "Avg OOS rate 28d", "Inventory Manager", "share",
           "triage_items", "item", "avg", expression="oos_rate_28d", where=_ITEM,
           semi_additive=True,
           description="Point-in-time service level; never sum across months."),

    # ---- planner grain
    Metric("fva_points", "Forecast value added", "Head of Demand Planning", "pts",
           "fva_by_planner", "planner", "ratio",
           numerator="fva_pts * volume", denominator="volume",
           description="Volume-weighted FVA on matured overrides."),

    # ---- quality grain
    Metric("open_quality_warnings", "Open quality warnings", "Data Steward", "count",
           "quality_warnings", "quality", "count",
           description="Open data-quality warnings for the cycle."),
    Metric("imputed_items", "Imputed items", "Data Steward", "count",
           "quality_warnings", "quality", "sum", expression="items",
           description="Items whose history was imputed rather than observed."),
    Metric("max_imputed_share", "Worst imputed share", "Data Steward", "share",
           "quality_warnings", "quality", "max", expression="imputed_share",
           description="Highest imputed share across the warned channel-months."),
]}


def compile_expr(metric: Metric) -> str:
    """Render the metric's aggregate expression from its single definition."""
    if metric.aggregation == "ratio":
        expr = f"CAST(SUM({metric.numerator}) AS REAL) / NULLIF(SUM({metric.denominator}), 0)"
        if metric.scale != 1.0:
            expr = f"{expr} * {metric.scale}"
    elif metric.aggregation == "sum":
        expr = f"SUM({metric.expression})"
    elif metric.aggregation == "avg":
        expr = f"AVG({metric.expression})"
    elif metric.aggregation == "max":
        expr = f"MAX({metric.expression})"
    elif metric.aggregation == "count":
        expr = "COUNT(*)"
    else:
        raise ValueError(f"Unknown aggregation {metric.aggregation!r} for metric {metric.id}")
    return expr


def compile_sql(metric: Metric) -> str:
    """Render the metric as a standalone statement, for catalog transparency."""
    where = f" WHERE {metric.where}" if metric.where else ""
    return f"SELECT {compile_expr(metric)} AS value FROM {metric.table}{where}"


def evaluate(path: Path, metric_ids: List[str], tenant: str, tag: QueryTag) -> Dict[str, Optional[float]]:
    """Evaluate the requested metrics, one query per table and grain.

    The connection is tenant-scoped, so the SQL below needs no tenant predicate: the
    temp views installed by `app.bi.tenancy` already restrict every table to one
    tenant's rows.

    `tag` is required, not optional: a metric query that cannot be attributed to a team,
    a pipeline and a workload is a query nobody is paying for, and PR-8.3 asks for 100%
    coverage. The tag is recorded here rather than at the call site so a new consumer
    cannot forget to.
    """
    groups: Dict[tuple, List[Metric]] = {}
    for metric_id in metric_ids:
        metric = METRICS[metric_id]
        groups.setdefault((metric.table, metric.where), []).append(metric)

    values: Dict[str, Optional[float]] = {}
    started = time.perf_counter()
    statements = 0
    with scoped(path, tenant) as con:
        for (table, where), metrics in groups.items():
            columns = ", ".join(f"{compile_expr(m)} AS m{i}" for i, m in enumerate(metrics))
            sql = f"SELECT {columns} FROM {table}" + (f" WHERE {where}" if where else "")
            row = con.execute(sql).fetchone()
            statements += 1
            for index, metric in enumerate(metrics):
                values[metric.id] = row[index] if row else None
    record(tag, tenant, kind="metric", metric_ids=metric_ids, statements=statements,
           duration_ms=(time.perf_counter() - started) * 1000)
    return values


def add_across_time(metric_id: str, values: List[float]) -> float:
    """Sum a metric across periods, refusing semi-additive metrics (PR-4.2)."""
    metric = METRICS[metric_id]
    if metric.semi_additive:
        raise ValueError(
            f"{metric_id} is semi-additive at grain {metric.grain!r}: summing across time "
            "double-counts. Take the period-end snapshot instead."
        )
    return float(sum(values))


def combine(metric_ids: List[str]) -> None:
    """Refuse to combine metrics whose grains differ (fan-out trap, PR-4.2)."""
    grains = {METRICS[m].grain for m in metric_ids}
    if len(grains) > 1:
        raise ValueError(
            f"Cannot combine metrics across grains {sorted(grains)}: the join would fan out "
            "and inflate the aggregate."
        )


def catalog(path: Path, tenant: str, tag: QueryTag) -> List[Dict[str, Any]]:
    """Every governed definition, with its compiled SQL and current value."""
    values = evaluate(path, list(METRICS), tenant, tag)
    entries = []
    for metric in METRICS.values():
        entry = asdict(metric)
        entry["sql"] = compile_sql(metric)
        entry["value"] = values[metric.id]
        entries.append(entry)
    return entries
