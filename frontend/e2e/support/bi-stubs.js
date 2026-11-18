// Deterministic fixtures and network stubs for the BI visual regression suite.
//
// The suite never talks to the backend. Every request the app makes is answered
// from this file via page.route(), so a screenshot depends only on the code under
// test and not on whatever happens to be in var/bi_mart.db on the machine running
// it. A request with no stub is recorded and fails the test rather than quietly
// rendering an empty panel.

export const FROZEN_NOW = "2026-11-16T09:00:00Z";
const MART_BUILT_AT = "2026-11-16T07:00:00Z";

const USERS = {
  planner: { username: "danny", role: "planner" },
  admin: { username: "admin", role: "admin" },
  auditor: { username: "auditor", role: "auditor" },
};

export const summary = () => ({
  cutoff: "2026-02-01",
  first_month: "2026-03-01",
  committed: false,
  pending_decisions: 74,
  quality_warnings: [],
});

export const dashboard = () => ({
  cycle: {
    cutoff: "2026-02-01",
    first_month: "2026-03-01",
    generated_at: MART_BUILT_AT,
    total_catalog_skus: 9162,
    in_scope_skus: 5499,
    autopilot_approved_skus: 4617,
    exceptions_requiring_review: 74,
    planner_led_skus: 808,
    planned_value_gbp: 1234567,
    baseline_value_gbp: 1200000,
    net_value_variance_gbp: 34567,
    quality_warnings: 1,
  },
  by_state: [
    { state: "AUTO_APPROVED", count: 4617 },
    { state: "EXCEPTION_REVIEW", count: 74 },
    { state: "PLANNER_LED", count: 808 },
    { state: "INACTIVE", count: 3663 },
  ],
  exceptions_by_channel: [
    { channel: "AMAZON", count: 40 },
    { channel: "D2C", count: 20 },
    { channel: "B2B", count: 14 },
  ],
  var_by_risk_type: [
    { risk_type: "OOS_RISK", var_gbp: 12000 },
    { risk_type: "OVERSTOCK_RISK", var_gbp: 8000 },
  ],
  plan_vs_baseline: [
    { month: "2026-03-01", plan: 100, baseline: 90, ml: 98 },
    { month: "2026-04-01", plan: 110, baseline: 95, ml: 105 },
    { month: "2026-05-01", plan: 118, baseline: 99, ml: 112 },
  ],
  top_exceptions: [
    { item_id: 100001, product_id: "PROD_2742", channel: "D2C", risk_type: "OOS_RISK", var_gbp: 1200, value_delta_gbp: -300 },
    { item_id: 100002, product_id: "PROD_1180", channel: "AMAZON", risk_type: "OVERSTOCK_RISK", var_gbp: 940, value_delta_gbp: 210 },
  ],
  totals: { exceptions: 74, var_gbp: 20000 },
  overrides_by_reason: [
    { reason_code: "Promotion", count: 10 },
    { reason_code: "Supply constraint", count: 6 },
  ],
  fva_by_planner: [
    { planner_id: "danny", n_overrides: 22, n_accepted: 18, volume: 5000, fpe_ml: 0.31, fpe_planner: 0.28, fpe_baseline: 0.35, fva_pts: 1.2 },
  ],
  quality_warnings: [{ channel: "AMAZON", month: "2026-02-01", imputed_share: 1.0, items: 3080 }],
  mart: {
    path: "D:/Case_study_2/var/bi_mart.db",
    exists: true,
    generated_at: MART_BUILT_AT,
    tables: {
      triage_items: 9162,
      triage_monthly: 109944,
      quantile_bands: 109944,
      phasing_items: 1455,
      safety_stock: 5499,
      overrides: 22,
      commits: 0,
      fva_by_planner: 1,
      fva_by_reason: 4,
      cycle_summary: 1,
      quality_warnings: 1,
      mart_meta: 1,
    },
  },
  superset: {
    url: "http://localhost:8088",
    dashboard_url: "http://localhost:8088/superset/dashboard/sop-overview/",
    slug: "sop-overview",
    reachable: true,
  },
  duration_ms: 42,
  cached: false,
  freshness: {
    mart_generated_at: MART_BUILT_AT,
    cycle_cutoff: "2026-02-01",
    first_month: "2026-03-01",
  },
});

