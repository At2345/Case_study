import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({
  api: {
    biDashboard: vi.fn(), biDashboardExceptions: vi.fn(), biKpiTree: vi.fn(), biMetrics: vi.fn(), biDiagnostics: vi.fn(),
    biContracts: vi.fn(),
    biFinops: vi.fn(),
    biServing: vi.fn(), biTransport: vi.fn(), biExportColumnar: vi.fn(),
    biActions: vi.fn(), biAnnotations: vi.fn(), biAnnotate: vi.fn(),
    biProposeAction: vi.fn(), biDecideAction: vi.fn(),
    biAsk: vi.fn(), biExport: vi.fn(),
  },
}));
import { api } from "../services/api";
import BiPage from "./BiPage";

const payload = (over = {}) => ({
  cycle: {
    cutoff: "2026-02-01", first_month: "2026-03-01", generated_at: "2026-11-14T16:54:19Z",
    total_catalog_skus: 9162, in_scope_skus: 5499, autopilot_approved_skus: 4617,
    exceptions_requiring_review: 74, planner_led_skus: 808,
    planned_value_gbp: 1234567, baseline_value_gbp: 1200000, net_value_variance_gbp: 34567,
    quality_warnings: 1,
  },
  by_state: [
    { state: "AUTO_APPROVED", count: 4617 }, { state: "EXCEPTION_REVIEW", count: 74 },
    { state: "PLANNER_LED", count: 808 }, { state: "INACTIVE", count: 3663 },
  ],
  exceptions_by_channel: [{ channel: "AMAZON", count: 40 }, { channel: "D2C", count: 20 }, { channel: "B2B", count: 14 }],
  var_by_risk_type: [{ risk_type: "OOS_RISK", var_gbp: 12000 }, { risk_type: "OVERSTOCK_RISK", var_gbp: 8000 }],
  plan_vs_baseline: [
    { month: "2026-03-01", plan: 100, baseline: 90, ml: 98 },
    { month: "2026-04-01", plan: 110, baseline: 95, ml: 105 },
  ],
  top_exceptions: [{ item_id: 100001, product_id: "PROD_2742", channel: "D2C", risk_type: "OOS_RISK", var_gbp: 1200, value_delta_gbp: -300 }],
  totals: { exceptions: 74, var_gbp: 20000 },
  overrides_by_reason: [{ reason_code: "Promotion", count: 10 }, { reason_code: "Supply constraint", count: 6 }],
  fva_by_planner: [{ planner_id: "danny", n_overrides: 22, n_accepted: 18, volume: 5000, fpe_ml: 0.31, fpe_planner: 0.28, fpe_baseline: 0.35, fva_pts: 1.2 }],
  quality_warnings: [{ channel: "AMAZON", month: "2026-02-01", imputed_share: 1.0, items: 3080 }],
  mart: {
    path: "D:/Case_study_2/var/bi_mart.db", exists: true,
    generated_at: "2026-11-14T16:54:19Z",
    tables: { triage_items: 9162, triage_monthly: 109944, overrides: 22 },
  },
  superset: {
    url: "http://localhost:8088",
    dashboard_url: "http://localhost:8088/superset/dashboard/sop-overview/",
    slug: "sop-overview", reachable: true,
  },
  duration_ms: 42,
  freshness: {
    mart_generated_at: "2026-11-14T16:54:19Z",
    cycle_cutoff: "2026-02-01",
    first_month: "2026-03-01",
  },
  ...over,
});

const breakdownFixture = (over = {}) => ({
  exceptions_by_channel: [{ channel: "AMAZON", count: 40 }, { channel: "D2C", count: 20 }, { channel: "B2B", count: 14 }],
  var_by_risk_type: [{ risk_type: "OOS_RISK", var_gbp: 3000 }],
  top_exceptions: [{ item_id: 100002, product_id: "PROD_9001", channel: "D2C", risk_type: "OOS_RISK", var_gbp: 900, value_delta_gbp: -100 }],
  totals: { exceptions: 20, var_gbp: 3000 },
  filter: { channel: "D2C", risk_type: null },
  ...over,
});

const kpiTree = () => ({
  root: {
    id: "autopilot", label: "Autopilot coverage", owner: "Head of Supply Chain",
    confidence: "high", signal: "outcome", unit: "share", corridor: { min: 0.7 },
    value: 0.84, health: "healthy",
    branches: [
      {
        id: "accuracy", label: "Forecast accuracy", owner: "Demand Planning Lead",
        confidence: "high", signal: "output", unit: "share", corridor: { min: 0.5 },
        value: 0.559, health: "healthy",
        leaves: [
          { id: "ml_coverage", label: "ML-chosen items", owner: "Demand Planning Lead", confidence: "high", signal: "output", unit: "share", corridor: { min: 0.5 }, value: 0.559, health: "healthy" },
          { id: "fallback_share", label: "Median fallbacks", owner: "Demand Planning Lead", confidence: "high", signal: "output", unit: "share", corridor: { max: 0.4 }, value: 0.342, health: "healthy" },
        ],
      },
      {
        id: "risk", label: "Inventory risk", owner: "Inventory Manager",
        confidence: "high", signal: "outcome", unit: "gbp", corridor: { max: 1250000 },
        value: 1664271, health: "breach",
        leaves: [
          { id: "oos_exposure", label: "OOS exposure", owner: "Inventory Manager", confidence: "high", signal: "outcome", unit: "gbp", corridor: { max: 500000 }, value: 635237, health: "breach" },
        ],
      },
    ],
  },
  counts: { root: 1, branches: 2, leaves: 3, total: 6 },
});

