import React from "react";
import { HelpCircle } from "lucide-react";
import { fmtNum, fmtPct } from "../lib/format";

const ROWS = [
  ["glance_mom_pct", "14-day traffic trend", (v) => fmtPct(v, 0)],
  ["p70_gap_pct", "Amazon p70 vs sell-out", (v) => fmtPct(v, 0)],
  ["on_hand", "Amazon stock on hand", (v) => `${fmtNum(v)} units`],
  ["open_po", "Open POs", (v) => `${fmtNum(v)} units`],
  ["oos_rate_28d", "Out-of-stock rate (28d)", (v) => `${(v * 100).toFixed(1)}%`],
  ["weeks_of_cover", "Weeks of cover", (v) => fmtNum(v, 1)],
];

export default function TelemetryCard({ telemetry }) {
  const rows = ROWS.filter(([k]) => telemetry && telemetry[k] !== null && telemetry[k] !== undefined);
  if (rows.length === 0) return null;
  return (
    <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 text-xs space-y-2">
      <div className="font-semibold text-slate-300 text-[11px] uppercase tracking-wider flex items-center gap-1.5">
        <HelpCircle className="h-3.5 w-3.5 text-indigo-400" /> Marketplace telemetry
      </div>
      <div className="grid grid-cols-2 gap-2 text-slate-400">
        {rows.map(([k, label, f]) => (
          <div key={k}>{label}: <span className="font-mono text-white">{f(telemetry[k])}</span></div>
        ))}
      </div>
    </div>
  );
}