export const dashboardExceptions = () => ({
  exceptions_by_channel: [
    { channel: "AMAZON", count: 40 },
    { channel: "D2C", count: 20 },
    { channel: "B2B", count: 14 },
  ],
  var_by_risk_type: [{ risk_type: "OOS_RISK", var_gbp: 12000 }],
  top_exceptions: [
    { item_id: 100001, product_id: "PROD_2742", channel: "D2C", risk_type: "OOS_RISK", var_gbp: 1200, value_delta_gbp: -300 },
  ],
  totals: { exceptions: 20, var_gbp: 12000 },
  filter: { channel: "D2C", risk_type: null },
});

export const kpiTree = () => ({
  root: {
    id: "autopilot",
    label: "Autopilot coverage",
    owner: "Head of Supply Chain",
    confidence: "high",
    signal: "outcome",
    unit: "share",
    corridor: { min: 0.7 },
    value: 0.84,
    health: "healthy",
    branches: [
      {
        id: "accuracy",
        label: "Forecast accuracy",
        owner: "Demand Planning Lead",
        confidence: "high",
        signal: "output",
        unit: "share",
        corridor: { min: 0.5 },
        value: 0.559,
        health: "healthy",
        leaves: [
          { id: "ml_coverage", label: "ML-chosen items", owner: "Demand Planning Lead", confidence: "high", signal: "output", unit: "share", corridor: { min: 0.5 }, value: 0.559, health: "healthy" },
          { id: "fallback_share", label: "Median fallbacks", owner: "Demand Planning Lead", confidence: "high", signal: "output", unit: "share", corridor: { max: 0.4 }, value: 0.342, health: "healthy" },
        ],
      },
      {
        id: "risk",
        label: "Inventory risk",
        owner: "Inventory Manager",
        confidence: "high",
        signal: "outcome",
        unit: "gbp",
        corridor: { max: 1250000 },
        value: 1664271,
        health: "breach",
        leaves: [
          { id: "oos_exposure", label: "OOS exposure", owner: "Inventory Manager", confidence: "high", signal: "outcome", unit: "gbp", corridor: { max: 500000 }, value: 635237, health: "breach" },
          { id: "overstock_exposure", label: "Overstock exposure", owner: "Inventory Manager", confidence: "medium", signal: "outcome", unit: "gbp", corridor: { max: 750000 }, value: 1029034, health: "watch" },
        ],
      },
    ],
  },
  counts: { root: 1, branches: 2, leaves: 4, total: 7 },
});

export const metricCatalog = () => ({
  metrics: [
    {
      id: "autopilot_share", label: "Autopilot coverage", owner: "Head of Supply Chain",
      unit: "share", table: "cycle_summary", grain: "cycle", aggregation: "ratio",
      version: "1.0.0", value: 0.84,
    },
    {
      id: "oos_exposure_gbp", label: "OOS exposure", owner: "Inventory Manager",
      unit: "gbp", table: "triage_items", grain: "item", aggregation: "sum",
      version: "1.0.0", value: 635237,
    },
    {
      id: "planned_value_gbp", label: "Planned value", owner: "Head of Demand Planning",
      unit: "gbp", table: "triage_monthly", grain: "item_month", aggregation: "sum",
      version: "1.0.0", value: 1234567,
    },
  ],
});

export const diagnostics = () => ({
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
      points: [
        { month: "2026-08-01", value: 319536.6 },
        { month: "2026-09-01", value: 423962.4 },
      ],
      anomalies: [
        { month: "2026-09-01", value: 423962.4, expected: 171329.9, deviation: 252632.5, z: 6.48, direction: "above", severity: "medium" },
      ],
      forecast: {
        method: "linear_trend_last_6_months", band: 1000,
        points: [{ month: "2027-03-01", value: 150000, low: 149000, high: 151000 }],
        caveat: "Naive trend baseline, not a seasonal model.",
      },
      rca: [
        {
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
        },
      ],
    },
  ],
  summary: { series: 1, anomalies: 1, actionable: 1, hypotheses: 0 },
});

export const contractReport = (over = {}) => ({
  contract: { tables: [], summary: { tables: 12, columns: 144 } },
  conformance: {
    ok: true,
    tables: [],
    problems: [],
    summary: { tables: 12, present: 12, errors: 0, warnings: 0 },
  },
  mart: "var/bi_mart.db",
  mart_present: true,
  ...over,
});

export const contractDrift = () =>
  contractReport({
    conformance: {
      ok: false,
      tables: [],
      problems: [
        {
          kind: "missing_column",
          severity: "error",
          table: "triage_items",
          column: "var_gbp",
          detail: "triage_items is missing column var_gbp; consumers: Executive KPI cards, Value at risk by risk type.",
        },
      ],
      summary: { tables: 12, present: 12, errors: 1, warnings: 0 },
    },
  });

