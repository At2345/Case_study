from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.config import settings
from app.data.contracts import CONTRACTS

_KIND_CHECKS = {
    "int": pa.types.is_integer,
    "float": pa.types.is_floating,
    "str": lambda t: pa.types.is_string(t) or pa.types.is_large_string(t),
    "datetime": pa.types.is_timestamp,
    "bool": pa.types.is_boolean,
}


class ContractViolation(ValueError):
    pass


def table_path(name: str, data_dir: Optional[Path] = None) -> Path:
    if name not in CONTRACTS:
        raise KeyError(f"Unknown table '{name}'. Known: {sorted(CONTRACTS)}")
    return Path(data_dir or settings.data_dir) / f"{name}.parquet"


def validate_table(name: str, data_dir: Optional[Path] = None) -> List[str]:
    """Return a list of contract issues for a table (empty list means valid). Reads schema only."""
    path = table_path(name, data_dir)
    if not path.exists():
        return [f"{name}: file not found at {path}"]
    schema = pq.read_schema(path)
    issues: List[str] = []
    for col, kind in CONTRACTS[name].items():
        if col not in schema.names:
            issues.append(f"{name}: missing column '{col}'")
            continue
        if not _KIND_CHECKS[kind](schema.field(col).type):
            issues.append(f"{name}.{col}: expected {kind}, got {schema.field(col).type}")
    return issues


def validate_all(data_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    return {name: validate_table(name, data_dir) for name in CONTRACTS}


@lru_cache(maxsize=64)
def _load_cached(name: str, columns: Optional[Tuple[str, ...]], data_dir: str) -> pd.DataFrame:
    """Full-table read cached per (name, columns, data_dir). Tables are immutable per data generation."""
    path = table_path(name, Path(data_dir))
    return pq.read_table(path, columns=list(columns) if columns else None).to_pandas()


def clear_cache() -> None:
    """Drop cached tables (call after a data regeneration / reload)."""
    _load_cached.cache_clear()


def load(
    name: str,
    columns: Optional[List[str]] = None,
    data_dir: Optional[Path] = None,
    validate: bool = True,
    filters: Optional[list] = None,
) -> pd.DataFrame:
    if validate:
        issues = validate_table(name, data_dir)
        if issues:
            raise ContractViolation("; ".join(issues))
    if filters is not None:
        return pq.read_table(table_path(name, data_dir), columns=columns, filters=filters).to_pandas()
    return _load_cached(name, tuple(columns) if columns else None, str(Path(data_dir or settings.data_dir)))


def row_counts(data_dir: Optional[Path] = None) -> Dict[str, int]:
    return {n: pq.ParquetFile(table_path(n, data_dir)).metadata.num_rows for n in CONTRACTS}
