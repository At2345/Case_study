import React from "react";

export function Panel({ title, subtitle, right, children, testId, className = "" }) {
  return (
    <section data-testid={testId} className={`bg-slate-900 border border-slate-800 rounded-xl p-5 ${className}`}>
      {(title || right) && (
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            {title && <h2 className="text-sm font-bold text-white tracking-tight">{title}</h2>}
            {subtitle && <p className="text-[11px] text-slate-400 mt-0.5 max-w-2xl">{subtitle}</p>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone = "text-white", testId }) {
  return (
    <div data-testid={testId} className="bg-slate-950 border border-slate-800 rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className={`text-xl font-bold font-mono mt-1 ${tone}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export function DataTable({ columns, rows, testId, empty = "No rows." }) {
  if (!rows || rows.length === 0) return <div data-testid={testId} className="text-xs text-slate-500 py-6 text-center">{empty}</div>;
  return (
    <div className="overflow-x-auto" data-testid={testId}>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
            {columns.map((c) => <th key={c.key} className={`py-2 pr-3 font-medium ${c.right ? "text-right" : ""}`}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id || r.item_id + "-" + (r.month || i) || i} className="border-b border-slate-800/60 hover:bg-slate-800/30">
              {columns.map((c) => (
                <td key={c.key} className={`py-2 pr-3 ${c.right ? "text-right font-mono" : ""} text-slate-300`}>
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return <div role="alert" className="bg-rose-950/50 border border-rose-800 text-rose-200 rounded-lg px-4 py-3 text-xs">{error}</div>;
}
