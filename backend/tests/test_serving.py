"""PR-3.1/3.2/3.3: freshness tiers, workload latency, index coverage and columnar transport."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.bi.finops import QueryTag, record
from app.bi.serving import (
    FRESHNESS,
    INDEXES,
    TIERS,
    WORKLOADS,
    ensure_indexes,
    freshness,
    index_coverage,
    latency,
    report,
)
from app.bi.transport import compare, encode_arrow, encode_json, encode_parquet, export, tables

TAG = QueryTag(team="Demand Planning", pipeline="metrics", workload="bi")


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    """Point the attribution ledger at a temp file so `var/` is never touched."""
    path = tmp_path / "finops.db"
    monkeypatch.setenv("FINOPS_LOG", str(path))
    return path


def _mart(path, generated_at=None):
    stamp = (generated_at or datetime.now(timezone.utc)).isoformat()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE mart_meta (tenant_id TEXT, generated_at TEXT, reports_dir TEXT,"
                    " store_dir TEXT, tables TEXT)")
        con.execute("CREATE TABLE triage_items (tenant_id TEXT, item_id INTEGER, in_scope INTEGER,"
                    " channel TEXT, risk_type TEXT)")
        con.execute("CREATE TABLE overrides (tenant_id TEXT, planner_id TEXT, reason_code TEXT)")
        con.execute("CREATE TABLE commits (tenant_id TEXT, cutoff TEXT, planner_id TEXT)")
        con.execute("INSERT INTO mart_meta VALUES ('default', ?, '', '', '{}')", (stamp,))
        con.executemany("INSERT INTO triage_items VALUES (?,?,?,?,?)", [
            ("default", 1, 1, "AMAZON", "OOS_RISK"),
            ("default", 2, 1, "D2C", "OVERSTOCK_RISK"),
            ("globex", 3, 1, "AMAZON", "OOS_RISK"),
        ])
        con.execute("INSERT INTO overrides VALUES ('default','danny','Promotion')")
        con.execute("INSERT INTO commits VALUES ('default','2026-02-01','danny')")
    return path


@pytest.fixture
def mart(tmp_path):
    return _mart(tmp_path / "mart.db")


class TestColumnarEncoding:
    def test_arrow_round_trips_the_frame(self, mart):
        from app.bi.tenancy import scoped
        import pandas as pd
        with scoped(mart, "default") as con:
            frame = pd.read_sql_query('SELECT * FROM "triage_items"', con)
        decoded = pa.ipc.open_stream(pa.BufferReader(encode_arrow(frame))).read_all()
        assert decoded.num_rows == len(frame)
        assert set(decoded.column_names) == set(frame.columns)

    def test_parquet_round_trips_the_frame(self, mart):
        from app.bi.tenancy import scoped
        import pandas as pd
        with scoped(mart, "default") as con:
            frame = pd.read_sql_query('SELECT * FROM "triage_items"', con)
        decoded = pq.read_table(pa.BufferReader(encode_parquet(frame)))
        assert decoded.num_rows == len(frame)

    def test_columnar_wins_once_the_table_is_large(self):
        """The crossover is the point: Arrow's schema overhead dominates a handful of rows."""
        import pandas as pd
        frame = pd.DataFrame({
            "item_id": range(500),
            "channel": ["AMAZON"] * 500,
            "in_scope": [1] * 500,
            "var_gbp": [float(i) for i in range(500)],
        })
        assert len(encode_arrow(frame)) < len(encode_json(frame))
        assert len(encode_parquet(frame)) < len(encode_json(frame))

    def test_arrow_loses_on_a_tiny_table_and_the_report_says_so(self, mart):
        """Two rows: the IPC schema message is larger than the data it describes."""
        body = compare(mart, "default", ["triage_items"])
        row = body["tables"][0]
        assert row["rows"] == 2
        assert row["arrow_ratio"] > 1


class TestComparison:
    def test_every_format_is_measured_for_every_table(self, mart):
        body = compare(mart, "default", ["triage_items"])
        row = body["tables"][0]
        assert row["table"] == "triage_items" and row["rows"] == 2
        for fmt in ("json", "arrow", "parquet"):
            assert row[f"{fmt}_bytes"] > 0
            assert row[f"{fmt}_ms"] >= 0
        assert row["arrow_ratio"] is not None and row["parquet_ratio"] is not None

    def test_the_totals_are_the_sum_of_the_tables(self, mart):
        body = compare(mart, "default", ["triage_items", "overrides"])
        assert body["totals"]["json_bytes"] == sum(r["json_bytes"] for r in body["tables"])
        assert body["totals"]["rows"] == 3

    def test_an_unknown_table_is_refused(self, mart):
        with pytest.raises(ValueError, match="Unknown table"):
            compare(mart, "default", ["not_a_table"])

    def test_the_build_metadata_is_not_exportable(self, mart):
        with pytest.raises(ValueError, match="build metadata"):
            compare(mart, "default", ["mart_meta"])

    def test_the_gaps_are_declared_rather_than_implied(self, mart):
        body = compare(mart, "default", ["triage_items"])
        assert any("Flight SQL" in gap for gap in body["gaps"])
        assert any("DuckDB-Wasm" in gap for gap in body["gaps"])


