"""Data contracts for the BI mart (PR-7.1).

The mart is the boundary between the pipeline and every dashboard consumer. A
column rename, a type change or a dropped column breaks a chart silently: the SQL
still runs, the payload still parses, and the panel renders an empty axis. These
contracts make that boundary explicit, name the consumers of each table, and are
checked on every build so a breaking change fails the build instead of the
dashboard.

The contract is also the schema authority. `build_mart` creates each table from
the declared types and then appends, rather than letting pandas infer the schema
from the frame. That closes a real defect: an empty frame used to produce an
all-TEXT table, so a column's type flipped the moment the first row arrived.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from app.bi.tenancy import TENANT_COLUMN

STORAGE_TYPES = ("INTEGER", "REAL", "TEXT")


class ContractError(Exception):
    """The mart does not match its declared contract."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    note: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    grain: str
    consumers: Tuple[str, ...]
    columns: Tuple[Column, ...]
    optional: bool = False

    @property
    def names(self) -> List[str]:
        return [c.name for c in self.columns]

    def ddl(self) -> str:
        body = ",\n  ".join(f'"{c.name}" {c.type}' for c in self.columns)
        return f'CREATE TABLE "{self.name}" (\n  {body}\n)'


def _cols(spec: str) -> Tuple[Column, ...]:
    """`item_id:INTEGER! product_id:TEXT` -> columns; a trailing `!` marks non-nullable.

    `tenant_id` is prepended to every table rather than written into each spec, so a
    table cannot be declared without one. Tenant isolation (PR-8.1) is enforced by
    filtering on this column, so a table that lacks it could not be served safely.
    """
    out: List[Column] = [Column(TENANT_COLUMN, "TEXT", nullable=False,
                                note="Owning tenant; every read is filtered on it.")]
    for token in spec.split():
        name, _, kind = token.partition(":")
        if kind.rstrip("!") not in STORAGE_TYPES:
            raise ContractError(f"{name}: unknown storage type {kind!r}")
        out.append(Column(name, kind.rstrip("!"), nullable=not kind.endswith("!")))
    return tuple(out)


