import React from "react";
import { fmtGbpPlain, fmtNum } from "../lib/format";

const HEALTH_STYLES = {
  healthy: "bg-emerald-950 text-emerald-300 border-emerald-800",
  watch: "bg-amber-950 text-amber-300 border-amber-800",
  breach: "bg-rose-950 text-rose-300 border-rose-800",
  unknown: "bg-slate-800 text-slate-300 border-slate-700",
};

const HEALTH_LABELS = { healthy: "On target", watch: "Watch", breach: "Breach", unknown: "No data" };

const HEALTH_DOTS = {
  healthy: "bg-emerald-400",
  watch: "bg-amber-400",
  breach: "bg-rose-400",
  unknown: "bg-slate-500",
};

export const formatValue = (value, unit) => {
  if (value === null || value === undefined) return "-";
  if (unit === "share") return `${(value * 100).toFixed(1)}%`;
  if (unit === "gbp") return fmtGbpPlain(value);
  if (unit === "pts") return fmtNum(value, 2);
  if (unit === "weeks") return `${fmtNum(value, 1)} w`;
  if (unit === "per100") return fmtNum(value, 2);
  return fmtNum(value);
};

const corridorLabel = (corridor, unit) => {
  if (!corridor) return "";
  const fmt = (v) => formatValue(v, unit);
  if (corridor.min !== undefined && corridor.max !== undefined) {
    return `target ${fmt(corridor.min)} to ${fmt(corridor.max)}`;
  }
  if (corridor.min !== undefined) return `target at least ${fmt(corridor.min)}`;
  if (corridor.max !== undefined) return `target at most ${fmt(corridor.max)}`;
  return "";
};

function HealthPill({ health }) {
  return (
    <span className={`px-2 py-0.5 rounded border text-[10px] font-semibold ${HEALTH_STYLES[health] || HEALTH_STYLES.unknown}`}>
      {HEALTH_LABELS[health] || health}
    </span>
  );
}

export default function KpiTree({ tree }) {
  if (!tree || !tree.root) {
    return <div className="text-xs text-slate-400">No KPI tree available.</div>;
  }
  const root = tree.root;
  return (
    <div data-testid="bi-kpi-tree" className="space-y-4">
      <div data-testid="bi-kpi-root" className="rounded-lg border border-slate-700 bg-slate-950 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400">Root KPI</div>
            <div className="text-sm font-bold text-white">{root.label}</div>
            <div className="text-[11px] text-slate-400">
              {root.owner} · confidence {root.confidence} · {corridorLabel(root.corridor, root.unit)}
            </div>
          </div>
          <div className="text-right space-y-1">
            <div className="text-2xl font-bold font-mono text-white">{formatValue(root.value, root.unit)}</div>
            <HealthPill health={root.health} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {root.branches.map((branch) => (
          <div key={branch.id} data-testid={`bi-kpi-branch-${branch.id}`}
            className="rounded-lg border border-slate-800 bg-slate-950 p-3 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-400">Branch</div>
                <div className="text-xs font-semibold text-slate-100">{branch.label}</div>
              </div>
              <HealthPill health={branch.health} />
            </div>
            <div className="text-lg font-bold font-mono text-white">{formatValue(branch.value, branch.unit)}</div>
            <div className="text-[10px] text-slate-400">
              {corridorLabel(branch.corridor, branch.unit)} · {branch.owner}
            </div>
            <ul className="space-y-1.5 pt-2 border-t border-slate-800">
              {branch.leaves.map((leaf) => (
                <li key={leaf.id} data-testid={`bi-kpi-leaf-${leaf.id}`}
                  className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="text-slate-300 truncate" title={`${leaf.owner} · confidence ${leaf.confidence}`}>
                    {leaf.label}
                  </span>
                  <span className="flex items-center gap-1.5 shrink-0">
                    <span className="font-mono text-slate-200">{formatValue(leaf.value, leaf.unit)}</span>
                    <span className={`h-2 w-2 rounded-full ${HEALTH_DOTS[leaf.health] || HEALTH_DOTS.unknown}`}
                      aria-hidden="true" />
                    <span className="sr-only">{HEALTH_LABELS[leaf.health] || leaf.health}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
