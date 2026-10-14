import React from "react";
import { monthLabel, fmtNum } from "../lib/format";

const W = 520;
const H = 150;
const PAD = { l: 36, r: 8, t: 8, b: 22 };

const SERIES = [
  { key: "actual", label: "Actual", color: "#94a3b8", dash: "" },
  { key: "baseline", label: "Baseline", color: "#64748b", dash: "4 3" },
  { key: "final", label: "ML plan", color: "#818cf8", dash: "" },
  { key: "consensus", label: "Consensus", color: "#34d399", dash: "2 2" },
];

export default function PlanChart({ history, plan }) {
  const months = [...history.map((h) => h.month), ...plan.map((p) => p.month)];
  if (months.length === 0) return null;
  const idx = Object.fromEntries(months.map((m, i) => [m, i]));
  const points = {
    actual: history.filter((h) => h.actual !== null).map((h) => [h.month, h.actual]),
    baseline: plan.map((p) => [p.month, p.baseline]),
    final: plan.map((p) => [p.month, p.final]),
    consensus: plan.map((p) => [p.month, p.consensus]),
  };
  const all = Object.values(points).flat().map((p) => p[1]).filter((v) => v !== null && v !== undefined);
  const max = Math.max(1, ...all);
  const x = (m) => PAD.l + (idx[m] / Math.max(1, months.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b);
  const path = (pts) => pts.map(([m, v], i) => `${i ? "L" : "M"}${x(m).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const ticks = months.filter((_, i) => i % Math.ceil(months.length / 6) === 0);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Actuals and plan by month">
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(max * t)} y2={y(max * t)} stroke="#1e293b" />
            <text x={PAD.l - 4} y={y(max * t) + 3} textAnchor="end" fontSize="9" fill="#64748b">{fmtNum(max * t)}</text>
          </g>
        ))}
        {ticks.map((m) => (
          <text key={m} x={x(m)} y={H - 6} textAnchor="middle" fontSize="9" fill="#64748b">{monthLabel(m)}</text>
        ))}
        {SERIES.map((s) => points[s.key].length > 0 && (
          <path key={s.key} d={path(points[s.key])} fill="none" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} />
        ))}
      </svg>
      <div className="flex gap-4 text-[10px] text-slate-400 mt-1">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5" style={{ background: s.color }} /> {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
