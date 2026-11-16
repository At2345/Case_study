import React from "react";
import { fmtNum } from "../lib/format";
import { IBCS, seriesStyle } from "../lib/ibcs";

export const PALETTE = ["#818cf8", "#22d3ee", "#fbbf24", "#fb7185", "#a3e635", "#c084fc", "#94a3b8", "#f472b6"];

const Empty = ({ testId }) => (
  <div data-testid={testId} className="h-32 flex items-center justify-center text-[11px] text-slate-400">No data</div>
);

function SrTable({ caption, rows, valueLabel = "Value", format = fmtNum }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr><th scope="col">Category</th><th scope="col">{valueLabel}</th></tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.label}-${i}`}><th scope="row">{r.label}</th><td>{format(r.value)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function BarChart({ data, height = 180, color = IBCS.actual, format = fmtNum, testId, label = "Bar chart" }) {
  const rows = (data || []).map((d) => ({ label: String(d.label ?? ""), value: Number(d.value) || 0 }));
  if (!rows.length) return <Empty testId={testId} />;
  const W = 320, H = height, padL = 6, padR = 6, padT = 18, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(...rows.map((r) => r.value), 1);
  const step = plotW / rows.length;
  const barW = Math.min(step * 0.62, 34);
  return (
    <div data-testid={testId}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={label}>
        <title>{label}</title>
        <line x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH} stroke={IBCS.grid} />
        {rows.map((r, i) => {
          const h = (r.value / max) * plotH;
          const x = padL + step * i + (step - barW) / 2;
          const y = padT + plotH - h;
          return (
            <g key={`${r.label}-${i}`}>
              <rect x={x} y={y} width={barW} height={Math.max(h, 1)} rx="2" fill={color} opacity="0.9" />
              <text x={x + barW / 2} y={y - 4} textAnchor="middle" fontSize="8" fill={IBCS.valueText}>{format(r.value)}</text>
              <text x={x + barW / 2} y={H - 8} textAnchor="middle" fontSize="7.5" fill={IBCS.axisText}>{r.label}</text>
            </g>
          );
        })}
      </svg>
      <SrTable caption={label} rows={rows} />
    </div>
  );
}

export function HBarList({ data, format = fmtNum, testId, color = IBCS.actual, label = "Horizontal bar chart" }) {
  const rows = (data || []).map((d) => ({ label: String(d.label ?? ""), value: Number(d.value) || 0 }));
  if (!rows.length) return <Empty testId={testId} />;
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1);
  return (
    <div data-testid={testId} className="space-y-2">
      {rows.map((r, i) => {
        const negative = r.value < 0;
        const pct = (Math.abs(r.value) / max) * 100;
        return (
          <div key={`${r.label}-${i}`} className="flex items-center gap-2 text-[10px]">
            <span className="w-20 shrink-0 text-right font-mono text-slate-300">{negative ? format(r.value) : ""}</span>
            <span className="w-24 shrink-0 truncate text-slate-300" title={r.label}>{r.label}</span>
            <div className="relative flex-1 h-2 bg-slate-800 rounded">
              <div className="absolute top-0 h-2 rounded"
                style={{ width: `${pct}%`, left: negative ? `${100 - pct}%` : 0, background: color }} />
            </div>
            <span className="w-20 shrink-0 font-mono text-slate-300">{negative ? "" : format(r.value)}</span>
          </div>
        );
      })}
      <SrTable caption={label} rows={rows} />
    </div>
  );
}

export function LineChart({ series, labels, height = 200, testId, format = fmtNum, label = "Line chart" }) {
  const rows = (series || []).filter((s) => s && s.values && s.values.length);
  if (!rows.length || !labels || !labels.length) return <Empty testId={testId} />;
  const styled = rows.map((s) => ({ name: s.name, values: s.values, ...seriesStyle(s) }));
  const W = 640, H = height, padL = 52, padR = 30, padT = 16, padB = 28;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const all = styled.flatMap((s) => s.values.map((v) => Number(v) || 0));
  const max = Math.max(...all, 1);
  const n = labels.length;
  const x = (i) => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v) => padT + plotH - (v / max) * plotH;
  const primary = styled[0].values.map((v) => Number(v) || 0);
  const peak = primary.indexOf(Math.max(...primary));
  const valley = primary.indexOf(Math.min(...primary));
  return (
    <div data-testid={testId}>
      <div className="flex flex-wrap gap-3 mb-2">
        {styled.map((s) => (
          <span key={s.name} className="flex items-center gap-1.5 text-[11px] text-slate-300">
            <svg width="18" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="18" y2="4" stroke={s.stroke} strokeWidth="2" strokeDasharray={s.dash || undefined} />
            </svg>
            {s.name}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxWidth: 900 }} role="img" aria-label={label}>
        <title>{label}</title>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={padL} y1={padT + plotH * f} x2={W - padR} y2={padT + plotH * f}
              stroke={IBCS.grid} strokeDasharray="3 3" />
            <text x={padL - 6} y={padT + plotH * f + 4} textAnchor="end" fontSize="11" fill={IBCS.axisText}>
              {format(max * (1 - f))}
            </text>
          </g>
        ))}
        {styled.map((s) => (
          <polyline key={s.name} fill="none" stroke={s.stroke} strokeWidth="2" strokeDasharray={s.dash || undefined}
            points={s.values.map((v, i) => `${x(i)},${y(Number(v) || 0)}`).join(" ")} />
        ))}
        {peak !== valley && (
          <>
            <text x={x(peak)} y={y(primary[peak]) - 6} textAnchor="middle" fontSize="12" fill={IBCS.valueText}>{format(primary[peak])}</text>
            <text x={Math.max(x(valley), padL + 18)} y={y(primary[valley]) + 14} textAnchor="middle" fontSize="12" fill={IBCS.valueText}>{format(primary[valley])}</text>
          </>
        )}
        {labels.map((l, i) => (
          <text key={`${l}-${i}`} x={x(i)} y={H - 8} textAnchor="middle" fontSize="11" fill={IBCS.axisText}>{l}</text>
        ))}
      </svg>
      <SrTable caption={label} rows={labels.map((l, i) => ({ label: l, value: primary[i] }))} />
    </div>
  );
}

export function DonutChart({ data, testId, size = 150, label = "Donut chart" }) {
  const rows = (data || [])
    .map((d, i) => ({ label: String(d.label ?? ""), value: Number(d.value) || 0, color: d.color || PALETTE[i % PALETTE.length] }))
    .filter((d) => d.value > 0);
  if (!rows.length) return <Empty testId={testId} />;
  const total = rows.reduce((s, d) => s + d.value, 0);
  const r = 42, c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div data-testid={testId} className="flex items-center gap-4 flex-wrap">
      <svg viewBox="0 0 120 120" style={{ width: size, height: size }} role="img" aria-label={label}>
        <title>{label}</title>
        <circle cx="60" cy="60" r={r} fill="none" stroke={IBCS.grid} strokeWidth="16" />
        {rows.map((d) => {
          const len = (d.value / total) * c;
          const el = (
            <circle key={d.label} cx="60" cy="60" r={r} fill="none" stroke={d.color} strokeWidth="16"
              strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} transform="rotate(-90 60 60)" />
          );
          offset += len;
          return el;
        })}
      </svg>
      <ul className="space-y-1 text-[11px]">
        {rows.map((d) => (
          <li key={d.label} className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ background: d.color }} aria-hidden="true" />
            <span className="text-slate-300">{d.label}</span>
            <span className="font-mono text-slate-300">{fmtNum(d.value)}</span>
            <span className="text-slate-400">({Math.round((d.value / total) * 100)}%)</span>
          </li>
        ))}
      </ul>
      <SrTable caption={label} rows={rows} />
    </div>
  );
}