export const actions = () => ({
  actions: [
    {
      action_id: "act1", event: "proposed", state: "pending",
      cutoff: "2026-02-01", series: "plan_qty", month: "2026-09-01", driver: "amazon",
      approver: "Head of Demand Planning", note: "Investigate the amazon contribution.",
      actor: "danny", role: "planner",
      history: [{ event: "proposed", actor: "danny", role: "planner", note: "Investigate.", timestamp: "2026-11-15T20:00:00Z" }],
    },
  ],
});

export const annotations = () => ({
  annotations: [
    {
      id: 1, cutoff: "2026-02-01", target_type: "item", target_id: "100001",
      body: "Supply constrained", author: "danny", role: "planner",
    },
  ],
});

export const askResult = () => ({
  question: "How much value is exposed to out-of-stock risk?",
  metrics: [
    { id: "oos_exposure_gbp", label: "OOS exposure", unit: "gbp", value: 635237 },
  ],
  rejected: [],
});

export const finopsReport = () => ({
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
});

export const servingReport = () => ({
  freshness: {
    generated_at: "2026-11-16T18:00:00Z", age_s: 1800, ok: true, breaches: [],
    tiers: { realtime: 60, hourly: 3600, daily: 86400, weekly: 604800 },
    tables: [
      { table: "triage_items", tier: "daily", sla_s: 86400, present: true, age_s: 1800, breach: false },
      { table: "overrides", tier: "hourly", sla_s: 3600, present: true, age_s: 1800, breach: false },
      { table: "commits", tier: "hourly", sla_s: 3600, present: true, age_s: 1800, breach: false },
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
});

export const transportReport = () => ({
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
});

const ROUTES = {
  "/api/v1/summary": summary,
  "/api/v1/reason-codes": () => ({ reason_codes: [] }),
  "/api/v1/exceptions": () => ({ items: [], total: 0 }),
  "/api/v1/notifications": () => ({ unread: 0, items: [] }),
  "/api/v1/bi/dashboard": dashboard,
  "/api/v1/bi/dashboard/exceptions": dashboardExceptions,
  "/api/v1/bi/kpi-tree": kpiTree,
  "/api/v1/bi/metrics": metricCatalog,
  "/api/v1/bi/diagnostics": diagnostics,
  "/api/v1/bi/contracts": () => contractReport(),
  "/api/v1/bi/finops": () => finopsReport(),
  "/api/v1/bi/serving": () => servingReport(),
  "/api/v1/bi/transport": () => transportReport(),
  "/api/v1/bi/actions": actions,
  "/api/v1/bi/annotations": annotations,
  "/api/v1/bi/ask": askResult,
};

/**
 * Freeze the clock and seed an authenticated session before the app boots.
 * The token is never validated because every request is stubbed.
 */
export async function seedSession(page, role = "planner") {
  const user = USERS[role];
  if (!user) throw new Error(`Unknown role: ${role}`);
  await page.clock.setFixedTime(new Date(FROZEN_NOW));
  await page.addInitScript(
    ([token, stored]) => {
      sessionStorage.setItem("sop_token", token);
      sessionStorage.setItem("sop_user", stored);
    },
    ["e2e-token", JSON.stringify(user)],
  );
}

/**
 * Answer every API call from the fixtures above.
 * `overrides` maps a pathname to a replacement payload (or a function returning one).
 * Returns an array that fills with any pathname that had no stub.
 */
export async function stubApi(page, overrides = {}) {
  const unmatched = [];
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    const handler = overrides[pathname] ?? ROUTES[pathname];
    if (!handler) {
      unmatched.push(pathname);
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: `No visual-regression stub for ${pathname}` }),
      });
    }
    const body = typeof handler === "function" ? handler() : handler;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return unmatched;
}

/**
 * Boot the app, navigate to the BI dashboard and wait for the panels to settle.
 * Fails loudly if the app requested an endpoint the suite does not stub.
 */
export async function openBiPage(page, { role = "planner", overrides = {} } = {}) {
  const unmatched = await stubApi(page, overrides);
  await seedSession(page, role);
  await page.goto("/");
  await page.getByTestId("nav-bi").click();
  await page.getByTestId("bi-page").waitFor();
  await page.getByTestId("bi-kpis").waitFor();
  if (unmatched.length) {
    throw new Error(`Unstubbed API calls: ${[...new Set(unmatched)].join(", ")}`);
  }
  return unmatched;
}

/**
 * Volatile elements masked out of every capture. The clock is frozen and the
 * payloads are fixed, so these are belt-and-braces: they are the only strings
 * that depend on the host's ICU data rather than on the code under test.
 */
export const VOLATILE = ["bi-mart-generated"];
