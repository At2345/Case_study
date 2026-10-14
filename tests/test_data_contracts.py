import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.config import settings
from app.data import loaders
from app.data.contracts import CONTRACTS

needs_data = pytest.mark.skipif(not settings.data_dir.exists(), reason="data not available")


def test_contract_covers_all_20_tables():
    assert len(CONTRACTS) == 20


def test_validate_detects_missing_column_and_wrong_type(tmp_path):
    pq.write_table(pa.table({"level_id": [1, 2], "item_id": ["a", "b"]}), tmp_path / "baseline_mapping.parquet")
    issues = loaders.validate_table("baseline_mapping", tmp_path)
    assert any("item_id" in i and "expected int" in i for i in issues)

    pq.write_table(pa.table({"level_id": [1]}), tmp_path / "baseline_mapping.parquet")
    assert any("missing column 'item_id'" in i for i in loaders.validate_table("baseline_mapping", tmp_path))


def test_missing_file_and_unknown_table(tmp_path):
    assert "file not found" in loaders.validate_table("items", tmp_path)[0]
    with pytest.raises(KeyError):
        loaders.table_path("nope")


def test_load_raises_on_violation(tmp_path):
    pq.write_table(pa.table({"level_id": [1]}), tmp_path / "baseline_mapping.parquet")
    with pytest.raises(loaders.ContractViolation):
        loaders.load("baseline_mapping", data_dir=tmp_path)


@needs_data
def test_all_real_tables_satisfy_contracts():
    problems = {k: v for k, v in loaders.validate_all().items() if v}
    assert not problems, problems


@needs_data
def test_known_row_counts():
    counts = loaders.row_counts()
    assert counts["items"] == 9173
    assert counts["baseline_items"] == 3560
    assert counts["baseline_mapping"] == 9173
    assert counts["overall_error_metrics"] == 1
    assert 33_000_000 < sum(counts.values()) < 34_500_000