const metricCatalog = () => ({
  metrics: [
    {
      id: "autopilot_share", label: "Autopilot coverage", owner: "Head of Supply Chain",
      unit: "share", table: "cycle_summary", grain: "cycle", aggregation: "ratio",
      numerator: "autopilot_approved_skus", denominator: "in_scope_skus", expression: "",
      where: "", scale: 1, semi_additive: false, version: "1.0.0", description: "",
      sql: "SELECT CAST(SUM(autopilot_approved_skus) AS REAL) / NULLIF(SUM(in_scope_skus), 0) AS value FROM cycle_summary",
      value: 0.84,
    },
    {
      id: "oos_exposure_gbp", label: "OOS exposure", owner: "Inventory Manager",
      unit: "gbp", table: "triage_items", grain: "item", aggregation: "sum",
      numerator: "", denominator: "", expression: "CASE WHEN risk_type = 'OOS_RISK' THEN var_gbp ELSE 0 END",
      where: "in_scope = 1", scale: 1, semi_additive: false, version: "1.0.0", description: "",
      sql: "SELECT SUM(CASE WHEN risk_type = 'OOS_RISK' THEN var_gbp ELSE 0 END) AS value FROM triage_items WHERE in_scope = 1",
      value: 635237,
    },
  ],
});

const diagnostics = () => ({
  role: "planner",
  protocol: [
    { id: "temporal_precedence", label: "Temporal precedence", rule: "The proposed driver moved at or before the KPI." },
    { id: "arithmetic_reconciliation", label: "Arithmetic reconciliation", rule: "Segment contributions sum to the total variance." },
    { id: "permission_preserved", label: "Permission preserved", rule: "Evidence is served at a grain the requester may read." },
    { id: "named_approver", label: "Named approver", rule: "The cause is routed to a named owner before any action." },
  ],
  series: [
    {
      series: "plan_qty", label: "Planned units", unit: "qty", owner: "Head of Demand Planning",
      metric: "planned_value_gbp", center: 171329.9, spread: 26310.8, threshold: 3.5,
      points: [{ month: "2026-08-01", value: 319536.6 }, { month: "2026-09-01", value: 423962.4 }],
      anomalies: [{
        month: "2026-09-01", value: 423962.4, expected: 171329.9, deviation: 252632.5,
        z: 6.48, direction: "above", severity: "medium",
      }],
      forecast: {
        method: "linear_trend_last_6_months", band: 1000,
        points: [{ month: "2027-03-01", value: 150000, low: 149000, high: 151000 }],
        caveat: "Naive trend baseline, not a seasonal model.",
      },
      rca: [{
        series: "plan_qty", month: "2026-09-01", dimension: "channel",
        baseline: 171329.9, total_deviation: 252632.5, leading_driver: "amazon",
        contributions: [
          { segment: "amazon", value: 176099.5, expected: 120000, contribution: 56099.5, z: 4.1, first_deviation: "2026-08-01" },
          { segment: "D2C", value: 23757.1, expected: 20000, contribution: 3757.1, z: 1.2, first_deviation: null },
        ],
        evidence: [{ item_id: "100001", product_id: "PROD_2742", channel: "amazon", risk_type: "OOS_RISK", var_gbp: 1200 }],
        evidence_withheld: false,
        checks: [
          { id: "temporal_precedence", passed: true, detail: "amazon first deviated in 2026-08-01, at or before 2026-09-01." },
          { id: "arithmetic_reconciliation", passed: true, detail: "Segments explain 252,632.50 of 252,632.50 (gap 0.000000)." },
          { id: "permission_preserved", passed: true, detail: "planner may read item-level evidence." },
          { id: "named_approver", passed: true, detail: "Routed to Head of Demand Planning.", approver: "Head of Demand Planning" },
        ],
        actionable: true, status: "actionable", approver: "Head of Demand Planning",
      }],
    },
  ],
  summary: { series: 1, anomalies: 1, actionable: 1, hypotheses: 0 },
});

