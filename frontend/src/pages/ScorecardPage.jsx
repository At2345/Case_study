import React, { useCallback, useEffect, useState } from "react";
import { ShieldCheck, ShieldAlert, Bot } from "lucide-react";
import { api } from "../services/api";
import { fmtNum, fmtGbp, monthLabel } from "../lib/format";
import { Panel, Stat, DataTable, ErrorNote } from "../components/ui";

const pts = (v) => (v === null || v === undefined ? "-" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)} pts`);

function FvaTable({ data }) {
  const key = data.group_by;
  return (
    <DataTable testId="fva-table" rows={data.rows} empty="No override has matured yet: FVA is scored once the month's actuals arrive."
      columns={[
        { key, label: key === "planner_id" ? "Planner" : "Reason code" },
        { key: "n_overrides", label: "Overrides", right: true },
        { key: "fpe_ml", label: "FPE ML", right: true, render: (r) => `${fmtNum(r.fpe_ml, 1)}%` },
        { key: "fpe_planner", label: "FPE planner", right: true, render: (r) => `${fmtNum(r.fpe_planner, 1)}%` },
        { key: "fva_pts", label: "FVA", right: true, render: (r) => <span className={r.fva_pts >= 0 ? "text-emerald-400" : "text-rose-400"}>{pts(r.fva_pts)}</span> },
      ]} />
  );
}

export default function ScorecardPage({ role }) {
  const [groupBy, setGroupBy] = useState("planner_id");
  const [segment, setSegment] = useState("channel");
  const [fva, setFva] = useState(null);
  const [auto, setAuto] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [verify, setVerify] = useState(null);
  const [error, setError] = useState(null);
  const canVerify = role === "auditor" || role === "admin";

  const load = useCallback(async () => {
    try {
      const [f, a, l] = await Promise.all([api.fva(groupBy), api.autopilot(segment), api.overrides(200)]);
      setFva(f);
      setAuto(a);
      setLedger([...l].reverse());
    } catch (e) {
      setError(e.message);
    }
  }, [groupBy, segment]);

  useEffect(() => { load(); }, [load]);

  const runVerify = async () => {
    try { setVerify(await api.auditVerify()); } catch (e) { setError(e.message); }
  };

  const accepted = ledger.filter((r) => r.reason_code === "ML Output Formally Accepted").length;
  return (
    <div className="px-8 py-6 space-y-6" data-testid="scorecard-page">
      <ErrorNote error={error} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat testId="stat-ledger-rows" label="Ledger decisions" value={fmtNum(ledger.length)} sub="this cycle, insert-only" />
        <Stat label="ML formally accepted" value={fmtNum(accepted)} tone="text-indigo-300" />
        <Stat label="Overrides" value={fmtNum(ledger.length - accepted)} tone="text-amber-300" />
        <Stat testId="stat-matured" label="Matured (scored)" value={fva ? fmtNum(fva.matured_overrides) : "-"} sub="overrides with actuals" tone="text-emerald-300" />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Panel title="Forecast Value Added scorecard" subtitle={fva ? fva.note : ""}
          right={
            <select data-testid="fva-groupby-select" value={groupBy} onChange={(e) => setGroupBy(e.target.value)}
              className="bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-200">
              <option value="planner_id">By planner</option>
              <option value="reason_code">By reason code</option>
            </select>
          }>
          {fva && <FvaTable data={fva} />}
        </Panel>
        <Panel title="Autopilot promotion rule" testId="autopilot-panel"
          subtitle={auto ? `${auto.note} Requires ${auto.min_cycles} consecutive scored cycles.` : ""}
          right={
            <select data-testid="autopilot-segment-select" value={segment} onChange={(e) => setSegment(e.target.value)}
              className="bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-200">
              <option value="channel">By channel</option>
              <option value="abc_category">By ABC class</option>
            </select>
          }>
          {auto && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs">
                <Bot className="h-4 w-4 text-indigo-400" />
                {auto.candidates.length ? (
                  <span className="text-amber-200">Move to autopilot: <strong>{auto.candidates.join(", ")}</strong></span>
                ) : (
                  <span className="text-slate-400" data-testid="autopilot-empty">No segment has a negative-FVA streak yet.</span>
                )}
              </div>
              <DataTable rows={auto.per_cycle} empty="Per-cycle FVA appears once overrides from past cycles have actuals."
                columns={[
                  { key: "cutoff", label: "Cycle" }, { key: "segment", label: "Segment" },
                  { key: "n_overrides", label: "Overrides", right: true },
                  { key: "fva_pts", label: "FVA", right: true, render: (r) => pts(r.fva_pts) },
                ]} />
            </div>
          )}
        </Panel>
      </div>
      <Panel title="Audit ledger" subtitle="Every decision with identity, session, reason and financial impact. Rows are hash-chained; edits are detectable."
        right={canVerify && (
          <button data-testid="audit-verify-btn" onClick={runVerify}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs font-semibold flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-400" /> Verify hash chain
          </button>
        )}>
        {verify && (
          <div data-testid="audit-verify-result" className={`mb-3 text-xs flex items-center gap-2 ${verify.ok ? "text-emerald-300" : "text-rose-300"}`}>
            {verify.ok ? <ShieldCheck className="h-4 w-4" /> : <ShieldAlert className="h-4 w-4" />}
            {verify.ok ? "Chain intact: no row has been altered." : `Chain broken at row ${verify.first_bad_id}.`}
          </div>
        )}
        <DataTable testId="ledger-table" rows={ledger} empty="No decisions recorded in this cycle yet."
          columns={[
            { key: "timestamp", label: "When", render: (r) => new Date(r.timestamp).toLocaleString("en-GB") },
            { key: "item_id", label: "Item" }, { key: "target_month", label: "Month", render: (r) => monthLabel(r.target_month) },
            { key: "planner_id", label: "By" }, { key: "reason_code", label: "Reason" },
            { key: "ml_recommended_quantity", label: "ML", right: true, render: (r) => fmtNum(r.ml_recommended_quantity) },
            { key: "submitted_quantity", label: "Final", right: true, render: (r) => fmtNum(r.submitted_quantity) },
            { key: "financial_impact_gbp", label: "Impact", right: true, render: (r) => fmtGbp(r.financial_impact_gbp) },
          ]} />
      </Panel>
    </div>
  );
}
