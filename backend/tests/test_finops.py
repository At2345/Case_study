"""PR-8.3: every governed query carries a cost tag, and the ledger attributes it."""
import sqlite3

import pytest

from app.bi.finops import (
    QuotaExceeded,
    QueryTag,
    attribute,
    check_quota,
    record,
    report,
    sprawl,
    storage,
    tag_for,
    thresholds,
)


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    """Point the attribution ledger at a temp file so `var/` is never touched."""
    path = tmp_path / "finops.db"
    monkeypatch.setenv("FINOPS_LOG", str(path))
    return path


@pytest.fixture
def mart(tmp_path):
    path = tmp_path / "mart.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE cycle_summary (tenant_id TEXT, in_scope_skus INTEGER,"
                    " autopilot_approved_skus INTEGER)")
        con.execute("INSERT INTO cycle_summary VALUES ('default', 10, 4)")
    return path


TAG = QueryTag(team="Demand Planning", pipeline="metrics", workload="bi")


class TestQueryTag:
    def test_an_unknown_workload_is_refused(self):
        with pytest.raises(ValueError, match="workload"):
            QueryTag(team="Demand Planning", pipeline="metrics", workload="etl")

    def test_a_tag_needs_a_team_and_a_pipeline(self):
        with pytest.raises(ValueError, match="team and a pipeline"):
            QueryTag(team="", pipeline="metrics", workload="bi")

    def test_the_team_comes_from_the_role(self):
        assert tag_for({"role": "planner"}, "dashboard").team == "Demand Planning"
        assert tag_for({"role": "auditor"}, "dashboard").team == "Audit"

    def test_a_role_with_no_declared_team_is_refused(self):
        with pytest.raises(ValueError, match="No owning team"):
            tag_for({"role": "contractor"}, "dashboard")


class TestAttribution:
    def test_a_metric_query_and_a_request_are_counted_apart(self):
        record(TAG, "default", kind="metric", metric_ids=["in_scope_skus"], statements=3, duration_ms=12.0)
        record(TAG, "default", kind="request", duration_ms=40.0)
        body = attribute()
        assert body["metric_queries"] == 1
        assert body["requests"] == 1
        assert body["statements"] == 3
        assert body["duration_ms"] == pytest.approx(52.0)

    def test_rows_roll_up_by_team_pipeline_and_workload(self):
        record(TAG, "default", kind="metric", statements=2, duration_ms=5.0)
        record(QueryTag(team="Platform", pipeline="finops", workload="admin"), "default",
               kind="request", duration_ms=7.0)
        body = attribute()
        assert [b["team"] for b in body["by_team"]] == ["Demand Planning", "Platform"]
        assert [b["pipeline"] for b in body["by_pipeline"]] == ["metrics", "finops"]
        assert {b["workload"] for b in body["by_workload"]} == {"bi", "admin"}

    def test_an_empty_ledger_reports_zero_rather_than_failing(self):
        body = attribute()
        assert body["metric_queries"] == 0 and body["by_team"] == []

    def test_a_broken_ledger_does_not_raise(self, tmp_path, monkeypatch):
        """Attribution is observability: it must never fail the query it measures."""
        monkeypatch.setenv("FINOPS_LOG", str(tmp_path / "nope" / "x" / "finops.db"))
        (tmp_path / "nope").write_text("not a directory")
        record(TAG, "default", kind="metric")  # no exception


class TestStorage:
    def test_the_mart_is_measured_per_table(self, mart):
        body = storage(mart)
        assert body["bytes"] > 0
        assert body["tables"] == [{"table": "cycle_summary", "rows": 1,
                                   "bytes": body["tables"][0]["bytes"]}]

    def test_a_missing_mart_reports_zero(self, tmp_path):
        body = storage(tmp_path / "absent.db")
        assert body["bytes"] == 0 and body["tables"] == []


class TestThresholds:
    def test_the_three_tiers_are_declared(self):
        body = thresholds()
        assert [t["id"] for t in body["tiers"]] == ["attribution", "capacity", "third_party"]
        assert [t["amount_usd"] for t in body["tiers"]] == [15000, 250000, 30000]

    def test_no_tier_is_claimed_active_without_a_cost_model(self):
        """There is no warehouse here, so no dollar figure can be computed."""
        body = thresholds()
        assert body["active"] is None
        assert body["measured_usd"] is None
        assert "No cloud warehouse" in body["reason"]


class TestSprawl:
    def test_a_metric_nobody_evaluated_is_a_prune_candidate(self):
        record(TAG, "default", kind="metric", metric_ids=["in_scope_skus"])
        body = sprawl()
        assert "in_scope_skus" not in body["metrics"]["prune_candidates"]
        assert "autopilot_share" in body["metrics"]["prune_candidates"]
        assert body["metrics"]["evaluated"] == 1

    def test_a_surface_nobody_called_is_an_archive_candidate(self):
        record(TAG, "default", kind="metric", metric_ids=["in_scope_skus"])
        body = sprawl()
        assert body["pipelines"]["called"] == ["metrics"]
        assert "dashboard" in body["pipelines"]["archive_candidates"]


class TestQuota:
    def test_the_allowance_is_enforced(self):
        for _ in range(2):
            record(QueryTag(team="Demand Planning", pipeline="ask", workload="ai"), "default",
                   kind="request", statements=1)
        check_quota("default", "ai", 3, 3600)  # under the limit
        with pytest.raises(QuotaExceeded, match="quota exceeded"):
            check_quota("default", "ai", 2, 3600)

    def test_a_zero_limit_disables_the_cap(self):
        record(QueryTag(team="Demand Planning", pipeline="ask", workload="ai"), "default",
               kind="request", statements=1)
        check_quota("default", "ai", 0, 3600)  # no exception

    def test_another_tenant_does_not_spend_this_tenants_allowance(self):
        record(QueryTag(team="Demand Planning", pipeline="ask", workload="ai"), "globex",
               kind="request", statements=1)
        check_quota("default", "ai", 1, 3600)  # no exception


class TestEvaluateIsTagged:
    def test_a_metric_query_cannot_be_issued_without_a_tag(self, mart):
        from app.bi.semantics import evaluate
        with pytest.raises(TypeError):
            evaluate(mart, ["in_scope_skus"], "default")

    def test_evaluate_records_its_statements(self, mart):
        from app.bi.semantics import evaluate
        values = evaluate(mart, ["in_scope_skus", "autopilot_share"], "default", TAG)
        assert values["in_scope_skus"] == 10
        body = attribute()
        assert body["metric_queries"] == 1
        assert body["statements"] == 1  # both metrics share one table, so one statement
        assert body["by_team"][0]["team"] == "Demand Planning"


class TestReport:
    def test_the_report_carries_all_four_sections(self, mart):
        body = report(mart)
        assert set(body) == {"attribution", "storage", "thresholds", "sprawl"}