const hypothesisDiagnostics = () => {
  const data = diagnostics();
  const cause = data.series[0].rca[0];
  cause.checks = cause.checks.map((c) => (c.id === "permission_preserved"
    ? { ...c, passed: false, detail: "item-level evidence requires one of ['admin', 'planner']; auditor is limited to ['aggregate', 'segment']." }
    : c));
  cause.actionable = false;
  cause.status = "hypothesis";
  cause.evidence = [];
  cause.evidence_withheld = true;
  data.summary = { series: 1, anomalies: 1, actionable: 0, hypotheses: 1 };
  return data;
};

const actionFixture = (over = {}) => ({
  action_id: "act1", event: "proposed", state: "pending",
  cutoff: "2026-02-01", series: "plan_qty", month: "2026-09-01", driver: "amazon",
  approver: "Head of Demand Planning", note: "Investigate the amazon contribution.",
  actor: "danny", role: "planner",
  history: [{ event: "proposed", actor: "danny", role: "planner", note: "Investigate.", timestamp: "2026-11-15T20:00:00Z" }],
  ...over,
});

const contractFixture = (over = {}) => ({
  contract: { tables: [], summary: { tables: 12, columns: 120 } },
  conformance: {
    ok: true, tables: [], problems: [],
    summary: { tables: 12, present: 12, errors: 0, warnings: 0 },
  },
  mart: "var/bi_mart.db", mart_present: true,
  ...over,
});

const annotationFixture = (over = {}) => ({
  id: 1, cutoff: "2026-02-01", target_type: "item", target_id: "100001",
  body: "Supply constrained", author: "danny", role: "planner",
  ...over,
});

const finopsFixture = (over = {}) => ({
  attribution: {
    window_days: 30, metric_queries: 12, requests: 5, statements: 31, duration_ms: 240.5,
    by_team: [{ team: "Demand Planning", metric_queries: 12, requests: 5, statements: 31, duration_ms: 240.5 }],
    by_pipeline: [{ pipeline: "metrics", metric_queries: 12, requests: 0, statements: 31, duration_ms: 240.5 }],
    by_workload: [{ workload: "bi", metric_queries: 12, requests: 5, statements: 31, duration_ms: 240.5 }],
  },
  storage: {
    path: "var/bi_mart.db", bytes: 1048576, dbstat: false,
    tables: [{ table: "cycle_summary", rows: 1, bytes: null }],
  },
  thresholds: {
    tiers: [
      { id: "attribution", period: "month", amount_usd: 15000, obligation: "Query attribution becomes mandatory." },
      { id: "capacity", period: "year", amount_usd: 250000, obligation: "Evaluate a capacity pre-purchase." },
      { id: "third_party", period: "month", amount_usd: 30000, obligation: "Consider paid FinOps tooling." },
    ],
    measured_usd: null, active: null,
    reason: "No cloud warehouse is configured, so no dollar spend can be computed.",
  },
  sprawl: {
    window_days: 90,
    metrics: { evaluated: 3, total: 18, prune_candidates: ["fallback_share", "routed_share"] },
    pipelines: { called: ["metrics"], archive_candidates: ["dashboard", "ask"] },
  },
  ...over,
});

const servingFixture = (over = {}) => ({
  freshness: {
    generated_at: "2026-11-16T18:00:00Z", age_s: 1800, ok: true, breaches: [],
    tiers: { realtime: 60, hourly: 3600, daily: 86400, weekly: 604800 },
    tables: [
      { table: "triage_items", tier: "daily", sla_s: 86400, present: true, age_s: 1800, breach: false },
      { table: "overrides", tier: "hourly", sla_s: 3600, present: true, age_s: 1800, breach: false },
      { table: "commits", tier: "hourly", sla_s: 3600, present: false, age_s: 1800, breach: false },
    ],
  },
  latency: {
    window_days: 30, breaches: [],
    workloads: [
      { workload: "dashboard", persona: "Executive / Operations", target_p95_ms: 400, measured_p95_ms: 120, samples: 5, ok: true },
      { workload: "ask", persona: "Analyst", target_p95_ms: 60000, measured_p95_ms: null, samples: 0, ok: null },
    ],
  },
  indexes: { declared: [{}, {}, {}], present: ["ix_a", "ix_b", "ix_c"], missing: [], ok: true },
  gaps: ["No streaming ingestion exists: the mart is rebuilt on demand.", "No OLAP engine is deployed."],
  ...over,
});

const transportFixture = (over = {}) => ({
  tenant: "default",
  tables: [{
    table: "triage_items", rows: 9162, json_bytes: 2000000, arrow_bytes: 900000, parquet_bytes: 400000,
    json_ms: 120, arrow_ms: 20, parquet_ms: 40, arrow_ratio: 0.45, parquet_ratio: 0.2,
  }],
  totals: {
    rows: 9162, json_bytes: 2000000, arrow_bytes: 900000, parquet_bytes: 400000,
    json_ms: 120, arrow_ms: 20, parquet_ms: 40, arrow_ratio: 0.45, parquet_ratio: 0.2,
  },
  formats: {
    json: "application/json",
    arrow: "application/vnd.apache.arrow.stream",
    parquet: "application/vnd.apache.parquet",
  },
  gaps: [],
  ...over,
});

