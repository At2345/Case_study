"""Zero-copy columnar transport (PR-3.3).

There is no Flight SQL server and no ADBC driver in this repo, so the wire-protocol half of this
requirement has no analogue: there is no row-oriented ODBC/JDBC driver here to replace. What can
be measured is the serialization overhead itself, which is the cost the requirement exists to
remove. The same tenant-scoped table is encoded three ways — JSON rows, which is what the API
serves today; an Arrow IPC stream; and Parquet — and the bytes and encode time are reported side
by side, so the claim is a measurement rather than an assertion.

The export is tenant-scoped by construction. It reads through `tenancy.scoped`, so a table with
no `tenant_id` column is refused rather than exported unscoped, and the bytes a caller receives
can only ever contain their own tenant's rows. The table name is checked against the declared
contract before it reaches any SQL, so the export cannot be pointed at an arbitrary table.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.bi.contracts import CONTRACTS
from app.bi.tenancy import scoped

# The build metadata is not analytical data, so it is not exportable.
_EXCLUDED = {"mart_meta"}

FORMATS: Dict[str, Tuple[str, str]] = {
    "json": ("application/json", "json"),
    "arrow": ("application/vnd.apache.arrow.stream", "arrow"),
    "parquet": ("application/vnd.apache.parquet", "parquet"),
}

GAPS = (
    "No Flight SQL server is deployed, so there is no gRPC endpoint ticket to fetch in parallel; "
    "the export is a single HTTP response.",
    "No ADBC driver is installed, so the client API is plain HTTP rather than ADBC's canonical "
    "uri/username/password configuration and built-in cancellation.",
    "No DuckDB-Wasm bundle is shipped, so the browser still receives JSON for the dashboard and "
    "cannot run SQL over the columnar buffer in a Web Worker.",
)


def tables() -> List[str]:
    """The exportable tables: the declared contract, minus the build metadata."""
    return [name for name in CONTRACTS if name not in _EXCLUDED]


def _check_table(table: str) -> str:
    if table not in CONTRACTS:
        raise ValueError(
            f"Unknown table {table!r}; the contract declares {sorted(CONTRACTS)}")
    if table in _EXCLUDED:
        raise ValueError(f"{table} is build metadata, not an exportable dataset")
    return table


def _frame(con, table: str) -> pd.DataFrame:
    """Read one tenant-scoped table. The temp view shadows the real table by name."""
    return pd.read_sql_query(f'SELECT * FROM "{table}"', con)


def _arrow(frame: pd.DataFrame) -> pa.Table:
    return pa.Table.from_pandas(frame, preserve_index=False)


def encode_json(frame: pd.DataFrame) -> bytes:
    """The baseline: row-oriented JSON, which is what the API already serves."""
    return frame.to_json(orient="records", date_format="iso").encode("utf-8")


def encode_arrow(frame: pd.DataFrame) -> bytes:
    """An Arrow IPC stream: one schema message followed by record batches."""
    table = _arrow(frame)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def encode_parquet(frame: pd.DataFrame) -> bytes:
    """Parquet with dictionary encoding and snappy, the format Superset and notebooks read."""
    sink = pa.BufferOutputStream()
    pq.write_table(_arrow(frame), sink, compression="snappy")
    return sink.getvalue().to_pybytes()


_ENCODERS = {"json": encode_json, "arrow": encode_arrow, "parquet": encode_parquet}


def _ratio(value: Optional[int], baseline: Optional[int]) -> Optional[float]:
    if not baseline or value is None:
        return None
    return round(value / baseline, 4)


def compare(path: Path, tenant: str, names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Encode each table three ways and report bytes and encode time for each.

    The JSON figure is the baseline because it is what the dashboard endpoint actually sends, so
    the ratio answers the question the requirement asks: how much of the payload is serialization
    overhead rather than data.
    """
    wanted = [_check_table(n) for n in (names or tables())]
    rows: List[Dict[str, Any]] = []
    with scoped(path, tenant) as con:
        for name in wanted:
            frame = _frame(con, name)
            entry: Dict[str, Any] = {"table": name, "rows": int(len(frame))}
            for fmt, encoder in _ENCODERS.items():
                started = time.perf_counter()
                payload = encoder(frame)
                entry[f"{fmt}_bytes"] = len(payload)
                entry[f"{fmt}_ms"] = round((time.perf_counter() - started) * 1000, 3)
            entry["arrow_ratio"] = _ratio(entry["arrow_bytes"], entry["json_bytes"])
            entry["parquet_ratio"] = _ratio(entry["parquet_bytes"], entry["json_bytes"])
            rows.append(entry)

    totals = {
        "rows": sum(r["rows"] for r in rows),
        **{f"{fmt}_bytes": sum(r[f"{fmt}_bytes"] for r in rows) for fmt in _ENCODERS},
        **{f"{fmt}_ms": round(sum(r[f"{fmt}_ms"] for r in rows), 3) for fmt in _ENCODERS},
    }
    totals["arrow_ratio"] = _ratio(totals["arrow_bytes"], totals["json_bytes"])
    totals["parquet_ratio"] = _ratio(totals["parquet_bytes"], totals["json_bytes"])

    return {
        "tenant": tenant,
        "tables": rows,
        "totals": totals,
        "formats": {fmt: media for fmt, (media, _) in FORMATS.items()},
        "gaps": list(GAPS),
    }


def export(path: Path, tenant: str, table: str, fmt: str = "arrow") -> Tuple[bytes, str, str]:
    """One tenant-scoped table as bytes, with its media type and a download filename."""
    _check_table(table)
    if fmt not in _ENCODERS:
        raise ValueError(f"Unknown format {fmt!r}; expected one of {sorted(_ENCODERS)}")
    with scoped(path, tenant) as con:
        frame = _frame(con, table)
    payload = _ENCODERS[fmt](frame)
    media, extension = FORMATS[fmt]
    return payload, media, f"{tenant}-{table}.{extension}"
