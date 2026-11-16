import React from "react";
import { fmtGbpPlain, fmtNum, monthLabel } from "../lib/format";
import { formatValue } from "./KpiTree";

const STATUS_STYLES = {
  actionable: "bg-emerald-950 text-emerald-300 border-emerald-800",
  hypothesis: "bg-amber-950 text-amber-300 border-amber-800",
};

const STATUS_LABELS = { actionable: "Actionable", hypothesis: "Hypothesis" };

const SEVERITY_STYLES = {
  high: "bg-rose-950 text-rose-300 border-rose-800",
  medium: "bg-amber-950 text-amber-300 border-amber-800",
};

const checkStyle = (passed) =>
  passed
    ? "bg-emerald-950 text-emerald-300 border-emerald-800"
    : "bg-rose-950 text-rose-300 border-rose-800";

function CheckPill({ check }) {
  return (
    <li data-testid={`bi-rca-check-${check.id}`} className="flex items-start gap-2 text-[11px]">
      <span className={`px-1.5 py-0.5 rounded border font-semibold shrink-0 ${checkStyle(check.passed)}`}>
        {check.passed ? "pass" : "fail"}
      </span>
      <span className="text-slate-300">
        <span className="font-semibold text-slate-200">{check.id.replace(/_/g, " ")}</span>
        <span className="text-slate-400"> — {check.detail}</span>
      </span>
    </li>
  );
}