describe("BiPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.biDashboard.mockResolvedValue(payload());
    api.biDashboardExceptions.mockResolvedValue(breakdownFixture());
    api.biKpiTree.mockResolvedValue(kpiTree());
    api.biMetrics.mockResolvedValue(metricCatalog());
    api.biDiagnostics.mockResolvedValue(diagnostics());
    api.biContracts.mockResolvedValue(contractFixture());
    api.biFinops.mockResolvedValue(finopsFixture());
    api.biServing.mockResolvedValue(servingFixture());
    api.biTransport.mockResolvedValue(transportFixture());
    api.biExportColumnar.mockResolvedValue(new Blob(["x"]));
    api.biActions.mockResolvedValue({ actions: [actionFixture()] });
    api.biAnnotations.mockResolvedValue({ annotations: [annotationFixture()] });
    api.biProposeAction.mockResolvedValue({ action_id: "act2", state: "pending" });
    api.biDecideAction.mockResolvedValue({ action_id: "act1", event: "approved" });
    api.biAnnotate.mockResolvedValue({ id: 2 });
  });

  it("renders the KPIs and every chart panel from the mart", async () => {
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-kpis")).toBeInTheDocument();
    for (const id of ["bi-triage-donut", "bi-channel-bars", "bi-risk-bars", "bi-plan-line",
      "bi-top-table", "bi-overrides-bars", "bi-fva-table", "bi-mart-tables"]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });

  it("shows the autopilot share and the cycle value", async () => {
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-kpis");
    expect(screen.getByText("4,617 (84%)")).toBeInTheDocument();
    expect(screen.getByText("£1,234,567")).toBeInTheDocument();
    expect(screen.getByText("+£34,567")).toBeInTheDocument();
  });

  it("renders chart labels and table rows", async () => {
    render(<BiPage role="planner" />);
    const triage = await screen.findByTestId("bi-triage-donut");
    expect(within(triage).getAllByText("Autopilot").length).toBeGreaterThan(0);
    expect(within(triage).getAllByText("Exceptions").length).toBeGreaterThan(0);
    const top = screen.getByTestId("bi-top-table");
    expect(within(top).getByText("PROD_2742")).toBeInTheDocument();
    expect(within(top).getByText("OOS exposure")).toBeInTheDocument();
    expect(within(screen.getByTestId("bi-fva-table")).getByText("danny")).toBeInTheDocument();
  });

  it("hides the rebuild button for planners and shows it for admins", async () => {
    const { unmount } = render(<BiPage role="planner" />);
    await screen.findByTestId("bi-mart-tables");
    expect(screen.queryByTestId("bi-rebuild-btn")).toBeNull();
    unmount();
    render(<BiPage role="admin" />);
    expect(await screen.findByTestId("bi-rebuild-btn")).toBeInTheDocument();
  });

  it("admin rebuild calls the export endpoint and refreshes", async () => {
    api.biExport.mockResolvedValue({ path: "x", tables: {} });
    const user = userEvent.setup();
    render(<BiPage role="admin" />);
    await screen.findByTestId("bi-mart-tables");
    await user.click(screen.getByTestId("bi-rebuild-btn"));
    await waitFor(() => expect(api.biExport).toHaveBeenCalled());
    expect(api.biDashboard).toHaveBeenCalledTimes(2);
  });

  it("explains how to build the mart when it is missing", async () => {
    api.biDashboard.mockResolvedValue(payload({ mart: { path: "x", exists: false, generated_at: null, tables: {} } }));
    render(<BiPage role="planner" />);
    const panel = await screen.findByTestId("bi-mart-panel");
    expect(within(panel).getByText(/python -m app.bi.export_mart/)).toBeInTheDocument();
  });

  it("keeps working when Superset is down", async () => {
    api.biDashboard.mockResolvedValue(payload({ superset: { ...payload().superset, reachable: false } }));
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-superset-offline")).toBeInTheDocument();
    expect(screen.getByTestId("bi-plan-line")).toBeInTheDocument();
  });

  it("check again re-probes Superset", async () => {
    api.biDashboard
      .mockResolvedValueOnce(payload({ superset: { ...payload().superset, reachable: false } }))
      .mockResolvedValue(payload());
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.click(await screen.findByTestId("bi-recheck-btn"));
    await waitFor(() => expect(screen.queryByTestId("bi-superset-offline")).toBeNull());
  });

  it("shows empty states instead of crashing on an empty mart", async () => {
    api.biDashboard.mockResolvedValue(payload({
      cycle: {}, by_state: [], exceptions_by_channel: [], var_by_risk_type: [],
      plan_vs_baseline: [], top_exceptions: [], overrides_by_reason: [], fva_by_planner: [],
    }));
    render(<BiPage role="planner" />);
    expect(await screen.findByText("No exceptions in this cycle.")).toBeInTheDocument();
    expect(screen.getAllByText("No data").length).toBeGreaterThan(0);
  });

  it("renders the KPI driver tree with owners and health states", async () => {
    render(<BiPage role="planner" />);
    const tree = await screen.findByTestId("bi-kpi-tree");
    expect(within(tree).getByTestId("bi-kpi-root")).toHaveTextContent("Autopilot coverage");
    expect(within(tree).getByTestId("bi-kpi-root")).toHaveTextContent("84.0%");
    expect(within(tree).getByTestId("bi-kpi-root")).toHaveTextContent("Head of Supply Chain");
    expect(within(tree).getByTestId("bi-kpi-branch-risk")).toHaveTextContent("Breach");
    expect(within(tree).getByTestId("bi-kpi-leaf-oos_exposure")).toHaveTextContent("£635,237");
    expect(within(tree).getByTestId("bi-kpi-leaf-ml_coverage")).toHaveTextContent("55.9%");
  });

  it("renders the governed metric catalog as definitions, not a second scorecard", async () => {
    render(<BiPage role="planner" />);
    const table = await screen.findByTestId("bi-metrics-table");
    expect(within(table).getByText("autopilot_share")).toBeInTheDocument();
    expect(within(table).getByText("Head of Supply Chain")).toBeInTheDocument();
    expect(within(table).getByText("cycle")).toBeInTheDocument();
    expect(within(table).getByText("ratio")).toBeInTheDocument();
    expect(within(table).getAllByText("1.0.0").length).toBeGreaterThan(0);
    expect(within(table).queryByText("84.0%")).toBeNull();
    expect(within(table).queryByText("£635,237")).toBeNull();
  });

  it("asks a grounded question and renders the resolved metrics", async () => {
    api.biAsk.mockResolvedValue({
      question: "How much OOS risk?",
      grounded: true,
      metrics: [{ id: "oos_exposure_gbp", label: "OOS exposure", unit: "gbp", owner: "Inventory Manager", value: 635237 }],
      rejected: ["made_up_metric"],
      catalog_size: 18,
    });
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.type(await screen.findByTestId("bi-ask-input"), "How much OOS risk?");
    await user.click(screen.getByTestId("bi-ask-submit"));
    const result = await screen.findByTestId("bi-ask-result");
    expect(result).toHaveTextContent("OOS exposure");
    expect(result).toHaveTextContent("£635,237");
    expect(result).toHaveTextContent("made_up_metric");
    expect(api.biAsk).toHaveBeenCalledWith("How much OOS risk?");
  });

  it("surfaces a grounded-ask failure", async () => {
    api.biAsk.mockRejectedValue(new Error("No LLM provider is configured."));
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.type(await screen.findByTestId("bi-ask-input"), "anything");
    await user.click(screen.getByTestId("bi-ask-submit"));
    expect(await screen.findByTestId("bi-ask-error")).toHaveTextContent("No LLM provider");
  });

  it("renders anomaly diagnostics with the four RCA checks", async () => {
    render(<BiPage role="planner" />);
    const panel = await screen.findByTestId("bi-diagnostics-panel");
    expect(within(panel).getByTestId("bi-diagnostics-summary")).toHaveTextContent("1 anomalies across 1 series");
    expect(within(panel).getByTestId("bi-rca-status-plan_qty-2026-09-01")).toHaveTextContent("Actionable");
    const checks = within(panel).getByTestId("bi-rca-checks-plan_qty-2026-09-01");
    for (const id of ["temporal_precedence", "arithmetic_reconciliation", "permission_preserved", "named_approver"]) {
      expect(within(checks).getByTestId(`bi-rca-check-${id}`)).toHaveTextContent("pass");
    }
    expect(within(panel).getByText("PROD_2742")).toBeInTheDocument();
    expect(within(panel).getByTestId("bi-diagnostics-forecast-plan_qty")).toHaveTextContent("Mar 27");
  });

  it("downgrades a cause to a hypothesis when a check fails", async () => {
    api.biDiagnostics.mockResolvedValue(hypothesisDiagnostics());
    render(<BiPage role="auditor" />);
    const panel = await screen.findByTestId("bi-diagnostics-panel");
    expect(within(panel).getByTestId("bi-rca-status-plan_qty-2026-09-01")).toHaveTextContent("Hypothesis");
    expect(within(panel).getByTestId("bi-rca-check-permission_preserved")).toHaveTextContent("fail");
    expect(within(panel).getByTestId("bi-rca-withheld-plan_qty-2026-09-01")).toHaveTextContent("withheld");
    expect(within(panel).queryByText("PROD_2742")).toBeNull();
  });

  it("proposes an action from an actionable cause", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.click(await screen.findByTestId("bi-rca-propose-plan_qty-2026-09-01"));
    await waitFor(() => expect(api.biProposeAction).toHaveBeenCalledTimes(1));
    const body = api.biProposeAction.mock.calls[0][0];
    expect(body.series).toBe("plan_qty");
    expect(body.month).toBe("2026-09-01");
    expect(body.idempotency_key).toBeTruthy();
    expect(await screen.findByTestId("bi-writeback-notice")).toHaveTextContent("Head of Demand Planning");
  });

  it("approves a pending action with its expected state", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.click(await screen.findByTestId("bi-action-approve-act1"));
    await waitFor(() => expect(api.biDecideAction).toHaveBeenCalledTimes(1));
    const [id, body] = api.biDecideAction.mock.calls[0];
    expect(id).toBe("act1");
    expect(body.decision).toBe("approved");
    expect(body.expected_state).toBe("pending");
    expect(body.idempotency_key).toBeTruthy();
  });

  it("annotates an exception", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.type(await screen.findByTestId("bi-annotate-target-id"), "100001");
    await user.type(screen.getByTestId("bi-annotate-body"), "Supply constrained");
    await user.click(screen.getByTestId("bi-annotate-submit"));
    await waitFor(() => expect(api.biAnnotate).toHaveBeenCalledTimes(1));
    const body = api.biAnnotate.mock.calls[0][0];
    expect(body).toMatchObject({ target_type: "item", target_id: "100001", body: "Supply constrained" });
    expect(body.idempotency_key).toBeTruthy();
  });

  it("gives a read-only auditor no write controls", async () => {
    render(<BiPage role="auditor" />);
    await screen.findByTestId("bi-actions-panel");
    expect(screen.queryByTestId("bi-rca-propose-plan_qty-2026-09-01")).toBeNull();
    expect(screen.queryByTestId("bi-action-approve-act1")).toBeNull();
    expect(screen.queryByTestId("bi-annotate-form")).toBeNull();
    expect(screen.getByTestId("bi-actions-list")).toHaveTextContent("only a planner or admin can decide");
  });

  it("surfaces a refused proposal", async () => {
    api.biProposeAction.mockRejectedValue(new Error("Cause is a hypothesis, not an actionable finding."));
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await user.click(await screen.findByTestId("bi-rca-propose-plan_qty-2026-09-01"));
    expect(await screen.findByTestId("bi-writeback-error")).toHaveTextContent("hypothesis");
  });

  it("shows the mart contract conformance", async () => {
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-contract-badge"))
      .toHaveTextContent("Contract: 12/12 tables conform");
  });

  it("flags contract drift", async () => {
    api.biContracts.mockResolvedValue(contractFixture({
      conformance: {
        ok: false, tables: [],
        summary: { tables: 12, present: 11, errors: 1, warnings: 0 },
        problems: [{
          table: "triage_items", kind: "missing_column", severity: "error", column: "p70_gap_pct",
          detail: "triage_items.p70_gap_pct is declared but absent; consumers that would break: KPI cards",
        }],
      },
    }));
    render(<BiPage role="planner" />);
    const badge = await screen.findByTestId("bi-contract-badge");
    expect(badge).toHaveTextContent("Contract drift: 1 error(s)");
    expect(badge).toHaveTextContent("p70_gap_pct");
  });

  it("shows freshness, cycle cutoff and latency badges", async () => {
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-freshness-badge")).toHaveTextContent(/Mart built/);
    expect(screen.getByTestId("bi-cycle-badge")).toHaveTextContent("Cycle cutoff Feb 26");
    expect(screen.getByTestId("bi-latency-badge")).toHaveTextContent("42 ms");
  });

  it("segments the dashboard by persona", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-kpis");
    expect(screen.getByTestId("bi-mart-panel")).toBeInTheDocument();

    await user.click(screen.getByTestId("bi-persona-executive"));
    expect(screen.getByTestId("bi-persona-executive")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("bi-triage-donut")).toBeInTheDocument();
    expect(screen.queryByTestId("bi-mart-panel")).toBeNull();
    expect(screen.queryByTestId("bi-top-table")).toBeNull();

    await user.click(screen.getByTestId("bi-persona-operations"));
    expect(screen.getByTestId("bi-top-table")).toBeInTheDocument();
    expect(screen.queryByTestId("bi-triage-donut")).toBeNull();
    expect(screen.queryByTestId("bi-plan-line")).toBeNull();
  });

  it("attributes compute to the owning team", async () => {
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-finops-panel")).toBeInTheDocument();
    expect(screen.getByTestId("bi-finops-metric-queries")).toHaveTextContent("12");
    const teams = screen.getByTestId("bi-finops-teams");
    expect(within(teams).getByText("Demand Planning")).toBeInTheDocument();
    expect(within(teams).getByText("31")).toBeInTheDocument();
  });

  it("does not claim a spend tier is active without a cost model", async () => {
    render(<BiPage role="planner" />);
    const note = await screen.findByTestId("bi-finops-thresholds-note");
    expect(note).toHaveTextContent("No tier is active");
    expect(note).toHaveTextContent("No cloud warehouse is configured");
    expect(screen.getByTestId("bi-finops-tiers")).toHaveTextContent("$15,000");
  });

  it("lists the metrics and surfaces nobody used", async () => {
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-finops-sprawl"))
      .toHaveTextContent("3 of 18 metrics were evaluated");
    expect(screen.getByTestId("bi-finops-prune-candidates"))
      .toHaveTextContent("fallback_share, routed_share");
    expect(screen.getByTestId("bi-finops-sprawl")).toHaveTextContent("dashboard, ask");
  });

  it("keeps the FinOps panel off the executive and operations personas", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-finops-panel");
    await user.click(screen.getByTestId("bi-persona-executive"));
    expect(screen.queryByTestId("bi-finops-panel")).toBeNull();
    await user.click(screen.getByTestId("bi-persona-operations"));
    expect(screen.queryByTestId("bi-finops-panel")).toBeNull();
  });

  it("measures the mart against its freshness and latency SLAs", async () => {
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-serving-panel")).toBeInTheDocument();
    expect(screen.getByTestId("bi-serving-age")).toHaveTextContent("30 min");
    expect(screen.getByTestId("bi-serving-freshness")).toHaveTextContent("within SLA");
    expect(screen.getByTestId("bi-serving-indexes")).toHaveTextContent("3/3");
    const latency = screen.getByTestId("bi-serving-latency-table");
    expect(within(latency).getByText("dashboard")).toBeInTheDocument();
    expect(within(latency).getByText("unmeasured")).toBeInTheDocument();
  });

  it("flags a stale table and a latency breach", async () => {
    api.biServing.mockResolvedValue(servingFixture({
      freshness: {
        generated_at: "2026-11-16T16:00:00Z", age_s: 7200, ok: false, breaches: ["overrides"],
        tiers: { hourly: 3600, daily: 86400 },
        tables: [{ table: "overrides", tier: "hourly", sla_s: 3600, present: true, age_s: 7200, breach: true }],
      },
      latency: {
        window_days: 30, breaches: ["dashboard"],
        workloads: [{
          workload: "dashboard", persona: "Executive / Operations",
          target_p95_ms: 400, measured_p95_ms: 900, samples: 5, ok: false,
        }],
      },
    }));
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-serving-freshness")).toHaveTextContent("breach");
    expect(screen.getByTestId("bi-serving-latency-breaches")).toHaveTextContent("1");
    expect(screen.getByTestId("bi-serving-freshness-table")).toHaveTextContent("stale");
  });

  it("reports the indexes the mart is missing", async () => {
    api.biServing.mockResolvedValue(servingFixture({
      indexes: { declared: [{}, {}], present: [], missing: ["ix_triage_items_in_scope"], ok: false },
    }));
    render(<BiPage role="planner" />);
    expect(await screen.findByTestId("bi-serving-missing-indexes"))
      .toHaveTextContent("ix_triage_items_in_scope");
  });

  it("measures the transport only when asked", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-serving-panel");
    expect(screen.getByTestId("bi-transport-hint")).toBeInTheDocument();
    expect(api.biTransport).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("bi-transport-measure"));
    expect(await screen.findByTestId("bi-transport-table")).toBeInTheDocument();
    expect(api.biTransport).toHaveBeenCalled();
    expect(screen.getByTestId("bi-transport-totals")).toHaveTextContent("45%");
    expect(screen.getByTestId("bi-download-parquet-triage_items")).toBeInTheDocument();
  });

  it("keeps the serving panel off the executive and operations personas", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-serving-panel");
    await user.click(screen.getByTestId("bi-persona-executive"));
    expect(screen.queryByTestId("bi-serving-panel")).toBeNull();
    await user.click(screen.getByTestId("bi-persona-operations"));
    expect(screen.queryByTestId("bi-serving-panel")).toBeNull();
  });

  it("puts the visualisations before the detail and governance surfaces", async () => {
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-kpis");
    const page = screen.getByTestId("bi-page");
    const order = [
      "bi-triage-donut", "bi-channel-bars", "bi-risk-bars", "bi-overrides-bars", "bi-plan-line",
      "bi-kpi-tree-panel", "bi-diagnostics-panel", "bi-top-table", "bi-fva-table",
      "bi-metrics-panel", "bi-mart-panel",
    ];
    const nodes = order.map((id) => within(page).getByTestId(id));
    for (let i = 1; i < nodes.length; i += 1) {
      const relation = nodes[i - 1].compareDocumentPosition(nodes[i]);
      expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("does not repeat the triage counts in the scorecard", async () => {
    render(<BiPage role="planner" />);
    const kpis = await screen.findByTestId("bi-kpis");
    expect(within(kpis).queryByText("Exceptions")).toBeNull();
    expect(within(kpis).queryByText("Planner-led")).toBeNull();
    expect(within(kpis).queryByText("In-scope SKUs")).toBeNull();
    expect(within(screen.getByTestId("bi-triage-donut")).getAllByText("Exceptions").length)
      .toBeGreaterThan(0);
  });

  it("refreshes from the header", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-kpis");
    await user.click(screen.getByTestId("bi-refresh-btn"));
    await waitFor(() => expect(api.biDashboard).toHaveBeenCalledTimes(2));
  });

  it("summarises the payload in an insight strip", async () => {
    render(<BiPage role="planner" />);
    const strip = await screen.findByTestId("bi-insights");
    expect(within(strip).getByTestId("bi-insight-channel"))
      .toHaveTextContent("AMAZON holds 54% of the 74 exceptions in review.");
    expect(within(strip).getByTestId("bi-insight-risk"))
      .toHaveTextContent("OOS exposure is 60% of the £20,000 value at risk.");
    expect(within(strip).getByTestId("bi-insight-plan"))
      .toHaveTextContent("Plan runs 13.5% above baseline across 2 months");
    expect(within(strip).getByTestId("bi-insight-exposure"))
      .toHaveTextContent("Largest single exposure: 100001 on D2C at £1,200.");
  });

  it("does not claim a filtered scope before a chart is selected", async () => {
    render(<BiPage role="planner" />);
    await screen.findByTestId("bi-insights");
    expect(screen.queryByTestId("bi-insight-scope")).toBeNull();
    expect(screen.getByTestId("bi-filter-hint")).toBeInTheDocument();
    expect(api.biDashboardExceptions).not.toHaveBeenCalled();
  });

  it("filters the risk mix and the exception list from a channel bar", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    const bars = await screen.findByTestId("bi-channel-bars");
    await user.click(within(bars).getByRole("button", { name: "Filter by channel D2C" }));

    await waitFor(() => expect(api.biDashboardExceptions)
      .toHaveBeenCalledWith({ channel: "D2C", risk_type: "" }));
    expect(await screen.findByTestId("bi-filter-channel")).toHaveTextContent("Channel: D2C");
    expect(screen.getByTestId("bi-insight-scope")).toHaveTextContent("D2C: 20 exceptions");
    expect(within(screen.getByTestId("bi-risk-bars")).getAllByRole("button")).toHaveLength(1);
    expect(within(screen.getByTestId("bi-top-table")).getByText("PROD_9001")).toBeInTheDocument();
    expect(within(screen.getByTestId("bi-top-table")).queryByText("PROD_2742")).toBeNull();
  });

  it("keeps every channel visible while one is selected", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    const bars = await screen.findByTestId("bi-channel-bars");
    await user.click(within(bars).getByRole("button", { name: "Filter by channel D2C" }));
    await screen.findByTestId("bi-filter-channel");
    expect(within(bars).getByRole("button", { name: "Filter by channel AMAZON" })).toBeInTheDocument();
    expect(within(bars).getByRole("button", { name: "Filter by channel B2B" })).toBeInTheDocument();
  });

  it("filters the channel mix from a risk row", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    const rows = await screen.findByTestId("bi-risk-bars");
    await user.click(within(rows).getByRole("button", { name: "Filter by risk type OOS exposure" }));
    await waitFor(() => expect(api.biDashboardExceptions)
      .toHaveBeenCalledWith({ channel: "", risk_type: "OOS_RISK" }));
    expect(await screen.findByTestId("bi-filter-risk")).toHaveTextContent("Risk: OOS exposure");
  });

  it("clears a filter when the selected bar is clicked again", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    const bars = await screen.findByTestId("bi-channel-bars");
    await user.click(within(bars).getByRole("button", { name: "Filter by channel D2C" }));
    await screen.findByTestId("bi-filter-channel");
    await user.click(within(bars).getByRole("button", { name: "Filter by channel D2C" }));
    await waitFor(() => expect(screen.queryByTestId("bi-filter-channel")).toBeNull());
    expect(screen.getByTestId("bi-filter-hint")).toBeInTheDocument();
  });

  it("clears every filter from the filter bar", async () => {
    const user = userEvent.setup();
    render(<BiPage role="planner" />);
    const bars = await screen.findByTestId("bi-channel-bars");
    await user.click(within(bars).getByRole("button", { name: "Filter by channel D2C" }));
    await screen.findByTestId("bi-filter-clear");
    await user.click(screen.getByTestId("bi-filter-clear"));
    await waitFor(() => expect(screen.queryByTestId("bi-filter-channel")).toBeNull());
    expect(screen.queryByTestId("bi-insight-scope")).toBeNull();
    expect(within(screen.getByTestId("bi-top-table")).getByText("PROD_2742")).toBeInTheDocument();
  });

  it("discloses that the exception list is a top-10 view", async () => {
    render(<BiPage role="planner" />);
    const panel = await screen.findByTestId("bi-top-panel");
    expect(within(panel).getByText(/10 largest absolute exposures/)).toBeInTheDocument();
    expect(within(panel).getByText(/full exception population is not listed here/)).toBeInTheDocument();
  });
});