CONTRACTS: Dict[str, Table] = {
    t.name: t for t in (
        Table(
            name="triage_items",
            grain="one row per item",
            consumers=("KPI cards", "channel bars", "risk bars", "top-items table", "overrides bars"),
            columns=_cols(
                "item_id:INTEGER! product_id:TEXT channel:TEXT abc_category:TEXT product_status:TEXT "
                "phase:TEXT state:TEXT tier:TEXT in_scope:INTEGER requires_review:INTEGER "
                "sales_price:REAL purchase_price:REAL baseline_h:REAL final_h:REAL final_m1:REAL "
                "baseline_m1:REAL ml_m1:REAL delta_h:REAL value_delta_gbp:REAL var_gbp:REAL "
                "pct_delta:REAL risk_type:TEXT risk_flags:TEXT source:TEXT hist_12m:REAL "
                "final_12m:REAL months_since_launch:REAL months_to_eol:REAL booked_m1:REAL "
                "book_gap_gbp:REAL on_hand:REAL open_po:REAL oos_rate_28d:REAL weeks_of_cover:REAL "
                "glance_mom_pct:REAL p70_gap_pct:REAL"),
        ),
        Table(
            name="triage_monthly",
            grain="one row per item and month",
            consumers=("plan line", "KPI cards"),
            columns=_cols(
                "item_id:INTEGER! date:TEXT! final_qty:REAL ml_prediction:REAL source:TEXT "
                "baseline_qty:REAL product_id:TEXT channel:TEXT abc_category:TEXT"),
        ),
        Table(
            name="quantile_bands",
            grain="one row per item and month",
            consumers=("plan line band",),
            columns=_cols(
                "item_id:INTEGER! date:TEXT! p10:REAL p50:REAL p90:REAL sigma:REAL "
                "product_id:TEXT channel:TEXT abc_category:TEXT"),
        ),
        Table(
            name="phasing_items",
            grain="one row per item and month",
            consumers=("phasing panel",),
            columns=_cols(
                "item_id:INTEGER! month:TEXT! booked_qty:REAL booked_shifted_qty:REAL "
                "carry_in_qty:REAL shifted_in_qty:REAL product_id:TEXT channel:TEXT abc_category:TEXT"),
        ),
        Table(
            name="safety_stock",
            grain="one row per item",
            consumers=("safety panel",),
            columns=_cols(
                "item_id:INTEGER! product_id:TEXT channel:TEXT abc_category:TEXT final_m1:REAL "
                "p10:REAL p90:REAL sigma:REAL sigma_hist:REAL z:REAL lt_m:REAL ss_dynamic:REAL "
                "ss_static:REAL holding_dynamic_gbp:REAL holding_static_gbp:REAL"),
        ),
        Table(
            name="overrides",
            grain="one row per ledger override",
            consumers=("FVA table", "overrides bars"),
            columns=_cols(
                "id:INTEGER! timestamp:TEXT! item_id:INTEGER! cutoff:TEXT! target_month:TEXT! "
                "baseline_quantity:REAL ml_recommended_quantity:REAL submitted_quantity:REAL! "
                "variance_to_ml:REAL financial_impact_gbp:REAL reason_code:TEXT! "
                "planner_comment:TEXT planner_id:TEXT! ip_address:TEXT session_id:TEXT tier:TEXT "
                "risk_type:TEXT prev_hash:TEXT! row_hash:TEXT! product_id:TEXT channel:TEXT "
                "abc_category:TEXT"),
        ),
        Table(
            name="commits",
            grain="one row per committed cycle",
            consumers=("cycle status",),
            columns=_cols(
                "id:INTEGER! timestamp:TEXT! cutoff:TEXT! planner_id:TEXT! items:INTEGER "
                "path:TEXT! prev_hash:TEXT! row_hash:TEXT!"),
        ),
        Table(
            name="cycle_summary",
            grain="one row per mart build",
            consumers=("KPI cards", "header badges"),
            columns=_cols(
                "cutoff:TEXT! first_month:TEXT generated_at:TEXT total_catalog_skus:INTEGER "
                "in_scope_skus:INTEGER autopilot_approved_skus:INTEGER "
                "exceptions_requiring_review:INTEGER planner_led_skus:INTEGER "
                "planned_value_gbp:REAL baseline_value_gbp:REAL net_value_variance_gbp:REAL "
                "quality_warnings:INTEGER"),
        ),
        Table(
            name="quality_warnings",
            grain="one row per imputed channel and month",
            consumers=("data quality",),
            columns=_cols("channel:TEXT month:TEXT imputed_share:REAL items:INTEGER"),
        ),
        Table(
            name="fva_by_planner",
            grain="one row per planner",
            consumers=("FVA table",),
            optional=True,
            columns=_cols(
                "planner_id:TEXT! n_overrides:INTEGER n_accepted:INTEGER volume:REAL fpe_ml:REAL "
                "fpe_planner:REAL fpe_baseline:REAL fva_pts:REAL"),
        ),
        Table(
            name="fva_by_reason",
            grain="one row per reason code",
            consumers=("FVA table",),
            optional=True,
            columns=_cols(
                "reason_code:TEXT! n_overrides:INTEGER n_accepted:INTEGER volume:REAL fpe_ml:REAL "
                "fpe_planner:REAL fpe_baseline:REAL fva_pts:REAL"),
        ),
        Table(
            name="mart_meta",
            grain="one row per mart build",
            consumers=("mart card",),
            columns=_cols("generated_at:TEXT! reports_dir:TEXT store_dir:TEXT tables:TEXT!"),
        ),
    )
}


