import React, { useCallback, useEffect, useState } from "react";
import { BarChart3, Clock, Database, ExternalLink, RefreshCw } from "lucide-react";
import { api } from "../services/api";
import { fmtGbp, fmtGbpPlain, fmtNum, monthLabel, RISK_LABELS } from "../lib/format";
import { Panel, Stat, DataTable, ErrorNote } from "../components/ui";
import { BarChart, DonutChart, HBarList, LineChart } from "../components/charts";
import KpiTree, { formatValue } from "../components/KpiTree";
import Diagnostics from "../components/Diagnostics";
import { ActionQueue, AnnotationForm } from "../components/WriteBack";
import FinOps from "../components/FinOps";
import Serving from "../components/Serving";
import { IBCS } from "../lib/ibcs";

const STATE_LABELS = {
  AUTO_APPROVED: "Autopilot",
  EXCEPTION_REVIEW: "Exceptions",
  PLANNER_LED: "Planner-led",
  INACTIVE: "Inactive",
};

const PERSONAS = [
  { id: "executive", label: "Executive", hint: "Outcome KPIs and the plan, on a quarterly horizon." },
  { id: "operations", label: "Operations", hint: "Where the exceptions sit and what is being done about them." },
  { id: "analyst", label: "Analyst", hint: "Every chart, the driver tree, and the governance surfaces behind them." },
];

const LAYOUT = {
  executive: [
    { id: "performance", title: "Performance", hint: "Composition and plan", panels: [{ key: "triage" }, { key: "plan" }] },
    { id: "drivers", title: "Drivers", hint: "Root to leaf", panels: [{ key: "tree", span: 2 }] },
    { id: "detail", title: "Detail", panels: [{ key: "fva", span: 2 }] },
  ],
  operations: [
    { id: "performance", title: "Performance", hint: "Where the exceptions sit", panels: [{ key: "channel" }, { key: "risk" }, { key: "overrides", span: 2 }] },
    { id: "diagnostics", title: "Diagnostics", hint: "Anomalies and the RCA gate", panels: [{ key: "diagnostics", span: 2 }] },
    { id: "actions", title: "Actions", hint: "Propose, decide, annotate", panels: [{ key: "actions" }, { key: "annotate" }] },
    { id: "detail", title: "Detail", panels: [{ key: "top", span: 2 }] },
  ],
  analyst: [
    { id: "performance", title: "Performance", hint: "Composition, exposure and plan", panels: [{ key: "triage" }, { key: "channel" }, { key: "risk" }, { key: "overrides" }, { key: "plan", span: 2 }] },
    { id: "drivers", title: "Drivers", hint: "Root to leaf", panels: [{ key: "tree", span: 2 }] },
    { id: "diagnostics", title: "Diagnostics", hint: "Anomalies and the RCA gate", panels: [{ key: "diagnostics", span: 2 }] },
    { id: "actions", title: "Actions", hint: "Propose, decide, annotate", panels: [{ key: "actions" }, { key: "annotate" }] },
    { id: "detail", title: "Detail", hint: "Item-level and planner-level", panels: [{ key: "top", span: 2 }, { key: "fva", span: 2 }] },
    { id: "governance", title: "Governance", hint: "Definitions, cost, serving and the mart", panels: [{ key: "metrics", span: 2 }, { key: "ask", span: 2 }, { key: "finops", span: 2 }, { key: "serving", span: 2 }, { key: "mart" }, { key: "superset" }] },
  ],
};