class TestExport:
    def test_arrow_export_carries_the_media_type_and_filename(self, mart):
        payload, media, filename = export(mart, "default", "triage_items", "arrow")
        assert media == "application/vnd.apache.arrow.stream"
        assert filename == "default-triage_items.arrow"
        assert pa.ipc.open_stream(pa.BufferReader(payload)).read_all().num_rows == 2

    def test_parquet_export_is_readable(self, mart):
        payload, media, _ = export(mart, "default", "triage_items", "parquet")
        assert media == "application/vnd.apache.parquet"
        assert pq.read_table(pa.BufferReader(payload)).num_rows == 2

    def test_the_export_is_tenant_scoped(self, mart):
        """The unscoped table holds three rows; each tenant must see only its own."""
        with sqlite3.connect(mart) as con:
            assert con.execute("SELECT COUNT(*) FROM triage_items").fetchone()[0] == 3
        default = pq.read_table(pa.BufferReader(export(mart, "default", "triage_items", "parquet")[0]))
        globex = pq.read_table(pa.BufferReader(export(mart, "globex", "triage_items", "parquet")[0]))
        assert default.num_rows == 2
        assert globex.num_rows == 1
        assert set(default.column("tenant_id").to_pylist()) == {"default"}
        assert set(globex.column("tenant_id").to_pylist()) == {"globex"}

    def test_an_unknown_format_is_refused(self, mart):
        with pytest.raises(ValueError, match="Unknown format"):
            export(mart, "default", "triage_items", "csv")

    def test_the_exportable_tables_exclude_the_build_metadata(self):
        assert "mart_meta" not in tables()
        assert "triage_items" in tables()


class TestIndexCoverage:
    def test_an_unindexed_mart_reports_the_missing_indexes(self, mart):
        body = index_coverage(mart)
        assert body["ok"] is False
        assert "ix_triage_items_in_scope" in body["missing"]

    def test_ensure_indexes_closes_the_gap(self, mart):
        with sqlite3.connect(mart) as con:
            ensure_indexes(con)
        body = index_coverage(mart)
        assert body["ok"] is True
        assert body["missing"] == []
        assert "ix_triage_items_in_scope" in body["present"]

    def test_an_absent_table_is_not_reported_as_missing(self, mart):
        """triage_monthly is declared but this mart does not carry it."""
        with sqlite3.connect(mart) as con:
            ensure_indexes(con)
        body = index_coverage(mart)
        assert "ix_triage_monthly_date" not in body["missing"]

    def test_a_missing_mart_is_reported_rather_than_raised(self, tmp_path):
        body = index_coverage(tmp_path / "absent.db")
        assert body["ok"] is False and "does not exist" in body["reason"]


class TestFreshness:
    def test_a_fresh_mart_breaches_nothing(self, mart):
        body = freshness(mart, "default")
        assert body["ok"] is True
        assert body["breaches"] == []
        assert body["age_s"] < TIERS["hourly"]

    def test_the_hourly_tables_breach_before_the_daily_ones(self, tmp_path):
        """Two hours old: the write-back tables are stale, the rebuilt ones are not."""
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        mart = _mart(tmp_path / "old.db", generated_at=old)
        body = freshness(mart, "default")
        assert set(body["breaches"]) == {"overrides", "commits"}
        assert body["ok"] is False

    def test_a_table_absent_from_the_mart_is_not_a_breach(self, mart):
        body = freshness(mart, "default")
        row = next(r for r in body["tables"] if r["table"] == "triage_monthly")
        assert row["present"] is False and row["breach"] is False

    def test_every_declared_table_has_a_tier(self):
        assert set(FRESHNESS.values()) <= set(TIERS)

    def test_a_mart_with_no_metadata_reports_no_measurement(self, tmp_path):
        path = tmp_path / "empty.db"
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE mart_meta (tenant_id TEXT, generated_at TEXT)")
        body = freshness(path, "default")
        assert body["generated_at"] is None
        assert body["ok"] is False
        assert all(r["age_s"] is None for r in body["tables"])


class TestLatency:
    def test_a_workload_with_no_samples_is_unmeasured_not_passing(self):
        body = latency()
        assert set(body["unmeasured"]) == set(WORKLOADS)
        assert all(r["ok"] is None for r in body["workloads"])

    def test_a_workload_under_target_passes(self):
        for ms in (10.0, 20.0, 30.0):
            record(QueryTag(team="Demand Planning", pipeline="metrics", workload="bi"),
                   "default", kind="metric", statements=1, duration_ms=ms)
        row = next(r for r in latency()["workloads"] if r["workload"] == "metrics")
        assert row["samples"] == 3
        assert row["measured_p95_ms"] == 30.0
        assert row["ok"] is True

    def test_a_workload_over_target_breaches(self):
        for ms in (100.0, 200.0, 900.0):
            record(QueryTag(team="Demand Planning", pipeline="dashboard", workload="bi"),
                   "default", kind="request", duration_ms=ms)
        body = latency()
        row = next(r for r in body["workloads"] if r["workload"] == "dashboard")
        assert row["measured_p95_ms"] == 900.0
        assert row["ok"] is False
        assert "dashboard" in body["breaches"]

    def test_the_percentile_uses_the_distribution_not_the_mean(self):
        """A 10% slow tail must move p95; an average would hide it in the fast majority."""
        for ms in [10.0] * 90 + [5000.0] * 10:
            record(QueryTag(team="Demand Planning", pipeline="metrics", workload="bi"),
                   "default", kind="metric", statements=1, duration_ms=ms)
        row = next(r for r in latency()["workloads"] if r["workload"] == "metrics")
        assert row["measured_p95_ms"] == 5000.0
        assert row["ok"] is False


class TestReport:
    def test_the_report_carries_all_four_sections(self, mart):
        body = report(mart, "default")
        assert set(body) == {"freshness", "latency", "indexes", "gaps"}

    def test_the_gaps_name_what_is_not_deployed(self, mart):
        body = report(mart, "default")
        assert any("streaming ingestion" in gap for gap in body["gaps"])
        assert any("OLAP engine" in gap for gap in body["gaps"])

    def test_the_index_map_covers_the_hot_tables(self):
        assert "triage_items" in INDEXES and "overrides" in INDEXES