def document() -> Dict[str, Any]:
    """The declared contract, for the API and for review."""
    return {
        "tables": [
            {
                "table": t.name,
                "grain": t.grain,
                "consumers": list(t.consumers),
                "optional": t.optional,
                "columns": [
                    {"name": c.name, "type": c.type, "nullable": c.nullable}
                    for c in t.columns
                ],
            }
            for t in CONTRACTS.values()
        ],
        "summary": {"tables": len(CONTRACTS),
                    "columns": sum(len(t.columns) for t in CONTRACTS.values())},
    }


def check_frame(table: Table, columns: List[str]) -> None:
    """Fail before writing when a frame cannot satisfy the contract."""
    declared = table.names
    missing = [c for c in declared if c not in columns]
    if missing:
        raise ContractError(
            f"{table.name} is missing declared column(s) {missing}; "
            f"consumers that would break: {', '.join(table.consumers)}")
    extra = [c for c in columns if c not in declared]
    if extra:
        raise ContractError(
            f"{table.name} has undeclared column(s) {extra}; add them to the contract first")


def conformance(con: sqlite3.Connection) -> Dict[str, Any]:
    """Compare a live mart against the contract."""
    present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    problems: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []

    for table in CONTRACTS.values():
        if table.name not in present:
            if not table.optional:
                problems.append({
                    "table": table.name, "kind": "missing_table", "severity": "error",
                    "detail": f"{table.name} is declared but absent; "
                              f"consumers that would break: {', '.join(table.consumers)}"})
            tables.append({"table": table.name, "present": False, "rows": 0, "ok": table.optional})
            continue

        live = {r[1]: (r[2] or "").upper() for r in con.execute(f'PRAGMA main.table_info("{table.name}")')}
        rows = int(con.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0])
        declared = {c.name for c in table.columns}

        for col in table.columns:
            if col.name not in live:
                problems.append({
                    "table": table.name, "kind": "missing_column", "severity": "error",
                    "column": col.name,
                    "detail": f"{table.name}.{col.name} is declared but absent; "
                              f"consumers that would break: {', '.join(table.consumers)}"})
            elif live[col.name] != col.type:
                problems.append({
                    "table": table.name, "kind": "type_mismatch", "severity": "error",
                    "column": col.name,
                    "detail": f"{table.name}.{col.name} is {live[col.name] or 'untyped'}, "
                              f"contract says {col.type}"})
            elif not col.nullable and rows:
                nulls = int(con.execute(
                    f'SELECT COUNT(*) FROM "{table.name}" WHERE "{col.name}" IS NULL').fetchone()[0])
                if nulls:
                    problems.append({
                        "table": table.name, "kind": "null_violation", "severity": "warning",
                        "column": col.name,
                        "detail": f"{table.name}.{col.name} is declared non-nullable "
                                  f"but has {nulls} null row(s)"})

        for name in live:
            if name not in declared:
                problems.append({
                    "table": table.name, "kind": "undeclared_column", "severity": "warning",
                    "column": name,
                    "detail": f"{table.name}.{name} is not in the contract; "
                              f"no consumer may rely on it"})

        tables.append({
            "table": table.name, "present": True, "rows": rows,
            "ok": not any(p["table"] == table.name and p["severity"] == "error" for p in problems)})

    errors = [p for p in problems if p["severity"] == "error"]
    return {
        "ok": not errors,
        "tables": tables,
        "problems": problems,
        "summary": {"tables": len(CONTRACTS),
                    "present": sum(1 for t in tables if t["present"]),
                    "errors": len(errors),
                    "warnings": len(problems) - len(errors)},
    }


def assert_conforms(con: sqlite3.Connection) -> Dict[str, Any]:
    """Raise ContractError when the mart has drifted from the contract."""
    report = conformance(con)
    if not report["ok"]:
        first = next(p for p in report["problems"] if p["severity"] == "error")
        raise ContractError(f"mart violates its contract: {first['detail']}")
    return report
