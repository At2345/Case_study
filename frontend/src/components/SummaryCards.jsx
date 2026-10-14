import React from "react";
import { CheckCircle2, AlertTriangle, TrendingUp, PoundSterling } from "lucide-react";
import { fmtNum, fmtGbp, fmtGbpPlain } from "../lib/format";

function Card({ title, icon, value, valueClass, sub }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div className="flex justify-between items-start">
        <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">{title}</span>
        {icon}
      </div>
      <div className={`text-2xl font-bold mt-2 ${valueClass}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1">{sub}</div>
    </div>
  );
}

export default function SummaryCards({ summary }) {
  if (!summary) return null;
  const touchless = summary.in_scope_skus ? (100 * summary.autopilot_approved_skus) / summary.in_scope_skus : 0;
  const fva = summary.fva;
  return (
    <section className="grid grid-cols-1 md:grid-cols-4 gap-4 px-8 py-6">
      <Card title="Touchless automation" icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />}
        value={`${touchless.toFixed(1)}%`} valueClass="text-emerald-400"
        sub={`${fmtNum(summary.autopilot_approved_skus)} of ${fmtNum(summary.in_scope_skus)} active items auto-approved`} />
      <Card title="Exception queue" icon={<AlertTriangle className="h-5 w-5 text-amber-400" />}
        value={`${summary.decided} / ${summary.exceptions_requiring_review}`} valueClass="text-amber-400"
        sub={`${summary.pending_decisions} pending | ${summary.critical_alerts} OOS / Buy Box alerts | ${summary.planner_led_skus} planner-led`} />
      <Card title="Forecast value added" icon={<TrendingUp className="h-5 w-5 text-indigo-400" />}
        value={fva ? `${fva.fva_pts >= 0 ? "+" : ""}${fva.fva_pts.toFixed(1)} pts` : "n/a"} valueClass="text-indigo-400"
        sub={fva ? `Planner overrides vs ML over ${fva.matured_overrides} matured overrides` : "No overrides with actuals yet"} />
      <Card title="Planned vs baseline" icon={<PoundSterling className="h-5 w-5 text-slate-400" />}
        value={fmtGbpPlain(summary.planned_value_gbp)} valueClass="text-white"
        sub={`${fmtGbp(summary.net_value_variance_gbp)} vs statistical baseline proxy`} />
    </section>
  );
}