function RcaCard({ rca, unit, canWrite, onPropose, busy }) {
  return (
    <div data-testid={`bi-rca-${rca.series}-${rca.month}`}
      className="rounded-lg border border-slate-800 bg-slate-950 p-3 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-slate-100">
            {monthLabel(rca.month)} · driver {rca.leading_driver || "none"}
          </div>
          <div className="text-[11px] text-slate-400">
            {formatValue(rca.total_deviation, unit)} against a baseline of {formatValue(rca.baseline, unit)}
            {" · "}approver {rca.approver || "unassigned"}
          </div>
        </div>
        <span data-testid={`bi-rca-status-${rca.series}-${rca.month}`}
          className={`px-2 py-0.5 rounded border text-[10px] font-semibold ${STATUS_STYLES[rca.status] || STATUS_STYLES.hypothesis}`}>
          {STATUS_LABELS[rca.status] || rca.status}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <caption className="sr-only">
            Segment contributions to the {monthLabel(rca.month)} deviation
          </caption>
          <thead>
            <tr className="text-slate-400 text-left">
              <th scope="col" className="py-1 pr-3 font-medium">Segment</th>
              <th scope="col" className="py-1 pr-3 font-medium text-right">Value</th>
              <th scope="col" className="py-1 pr-3 font-medium text-right">Expected</th>
              <th scope="col" className="py-1 pr-3 font-medium text-right">Contribution</th>
              <th scope="col" className="py-1 pr-3 font-medium text-right">z</th>
              <th scope="col" className="py-1 font-medium">First move</th>
            </tr>
          </thead>
          <tbody>
            {rca.contributions.map((c) => (
              <tr key={c.segment} className="border-t border-slate-800">
                <td className="py-1 pr-3 text-slate-200">{c.segment}</td>
                <td className="py-1 pr-3 text-right font-mono text-slate-300">{formatValue(c.value, unit)}</td>
                <td className="py-1 pr-3 text-right font-mono text-slate-400">{formatValue(c.expected, unit)}</td>
                <td className={`py-1 pr-3 text-right font-mono ${c.contribution < 0 ? "text-rose-300" : "text-emerald-300"}`}>
                  {formatValue(c.contribution, unit)}
                </td>
                <td className="py-1 pr-3 text-right font-mono text-slate-400">{fmtNum(c.z, 2)}</td>
                <td className="py-1 text-slate-400">{c.first_deviation ? monthLabel(c.first_deviation) : "never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul data-testid={`bi-rca-checks-${rca.series}-${rca.month}`} className="space-y-1.5 pt-2 border-t border-slate-800">
        {rca.checks.map((check) => <CheckPill key={check.id} check={check} />)}
      </ul>

      {rca.actionable && canWrite && (
        <button data-testid={`bi-rca-propose-${rca.series}-${rca.month}`} disabled={busy}
          onClick={() => onPropose(rca)}
          className="px-3 py-1.5 rounded border border-indigo-700 bg-indigo-950 text-indigo-200 text-[11px] font-semibold disabled:opacity-50">
          {busy ? "Proposing..." : `Propose action to ${rca.approver}`}
        </button>
      )}

      {rca.evidence_withheld && (
        <p data-testid={`bi-rca-withheld-${rca.series}-${rca.month}`}
          className="text-[11px] text-amber-300">
          Item-level evidence is withheld for your role. Ask a planner or admin to review this cause.
        </p>
      )}

      {rca.evidence.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <caption className="sr-only">Item-level evidence behind the leading driver</caption>
            <thead>
              <tr className="text-slate-400 text-left">
                <th scope="col" className="py-1 pr-3 font-medium">Item</th>
                <th scope="col" className="py-1 pr-3 font-medium">Product</th>
                <th scope="col" className="py-1 pr-3 font-medium">Channel</th>
                <th scope="col" className="py-1 pr-3 font-medium">Risk</th>
                <th scope="col" className="py-1 font-medium text-right">VaR</th>
              </tr>
            </thead>
            <tbody>
              {rca.evidence.map((row) => (
                <tr key={row.item_id} className="border-t border-slate-800">
                  <td className="py-1 pr-3 font-mono text-slate-200">{row.item_id}</td>
                  <td className="py-1 pr-3 text-slate-300">{row.product_id}</td>
                  <td className="py-1 pr-3 text-slate-300">{row.channel}</td>
                  <td className="py-1 pr-3 text-slate-400">{row.risk_type || "-"}</td>
                  <td className="py-1 text-right font-mono text-slate-300">{fmtGbpPlain(row.var_gbp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function Diagnostics({ data, canWrite, onPropose, busy }) {
  if (!data) return <div className="text-xs text-slate-400">Loading diagnostics...</div>;
  const summary = data.summary || {};
  const series = data.series || [];
  const totalAnomalies = Number(summary.anomalies) || 0;

  return (
    <div data-testid="bi-diagnostics" className="space-y-5">
      <div className="flex flex-wrap gap-4 text-[11px] text-slate-400">
        <span data-testid="bi-diagnostics-summary">
          {fmtNum(summary.anomalies)} anomalies across {fmtNum(summary.series)} series
        </span>
        <span className="text-emerald-300">{fmtNum(summary.actionable)} actionable</span>
        <span className="text-amber-300">{fmtNum(summary.hypotheses)} hypotheses</span>
      </div>

      {totalAnomalies === 0 && (
        <div data-testid="bi-diagnostics-empty" className="text-xs text-slate-400">
          No month is outside the screening band.
        </div>
      )}

      {series.map((s) => (
        <div key={s.series} data-testid={`bi-diagnostics-series-${s.series}`} className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="text-xs font-semibold text-slate-100">{s.label}</div>
              <div className="text-[11px] text-slate-400">
                {s.owner} · centre {formatValue(s.center, s.unit)} · band ±{formatValue(s.spread * s.threshold, s.unit)}
                {" · "}modified z threshold {s.threshold}
              </div>
            </div>
            <span className="text-[11px] text-slate-400">
              {fmtNum(s.anomalies.length)} of {fmtNum(s.points.length)} months flagged
            </span>
          </div>

          {s.anomalies.length === 0 && (
            <div className="text-[11px] text-slate-400">No anomaly in this series.</div>
          )}

          {s.anomalies.map((a) => (
            <div key={a.month} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="font-semibold text-slate-200">{monthLabel(a.month)}</span>
                <span className="font-mono text-slate-300">{formatValue(a.value, s.unit)}</span>
                <span className="text-slate-400">vs centre {formatValue(a.expected, s.unit)}</span>
                <span className={`px-1.5 py-0.5 rounded border font-semibold ${SEVERITY_STYLES[a.severity] || SEVERITY_STYLES.medium}`}>
                  {a.severity} · z {fmtNum(a.z, 2)} {a.direction}
                </span>
              </div>
              {(s.rca || []).filter((r) => r.month === a.month).map((r) => (
                <RcaCard key={`${r.series}-${r.month}`} rca={r} unit={s.unit}
                  canWrite={canWrite} onPropose={onPropose} busy={busy} />
              ))}
            </div>
          ))}

          {s.forecast && s.forecast.points && s.forecast.points.length > 0 && (
            <div data-testid={`bi-diagnostics-forecast-${s.series}`} className="text-[11px] text-slate-400">
              <span className="font-semibold text-slate-300">Projection</span>{" "}
              {s.forecast.points.map((p) => (
                <span key={p.month} className="mr-3 font-mono">
                  {monthLabel(p.month)} {formatValue(p.value, s.unit)}
                </span>
              ))}
              <span className="text-slate-500">({s.forecast.caveat})</span>
            </div>
          )}
        </div>
      ))}

      {data.protocol && (
        <div className="pt-2 border-t border-slate-800">
          <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">RCA protocol</div>
          <ul className="space-y-1">
            {data.protocol.map((rule) => (
              <li key={rule.id} className="text-[11px] text-slate-400">
                <span className="font-semibold text-slate-300">{rule.label}</span> — {rule.rule}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