function Section({ title, hint, children }) {
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-[11px] font-bold uppercase tracking-[0.16em] text-slate-400">{title}</h2>
        <span className="h-px flex-1 bg-slate-800" aria-hidden="true" />
        {hint && <span className="text-[10px] text-slate-500">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function KpiCard({ label, value, sub, tone = "text-white" }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 px-5 py-4">
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">{label}</div>
      <div className={`mt-2 text-2xl font-bold font-mono leading-none ${tone}`}>{value}</div>
      {sub && <div className="mt-2 text-[11px] text-slate-400">{sub}</div>}
    </div>
  );
}

const riskLabel = (v) => RISK_LABELS[v] || v || "Unclassified";

const ageLabel = (iso) => {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
};

const newKey = () =>
  globalThis.crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random().toString(16).slice(2)}`;

export default function BiPage({ role }) {
  const [data, setData] = useState(null);
  const [kpiTree, setKpiTree] = useState(null);
  const [metrics, setMetrics] = useState([]);
  const [diagnostics, setDiagnostics] = useState(null);
  const [contracts, setContracts] = useState(null);
  const [finops, setFinops] = useState(null);
  const [serving, setServing] = useState(null);
  const [transport, setTransport] = useState(null);
  const [transportBusy, setTransportBusy] = useState(false);
  const [actions, setActions] = useState([]);
  const [annotations, setAnnotations] = useState([]);
  const [wbBusy, setWbBusy] = useState(false);
  const [wbError, setWbError] = useState(null);
  const [wbNotice, setWbNotice] = useState(null);
  const [askQuestion, setAskQuestion] = useState("");
  const [askResult, setAskResult] = useState(null);
  const [askError, setAskError] = useState(null);
  const [askBusy, setAskBusy] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [persona, setPersona] = useState("analyst");
  const isAdmin = role === "admin";
  const canWrite = role === "planner" || role === "admin";

  const load = useCallback(async () => {
    try {
      const [dashboard, tree, catalog, diag, contract, acts, notes, cost, sla] = await Promise.all([
        api.biDashboard(),
        api.biKpiTree().catch(() => null),
        api.biMetrics().catch(() => ({ metrics: [] })),
        api.biDiagnostics().catch(() => null),
        api.biContracts().catch(() => null),
        api.biActions().catch(() => ({ actions: [] })),
        api.biAnnotations({ limit: 20 }).catch(() => ({ annotations: [] })),
        api.biFinops().catch(() => null),
        api.biServing().catch(() => null),
      ]);
      setData(dashboard);
      setKpiTree(tree);
      setMetrics((catalog && catalog.metrics) || []);
      setDiagnostics(diag);
      setContracts(contract);
      setActions((acts && acts.actions) || []);
      setAnnotations((notes && notes.annotations) || []);
      setFinops(cost);
      setServing(sla);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const measureTransport = async () => {
    setTransportBusy(true);
    try {
      setTransport(await api.biTransport());
    } catch (e) {
      setError(e.message);
    } finally {
      setTransportBusy(false);
    }
  };

  const downloadColumnar = async (table, format) => {
    try {
      const blob = await api.biExportColumnar(table, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${table}.${format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e.message);
    }
  };

  const ask = async (event) => {
    event.preventDefault();
    const question = askQuestion.trim();
    if (!question) return;
    setAskBusy(true);
    setAskError(null);
    try {
      setAskResult(await api.biAsk(question));
    } catch (e) {
      setAskResult(null);
      setAskError(e.message);
    } finally {
      setAskBusy(false);
    }
  };

  const writeBack = async (call, notice) => {
    setWbBusy(true);
    setWbError(null);
    setWbNotice(null);
    try {
      await call();
      setWbNotice(notice);
      await load();
    } catch (e) {
      setWbError(e.message);
    } finally {
      setWbBusy(false);
    }
  };

  const proposeAction = (rca) => writeBack(
    () => api.biProposeAction({
      series: rca.series, month: rca.month,
      note: `Investigate the ${rca.leading_driver} contribution to the ${monthLabel(rca.month)} deviation.`,
      idempotency_key: newKey(),
    }),
    `Action proposed to ${rca.approver}.`,
  );

  const decideAction = (action, decision) => writeBack(
    () => api.biDecideAction(action.action_id, {
      decision,
      note: decision === "approved" ? "Approved from the dashboard." : "Rejected from the dashboard.",
      expected_state: action.state,
      idempotency_key: newKey(),
    }),
    `Action ${decision}.`,
  );

  const annotate = (fields) => writeBack(
    () => api.biAnnotate({ ...fields, idempotency_key: newKey() }),
    "Annotation saved.",
  );

  const rebuild = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.biExport();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const cycle = (data && data.cycle) || {};
  const mart = data ? data.mart : null;
  const superset = (data && data.superset) || null;
  const tables = mart ? Object.entries(mart.tables || {}).map(([name, rows]) => ({ name, rows })) : [];
  const totalRows = tables.reduce((sum, t) => sum + t.rows, 0);
  const inScope = Number(cycle.in_scope_skus) || 0;
  const autopilot = Number(cycle.autopilot_approved_skus) || 0;
  const autopilotPct = inScope ? Math.round((autopilot / inScope) * 100) : 0;
  const months = (data && data.plan_vs_baseline) || [];
  const planSeries = [
    { name: "Plan", style: "plan", values: months.map((m) => m.plan) },
    { name: "ML forecast", style: "forecast", values: months.map((m) => m.ml) },
    { name: "Baseline", style: "actual", values: months.map((m) => m.baseline) },
  ];
  const layout = LAYOUT[persona] || LAYOUT.analyst;
  const freshness = (data && data.freshness) || {};
  const builtAgo = ageLabel(freshness.mart_generated_at);
  const personaHint = (PERSONAS.find((p) => p.id === persona) || {}).hint;
  const baselineValue = Number(cycle.baseline_value_gbp) || 0;
  const varianceValue = Number(cycle.net_value_variance_gbp) || 0;
  const variancePct = baselineValue
    ? `${((varianceValue / baselineValue) * 100).toFixed(1)}% vs baseline`
    : null;

  const PANELS = {
    triage: () => (
      <Panel title="Triage mix" testId="bi-triage-mix" className="h-full"
        subtitle={`${fmtNum(cycle.total_catalog_skus)} catalog SKUs, ${fmtNum(inScope)} in scope.`}>
        <DonutChart testId="bi-triage-donut" label="Triage mix by state"
          data={(data.by_state || []).map((r) => ({ label: STATE_LABELS[r.state] || r.state, value: r.count }))} />
      </Panel>
    ),
    channel: () => (
      <Panel title="Exceptions by channel" testId="bi-channel-panel" className="h-full">
        <BarChart testId="bi-channel-bars" label="Exceptions by channel"
          data={(data.exceptions_by_channel || []).map((r) => ({ label: r.channel, value: r.count }))} />
      </Panel>
    ),
    risk: () => (
      <Panel title="Value at risk by risk type" testId="bi-risk-panel" className="h-full">
        <HBarList testId="bi-risk-bars" color={IBCS.negative} format={fmtGbpPlain}
          label="Value at risk by risk type"
          data={(data.var_by_risk_type || []).map((r) => ({ label: riskLabel(r.risk_type), value: r.var_gbp }))} />
      </Panel>
    ),
    overrides: () => (
      <Panel title="Overrides by reason code" testId="bi-overrides-panel" className="h-full">
        <HBarList testId="bi-overrides-bars" color={IBCS.actual} label="Overrides by reason code"
          data={(data.overrides_by_reason || []).map((r) => ({ label: r.reason_code, value: r.count }))} />
      </Panel>
    ),
    plan: () => (
      <Panel title="Plan vs baseline by month" testId="bi-plan-panel" className="h-full"
        subtitle="Monthly plan, ML forecast and baseline summed across in-scope items.">
        <LineChart testId="bi-plan-line" series={planSeries} label="Plan, ML forecast and baseline by month"
          labels={months.map((m) => monthLabel(m.month))} />
      </Panel>
    ),
    tree: () => (
      <Panel title="KPI driver tree" testId="bi-kpi-tree-panel"
        subtitle="Root to branch to leaf. Each node carries one owner, a confidence rating and a target corridor.">
        <KpiTree tree={kpiTree} />
      </Panel>
    ),
    diagnostics: () => (
      <Panel title="Anomaly diagnostics" testId="bi-diagnostics-panel"
        subtitle="Months outside the modified z-score band, each explained by segment contribution and gated by the four-check RCA protocol.">
        <Diagnostics data={diagnostics} canWrite={canWrite} onPropose={proposeAction} busy={wbBusy} />
      </Panel>
    ),
    actions: () => (
      <Panel title="Proposed actions" testId="bi-actions-panel" className="h-full"
        subtitle="Only a cause that passed all four RCA checks can be proposed. Every decision is appended to a hash-chained log.">
        {wbNotice && <div data-testid="bi-writeback-notice" className="mb-2 text-[11px] text-emerald-300">{wbNotice}</div>}
        {wbError && <div data-testid="bi-writeback-error" className="mb-2 text-[11px] text-rose-300">{wbError}</div>}
        <ActionQueue actions={actions} canWrite={canWrite} onDecide={decideAction} busy={wbBusy} />
      </Panel>
    ),
    annotate: () => (
      <Panel title="Annotate an exception" testId="bi-annotate-panel" className="h-full"
        subtitle="Notes are append-only and attributed; a later note never overwrites an earlier one.">
        <AnnotationForm annotations={annotations} canWrite={canWrite} onAnnotate={annotate} busy={wbBusy} />
      </Panel>
    ),
    top: () => (
      <Panel title="Top exceptions by value at risk" testId="bi-top-panel">
        <DataTable testId="bi-top-table" rows={data.top_exceptions || []} empty="No exceptions in this cycle."
          columns={[
            { key: "item_id", label: "Item", render: (r) => <span className="font-mono text-slate-200">{r.item_id}</span> },
            { key: "product_id", label: "Product" },
            { key: "channel", label: "Channel" },
            { key: "risk_type", label: "Risk", render: (r) => riskLabel(r.risk_type) },
            { key: "var_gbp", label: "VaR", right: true, render: (r) => fmtGbpPlain(r.var_gbp) },
          ]} />
      </Panel>
    ),
    fva: () => (
      <Panel title="FVA by planner" testId="bi-fva-panel"
        subtitle="Forecast value added on matured overrides (points = FVA / volume).">
        <DataTable testId="bi-fva-table" rows={data.fva_by_planner || []} empty="No matured overrides yet."
          columns={[
            { key: "planner_id", label: "Planner" },
            { key: "n_overrides", label: "Overrides", right: true, render: (r) => fmtNum(r.n_overrides) },
            { key: "n_accepted", label: "Accepted", right: true, render: (r) => fmtNum(r.n_accepted) },
            { key: "volume", label: "Volume", right: true, render: (r) => fmtNum(r.volume) },
            { key: "fpe_ml", label: "FPE ML", right: true, render: (r) => fmtNum(r.fpe_ml, 2) },
            { key: "fpe_planner", label: "FPE planner", right: true, render: (r) => fmtNum(r.fpe_planner, 2) },
            { key: "fva_pts", label: "FVA pts", right: true, render: (r) => fmtNum(r.fva_pts, 1) },
          ]} />
      </Panel>
    ),
    metrics: () => (
      <Panel title="Metric catalog" testId="bi-metrics-panel"
        subtitle="One governed definition per metric: owner, grain, aggregation and version. The KPI tree and the ask panel both read from this registry, so a metric is defined once.">
        <DataTable testId="bi-metrics-table" rows={metrics} empty="No metrics registered."
          columns={[
            { key: "id", label: "Metric", render: (r) => <span className="font-mono text-slate-200">{r.id}</span> },
            { key: "label", label: "Label" },
            { key: "owner", label: "Owner" },
            { key: "grain", label: "Grain" },
            { key: "aggregation", label: "Agg" },
            { key: "version", label: "Ver" },
          ]} />
      </Panel>
    ),
    ask: () => (
      <Panel title="Ask the metrics" testId="bi-ask-panel"
        subtitle="Questions resolve to governed metric ids. The model never writes SQL.">
        <form onSubmit={ask} className="flex gap-2">
          <input data-testid="bi-ask-input" value={askQuestion} aria-label="Ask a question about the metrics"
            onChange={(e) => setAskQuestion(e.target.value)}
            placeholder="e.g. How much value is exposed to out-of-stock risk?"
            className="flex-1 rounded border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500" />
          <button data-testid="bi-ask-submit" type="submit" disabled={askBusy || !askQuestion.trim()}
            className="rounded border border-indigo-700 bg-indigo-950 px-3 py-2 text-xs font-semibold text-indigo-200 disabled:opacity-50">
            {askBusy ? "Asking..." : "Ask"}
          </button>
        </form>
        {askError && <div data-testid="bi-ask-error" className="mt-2 text-xs text-rose-300">{askError}</div>}
        {askResult && (
          <div data-testid="bi-ask-result" className="mt-3 space-y-2">
            {askResult.metrics.length === 0 && (
              <div className="text-xs text-slate-400">No governed metric matched that question.</div>
            )}
            {askResult.metrics.map((m) => (
              <div key={m.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="text-slate-300">
                  {m.label} <span className="font-mono text-slate-500">{m.id}</span>
                </span>
                <span className="font-mono text-slate-100">{formatValue(m.value, m.unit)}</span>
              </div>
            ))}
            {askResult.rejected.length > 0 && (
              <div className="text-[11px] text-amber-300">
                Rejected unknown ids: {askResult.rejected.join(", ")}
              </div>
            )}
          </div>
        )}
      </Panel>
    ),
    finops: () => (
      <Panel title="FinOps and sprawl" testId="bi-finops-panel"
        subtitle="Every governed query carries a team, pipeline, environment and workload tag. No cloud warehouse is configured, so the spend tiers are declared policy rather than a measured figure.">
        <FinOps data={finops} />
      </Panel>
    ),
    serving: () => (
      <Panel title="Serving SLAs and transport" testId="bi-serving-panel"
        subtitle="Each table declares a freshness tier and each workload a latency target; the report measures the mart against both. The transport comparison encodes the same tables as JSON, Arrow IPC and Parquet.">
        <Serving data={serving} transport={transport} busy={transportBusy}
          onMeasure={measureTransport} onDownload={downloadColumnar} />
      </Panel>
    ),
    mart: () => (
      <Panel title="Data mart" testId="bi-mart-panel" className="h-full"
        subtitle="SQLite mart rebuilt from the cycle artifacts and the audit ledger."
        right={isAdmin && (
          <button data-testid="bi-rebuild-btn" disabled={busy} onClick={rebuild}
            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 rounded-lg text-xs font-semibold flex items-center gap-2">
            <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} /> {busy ? "Rebuilding..." : "Rebuild mart"}
          </button>
        )}>
        {mart && !mart.exists && (
          <div className="text-xs text-amber-200 bg-amber-950/30 border border-amber-800 rounded-lg p-3">
            No mart yet. Build it with <code className="font-mono">python -m app.bi.export_mart</code>.
          </div>
        )}
        {mart && mart.exists && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <Stat label="Tables" value={fmtNum(tables.length)} tone="text-indigo-300" />
              <Stat label="Rows" value={fmtNum(totalRows)} />
            </div>
            {contracts && contracts.conformance && (
              <div data-testid="bi-contract-badge"
                className={`text-[11px] rounded-lg border p-2 ${contracts.conformance.ok
                  ? "border-emerald-800 bg-emerald-950/30 text-emerald-200"
                  : "border-rose-800 bg-rose-950/30 text-rose-200"}`}>
                {contracts.conformance.ok
                  ? `Contract: ${contracts.conformance.summary.present}/${contracts.conformance.summary.tables} tables conform`
                  : `Contract drift: ${contracts.conformance.summary.errors} error(s) — ${(contracts.conformance.problems.find((p) => p.severity === "error") || {}).detail}`}
              </div>
            )}
            <DataTable testId="bi-mart-tables" rows={tables} empty="No tables in the mart."
              columns={[
                { key: "name", label: "Table", render: (r) => <span className="font-mono text-slate-200">{r.name}</span> },
                { key: "rows", label: "Rows", right: true, render: (r) => fmtNum(r.rows) },
              ]} />
            <p className="text-[11px] text-slate-500 font-mono">{mart.path}</p>
          </div>
        )}
      </Panel>
    ),
    superset: () => (
      <Panel title="Apache Superset (optional)" testId="bi-superset-panel" className="h-full"
        subtitle="The charts above render natively from the mart. Superset can read the same mart for ad-hoc analysis."
        right={superset && (
          <a href={superset.dashboard_url} target="_blank" rel="noreferrer"
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs font-semibold flex items-center gap-2">
            <ExternalLink className="h-4 w-4" /> Open in Superset
          </a>
        )}>
        {superset && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-3 text-[11px] text-slate-400">
              <span className="flex items-center gap-1"><Database className="h-3.5 w-3.5" /> {superset.url}</span>
              <span className="flex items-center gap-1"><BarChart3 className="h-3.5 w-3.5" /> dashboard <code className="font-mono">{superset.slug}</code></span>
              <span className={`flex items-center gap-1 ${superset.reachable ? "text-emerald-400" : "text-amber-300"}`}>
                {superset.reachable ? "Superset reachable" : "Superset not running"}
              </span>
            </div>
            {!superset.reachable && (
              <div data-testid="bi-superset-offline" className="rounded-lg border border-amber-800 bg-amber-950/30 p-3 space-y-2">
                <p className="text-[11px] text-amber-100">Superset is optional — the dashboard above works without it.</p>
                <p className="text-[11px] text-amber-100/80">Start it with <code className="font-mono">.\superset\run_native.ps1</code>.</p>
                <button data-testid="bi-recheck-btn" onClick={load}
                  className="px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs font-semibold flex items-center gap-2">
                  <RefreshCw className="h-4 w-4" /> Check again
                </button>
              </div>
            )}
          </div>
        )}
      </Panel>
    ),
  };

  return (
    <div className="px-8 py-6 space-y-6" data-testid="bi-page">
      <ErrorNote error={error} />

      <header className="flex flex-wrap items-start justify-between gap-4 pb-5 border-b border-slate-800">
        <div className="space-y-3">
          <div>
            <h1 className="text-lg font-bold text-white tracking-tight">Supply &amp; operations BI</h1>
            <p className="text-[11px] text-slate-400 mt-1" data-testid="bi-persona-hint">{personaHint}</p>
          </div>
          <div role="tablist" aria-label="Dashboard persona" data-testid="bi-persona-tabs"
            className="inline-flex gap-1 rounded-lg border border-slate-800 bg-slate-950 p-1">
            {PERSONAS.map((p) => (
              <button key={p.id} type="button" role="tab" aria-selected={persona === p.id}
                data-testid={`bi-persona-${p.id}`} onClick={() => setPersona(p.id)}
                className={`px-3 py-1.5 rounded-md text-xs font-semibold transition ${
                  persona === p.id
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
                }`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data && (
            <div className="flex flex-wrap items-center gap-2 text-[11px]" data-testid="bi-status-bar">
              <span data-testid="bi-freshness-badge"
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-900 px-2.5 py-1 text-slate-300">
                <Clock className="h-3 w-3" aria-hidden="true" /> Mart built {builtAgo || "unknown"}
              </span>
              {freshness.cycle_cutoff && (
                <span data-testid="bi-cycle-badge" className="rounded-full border border-slate-800 bg-slate-900 px-2.5 py-1 text-slate-300">
                  Cycle cutoff {monthLabel(freshness.cycle_cutoff)}
                </span>
              )}
              <span data-testid="bi-latency-badge" title="This dashboard request"
                className="rounded-full border border-slate-800 bg-slate-900 px-2.5 py-1 text-slate-300">
                {fmtNum(data.duration_ms, 0)} ms{data.cached ? " cached" : ""}
              </span>
            </div>
          )}
          <button type="button" data-testid="bi-refresh-btn" onClick={load}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] font-semibold text-slate-300 hover:bg-slate-800">
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </button>
        </div>
      </header>

      {data && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4" data-testid="bi-kpis">
          <KpiCard label="Autopilot coverage" value={`${fmtNum(autopilot)} (${autopilotPct}%)`}
            sub={`of ${fmtNum(inScope)} in-scope SKUs`} tone="text-emerald-300" />
          <KpiCard label="Planned value" value={fmtGbpPlain(cycle.planned_value_gbp)}
            sub={`baseline ${fmtGbpPlain(cycle.baseline_value_gbp)}`} tone="text-indigo-300" />
          <KpiCard label="Net vs baseline" value={fmtGbp(cycle.net_value_variance_gbp)}
            sub={variancePct} tone={varianceValue < 0 ? "text-rose-300" : "text-emerald-300"} />
        </div>
      )}

      {!data && !error && <div className="text-xs text-slate-400">Loading dashboard...</div>}

      {data && (
        <div className="space-y-8">
          {layout.map((group) => (
            <Section key={group.id} title={group.title} hint={group.hint}>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                {group.panels.map((p) => (
                  <div key={p.key} className={p.span === 2 ? "lg:col-span-2" : ""}>
                    {PANELS[p.key]()}
                  </div>
                ))}
              </div>
            </Section>
          ))}
        </div>
      )}
    </div>
  );
}
