import React, { useCallback, useEffect, useState } from "react";
import { Play, RefreshCw, CheckCircle2, XCircle, Circle, PauseCircle, Database } from "lucide-react";
import { api } from "../services/api";
import { fmtNum } from "../lib/format";
import { Panel, Stat, DataTable, ErrorNote } from "../components/ui";

const STATUS_STYLE = {
  RUNNING: "bg-sky-900 text-sky-200", AWAITING_REVIEW: "bg-amber-900 text-amber-200", SUCCEEDED: "bg-emerald-900 text-emerald-200",
  COMMITTED: "bg-emerald-900 text-emerald-200", FAILED: "bg-rose-900 text-rose-200",
};

function NodeTrack({ nodes, run }) {
  const done = Object.fromEntries((run.steps || []).map((s) => [s.node, s]));
  const reached = run.status === "AWAITING_REVIEW" || run.status === "COMMITTED";
  return (
    <ol className="flex flex-wrap gap-2 mt-3">
      {nodes.map((n) => {
        const s = done[n];
        const review = n === "human_review";
        let icon = <Circle className="h-3.5 w-3.5 text-slate-600" />;
        if (s && s.status === "ok") icon = <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />;
        if (s && s.status === "failed") icon = <XCircle className="h-3.5 w-3.5 text-rose-400" />;
        if (review && reached) icon = <PauseCircle className="h-3.5 w-3.5 text-amber-400" />;
        return (
          <li key={n} title={s ? (s.error || JSON.stringify(s.detail)) : ""}
            className="flex items-center gap-1.5 text-[11px] bg-slate-950 border border-slate-800 rounded-md px-2 py-1 text-slate-300">
            {icon} {n.replace(/_/g, " ")} {s && <span className="text-slate-500 font-mono">{s.seconds}s</span>}
          </li>
        );
      })}
    </ol>
  );
}

function RegressionTable({ reg }) {
  const rows = Object.entries(reg.columns).map(([k, v]) => ({ id: k, column: k, ...v }));
  return (
    <DataTable testId="regression-table" rows={rows}
      columns={[{ key: "column", label: "past_covariates column" }, { key: "rows", label: "Item-months", right: true, render: (r) => fmtNum(r.rows) },
        { key: "match_rate", label: "Match", right: true, render: (r) => (r.match_rate === null ? "-" : `${(r.match_rate * 100).toFixed(2)}%`) }]} />
  );
}

export default function OperationsPage({ role, onReloaded }) {
  const [runs, setRuns] = useState(null);
  const [amazon, setAmazon] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const isAdmin = role === "admin";

  const load = useCallback(async () => {
    try {
      const [r, a] = await Promise.all([api.runs(), api.amazonPipeline()]);
      setRuns(r);
      setAmazon(a);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const running = runs && runs.runs.some((r) => r.status === "RUNNING");
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);
  const wasRunning = React.useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) onReloaded();
    wasRunning.current = running;
  }, [running, onReloaded]);

  const start = async (fn) => {
    setBusy(true);
    setError(null);
    try { await fn(); await load(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const meta = amazon && amazon.meta;
  return (
    <div className="px-8 py-6 space-y-6" data-testid="operations-page">
      <ErrorNote error={error} />
      <Panel title="Monthly cycle orchestration (LangGraph)" testId="cycle-runs-panel"
        subtitle="INGEST/VALIDATE, AMAZON DAILY, BASE INFERENCE, RECONCILE, PHASING, RULES/TRIAGE, BANDS/SAFETY STOCK, NARRATIVE, then an interrupt for human review. A failed guard halts the run; nothing after it is published."
        right={isAdmin && (
          <button data-testid="run-cycle-btn" disabled={busy || running} onClick={() => start(api.runCycle)}
            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 rounded-lg text-xs font-semibold flex items-center gap-2">
            {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} {running ? "Running..." : "Run monthly cycle"}
          </button>
        )}>
        {runs && runs.runs.length === 0 && <div className="text-xs text-slate-500" data-testid="no-runs">No pipeline run recorded yet.</div>}
        <div className="space-y-3">
          {runs && runs.runs.map((r) => (
            <div key={r.id} data-testid={`run-${r.id}`} className="bg-slate-950/60 border border-slate-800 rounded-lg p-3">
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${STATUS_STYLE[r.status] || "bg-slate-800"}`} data-testid="run-status">{r.status}</span>
                <span className="text-slate-200 font-semibold">{r.kind === "monthly_cycle" ? "Monthly cycle" : "Amazon daily pipeline"}</span>
                <span className="text-slate-500">{new Date(r.started_at).toLocaleString("en-GB")} by {r.started_by}</span>
                {r.error && <span className="text-rose-300">{r.error}</span>}
              </div>
              {r.kind === "monthly_cycle" && <NodeTrack nodes={runs.nodes} run={r} />}
              {(r.steps || []).filter((s) => s.status === "failed").map((s) => (
                <div key={s.node} className="text-[11px] text-rose-300 mt-2">{s.node}: {s.error}</div>
              ))}
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Daily Amazon feature pipeline (Phase 2, DuckDB)" testId="amazon-pipeline-panel"
        subtitle="Rebuilds the monthly Amazon covariates from the daily traffic, sell-out and inventory drops (regression test against past_covariates) and recalculates the 14-day glance-view risk."
        right={isAdmin && (
          <button data-testid="run-amazon-btn" disabled={busy || running} onClick={() => start(api.runAmazon)}
            className="px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 disabled:opacity-40 rounded-lg text-xs font-semibold flex items-center gap-2">
            <Database className="h-4 w-4" /> Run daily pipeline
          </button>
        )}>
        {!meta && <div className="text-xs text-slate-500">The daily pipeline has not run yet.</div>}
        {meta && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Stat testId="amazon-rows-in" label="Daily rows read" value={fmtNum(meta.rows_in)} sub={`as of ${meta.as_of}`} />
              <Stat testId="amazon-seconds" label="Runtime (NF-04)" value={`${meta.total_seconds}s`} sub={`SLA ${meta.sla_seconds}s ${meta.sla_ok ? "met" : "breached"}`} tone={meta.sla_ok ? "text-emerald-400" : "text-rose-400"} />
              <Stat label="Item-months rebuilt" value={fmtNum(meta.item_months_out)} />
              <Stat testId="amazon-surges" label="Glance-view surges" value={fmtNum(meta.surge_items)} sub="> +20% last 14 days vs prior 14" tone="text-amber-300" />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div><h3 className="text-xs font-semibold text-slate-300 mb-2">Regression vs past_covariates</h3><RegressionTable reg={meta.regression} /></div>
              <div>
                <h3 className="text-xs font-semibold text-slate-300 mb-2">Largest traffic surges</h3>
                <DataTable testId="surges-table" rows={amazon.top_surges}
                  columns={[{ key: "item_id", label: "Item" }, { key: "gv_prev14", label: "Prior 14d", right: true, render: (r) => fmtNum(r.gv_prev14) },
                    { key: "gv_last14", label: "Last 14d", right: true, render: (r) => fmtNum(r.gv_last14) },
                    { key: "glance_change_14d", label: "Change", right: true, render: (r) => `+${(r.glance_change_14d * 100).toFixed(0)}%` },
                    { key: "oos_rate_28d", label: "OOS 28d", right: true, render: (r) => (r.oos_rate_28d == null ? "-" : `${(r.oos_rate_28d * 100).toFixed(0)}%`) }]} />
              </div>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}
