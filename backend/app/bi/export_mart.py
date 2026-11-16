"""Build the Apache Superset data mart (SQLite) from the cycle artifacts and the audit ledger.

Usage (repo root, after the cycle artifacts exist in `reports/`):
    $env:PYTHONPATH="backend"; python -m app.bi.export_mart

Writes `var/bi_mart.db` with one table per dataset plus `mart_meta`. Superset connects to it with
`sqlite:////app/bi_mart.db` (see docker-compose.superset.yml) and reads it read-only.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from app.bi.contracts import CONTRACTS, ContractError, assert_conforms, check_frame
from app.bi.serving import ensure_indexes
from app.bi.tenancy import DEFAULT_TENANT, TENANT_COLUMN, normalise
from app.config import settings
from app.store.ledger import Ledger

MART_FILE = "bi_mart.db"

TRIAGE_COLS = CONTRACTS["triage_items"].names


def _read_parquet(reports_dir: Path, name: str) -> Optional[pd.DataFrame]:
    path = reports_dir / name
    return pd.read_parquet(path) if path.exists() else None


def _iso_month(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = df.copy()
    out[col] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%d")
    return out


def _commits(store_dir: Path) -> pd.DataFrame:
    path = store_dir / "ledger.db"
    if not path.exists():
        return pd.DataFrame(columns=CONTRACTS["commits"].names)
    with sqlite3.connect(path) as con:
        return pd.read_sql_query("SELECT * FROM commits ORDER BY id", con)


def _coerce(table, df: pd.DataFrame) -> pd.DataFrame:
    """Force the frame onto the declared types so the written schema cannot drift."""
    out = df.reindex(columns=table.names).copy()
    for col in table.columns:
        series = out[col.name]
        if col.type in ("INTEGER", "REAL"):
            numeric = pd.to_numeric(series, errors="coerce")
            lost = int(series.notna().sum() - numeric.notna().sum())
            if lost:
                raise ContractError(
                    f"{table.name}.{col.name} is declared {col.type} "
                    f"but {lost} value(s) are not numeric")
            out[col.name] = numeric.astype("Int64" if col.type == "INTEGER" else "float64")
        else:
            out[col.name] = series.astype(object).where(series.notna(), None)
    return out


def _ensure_table(con: sqlite3.Connection, table) -> None:
    """Create the table from the contract, or rebuild it when the schema has moved."""
    live = {r[1]: (r[2] or "").upper()
            for r in con.execute(f'PRAGMA main.table_info("{table.name}")')}
    if not live:
        con.execute(table.ddl())
        return
    if live != {c.name: c.type for c in table.columns}:
        # A column change cannot be applied in place. Rebuilding discards every
        # tenant's rows for this table, so it is a migration rather than a routine
        # build; the contract check afterwards proves the new schema is correct.
        con.execute(f'DROP TABLE "{table.name}"')
        con.execute(table.ddl())


def _write_table(con: sqlite3.Connection, table, df: pd.DataFrame, tenant: str) -> int:
    """Replace one tenant's rows, leaving every other tenant's rows untouched."""
    scoped = df.copy()
    scoped[TENANT_COLUMN] = tenant
    check_frame(table, list(scoped.columns))
    _ensure_table(con, table)
    con.execute(f'DELETE FROM "{table.name}" WHERE {TENANT_COLUMN} = ?', (tenant,))
    _coerce(table, scoped).to_sql(table.name, con, if_exists="append", index=False)
    return int(len(scoped))


def _fva(overrides: pd.DataFrame, reports_dir: Path) -> Dict[str, pd.DataFrame]:
    """FVA scorecard tables; skipped when there are no overrides or the actuals cannot be loaded."""
    if overrides.empty:
        return {}
    try:
        from app.api.service import DataService
        from app.evaluation.fva import override_fva

        svc = DataService.from_reports(reports_dir)
        return {
            "fva_by_planner": override_fva(overrides, svc.actuals, group_by=("planner_id",)),
            "fva_by_reason": override_fva(overrides, svc.actuals, group_by=("reason_code",)),
        }
    except Exception as exc:  # pragma: no cover - defensive: the mart must build without FVA
        print(f"!! FVA tables skipped: {type(exc).__name__}: {exc}")
        return {}


def build_mart(reports_dir=None, store_dir=None, out_path=None,
               tenant_id: str = DEFAULT_TENANT) -> Dict[str, int]:
    """Write one tenant's rows into the mart and return {table: row_count}."""
    tenant = normalise(tenant_id)
    reports_dir = Path(reports_dir or settings.reports_dir)
    store_dir = Path(store_dir or settings.store_dir)
    out_path = Path(out_path or store_dir / MART_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    triage_path = reports_dir / "triage_live.parquet"
    if not triage_path.exists():
        raise FileNotFoundError(f"{triage_path} (run python -m app.triage.run first)")
    triage = pd.read_parquet(triage_path)
    meta_path = reports_dir / "triage_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    dims = triage[["item_id", "product_id", "channel", "abc_category"]].drop_duplicates("item_id")
    tables: Dict[str, pd.DataFrame] = {}

    items = triage[[c for c in TRIAGE_COLS if c in triage.columns]].copy()
    items["risk_flags"] = items["risk_flags"].fillna("").astype(str)
    tables["triage_items"] = items

    monthly = _read_parquet(reports_dir, "triage_monthly.parquet")
    if monthly is not None:
        tables["triage_monthly"] = _iso_month(monthly, "date").merge(dims, on="item_id", how="left")

    bands = _read_parquet(reports_dir, "quantile_bands.parquet")
    if bands is not None:
        tables["quantile_bands"] = _iso_month(bands, "date").merge(dims, on="item_id", how="left")

    phasing = _read_parquet(reports_dir, "phasing_items.parquet")
    if phasing is not None:
        tables["phasing_items"] = _iso_month(phasing, "month").merge(dims, on="item_id", how="left")

    safety = _read_parquet(reports_dir, "safety_stock.parquet")
    if safety is not None:
        tables["safety_stock"] = safety

    ledger = Ledger(store_dir / "ledger.db")
    overrides = pd.DataFrame(ledger.overrides())
    if overrides.empty:
        overrides = pd.DataFrame(columns=CONTRACTS["overrides"].names)
    else:
        overrides = overrides.merge(dims, on="item_id", how="left")
    tables["overrides"] = overrides
    tables["commits"] = _commits(store_dir)
    tables.update(_fva(overrides, reports_dir))

    scope = triage[triage["in_scope"]]
    price = scope["sales_price"].fillna(0)
    tables["cycle_summary"] = pd.DataFrame([{
        "cutoff": meta.get("cutoff"), "first_month": meta.get("first_month"),
        "generated_at": meta.get("generated_at"),
        "total_catalog_skus": int(len(triage)), "in_scope_skus": int(len(scope)),
        "autopilot_approved_skus": int((scope["state"] == "AUTO_APPROVED").sum()),
        "exceptions_requiring_review": int((scope["state"] == "EXCEPTION_REVIEW").sum()),
        "planner_led_skus": int((scope["state"] == "PLANNER_LED").sum()),
        "planned_value_gbp": float((scope["final_h"] * price).sum()),
        "baseline_value_gbp": float((scope["baseline_h"] * price).sum()),
        "net_value_variance_gbp": float(((scope["final_h"] - scope["baseline_h"]) * price).sum()),
        "quality_warnings": len(meta.get("quality_warnings") or []),
    }])

    warnings = pd.DataFrame(meta.get("quality_warnings") or [],
                            columns=CONTRACTS["quality_warnings"].names)
    tables["quality_warnings"] = warnings

    counts: Dict[str, int] = {name: int(len(df)) for name, df in tables.items()}
    tables["mart_meta"] = pd.DataFrame([{
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports_dir": str(reports_dir), "store_dir": str(store_dir),
        "tables": json.dumps(counts, sort_keys=True),
    }])

    with sqlite3.connect(out_path) as con:
        for name, df in tables.items():
            if name not in CONTRACTS:
                raise ContractError(f"table {name!r} is not declared in the contract")
            _write_table(con, CONTRACTS[name], df, tenant)
        assert_conforms(con)
        ensure_indexes(con)
    return counts


def main() -> None:
    counts = build_mart()
    print(f"== Superset mart written to {settings.store_dir / MART_FILE} ==")
    for name, rows in counts.items():
        print(f"  {name:<20} {rows:>8,} rows")


if __name__ == "__main__":
    main()
