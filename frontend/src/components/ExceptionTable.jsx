import React from "react";
import { BarChart3, CheckCircle2 } from "lucide-react";
import { fmtNum, fmtGbp, RISK_LABELS, RISK_STYLES } from "../lib/format";

const STATES = [
  { value: "EXCEPTION_REVIEW", label: "Exceptions" },
  { value: "PLANNER_LED", label: "Planner-led" },
];
const CHANNELS = ["", "amazon", "B2B wo Amazon", "D2C"];
const RISKS = ["", ...Object.keys(RISK_LABELS)];

export default function ExceptionTable({ data, filters, onFilters, selectedId, onSelect, loading }) {
  const set = (patch) => onFilters({ ...filters, ...patch, offset: 0 });
  const select = "bg-slate-950 border border-slate-700 rounded-md px-2 py-1 text-xs text-slate-200";
  const items = data ? data.items : [];
  return (
    <section className="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-xl flex flex-col overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-800 bg-slate-900/90 space-y-3">
        <div className="flex justify-between items-center">
          <div>
            <h2 className="font-semibold text-white text-sm flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-indigo-400" /> Prioritised action queue (ranked by financial exposure)
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">Select an item to see the drivers and record a decision.</p>
          </div>
          <span className="text-xs bg-indigo-950 text-indigo-300 border border-indigo-800/80 px-2.5 py-1 rounded-full font-medium">
            {data ? data.total : 0} items
          </span>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <div className="flex rounded-md overflow-hidden border border-slate-700">
            {STATES.map((s) => (
              <button key={s.value} onClick={() => set({ state: s.value })}
                className={`px-3 py-1 text-xs ${filters.state === s.value ? "bg-indigo-600 text-white" : "bg-slate-950 text-slate-300"}`}>
                {s.label}
              </button>
            ))}
          </div>
          <select aria-label="Channel filter" className={select} value={filters.channel || ""} onChange={(e) => set({ channel: e.target.value })}>
            {CHANNELS.map((c) => <option key={c} value={c}>{c || "All channels"}</option>)}
          </select>
          <select aria-label="Risk filter" className={select} value={filters.risk_type || ""} onChange={(e) => set({ risk_type: e.target.value })}>
            {RISKS.map((r) => <option key={r} value={r}>{RISK_LABELS[r] || "All risks"}</option>)}
          </select>
          <label className="text-xs text-slate-300 flex items-center gap-1">
            <input type="checkbox" checked={filters.pending === "true"} onChange={(e) => set({ pending: e.target.checked ? "true" : "" })} />
            Pending only
          </label>
        </div>
      </div>
      <div className="overflow-x-auto flex-1">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-950/80 text-slate-400 border-b border-slate-800 uppercase tracking-wider">
            <tr>
              <th className="py-3 px-4">Item</th>
              <th className="py-3 px-3">Channel</th>
              <th className="py-3 px-3 text-right">Baseline</th>
              <th className="py-3 px-3 text-right">ML plan</th>
              <th className="py-3 px-3 text-right">Final</th>
              <th className="py-3 px-3 text-right">Exposure</th>
              <th className="py-3 px-3">Risk</th>
              <th className="py-3 px-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono">
            {items.map((it) => (
              <tr key={it.item_id} onClick={() => onSelect(it.item_id)} data-testid={`row-${it.item_id}`}
                className={`cursor-pointer transition-colors ${selectedId === it.item_id ? "bg-indigo-950/40" : "hover:bg-slate-800/40"}`}>
                <td className="py-3 px-4 font-sans">
                  <div className="font-semibold text-white">{it.product_id}</div>
                  <div className="text-[11px] text-slate-400 font-mono">Item {it.item_id} | Class {it.abc_category || "-"}</div>
                </td>
                <td className="py-3 px-3 font-sans">
                  <span className="text-[10px] px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300 uppercase">{it.channel}</span>
                </td>
                <td className="py-3 px-3 text-right text-slate-400">{fmtNum(it.baseline_forecast)}</td>
                <td className="py-3 px-3 text-right text-indigo-300 font-bold">{fmtNum(it.ml_forecast)}</td>
                <td className="py-3 px-3 text-right font-bold text-white">{fmtNum(it.current_final)}</td>
                <td className={`py-3 px-3 text-right font-medium ${it.value_delta_gbp >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {fmtGbp(it.value_delta_gbp)}
                </td>
                <td className="py-3 px-3 font-sans">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold border ${RISK_STYLES[it.risk_type] || RISK_STYLES.OVERSTOCK_RISK}`}>
                    {RISK_LABELS[it.risk_type] || it.risk_type}
                  </span>
                </td>
                <td className="py-3 px-3 font-sans">
                  {it.has_override ? (
                    <span className="text-emerald-400 flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" /> Decided</span>
                  ) : (
                    <span className="text-slate-500">Pending</span>
                  )}
                </td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr><td colSpan={8} className="py-8 text-center text-slate-500 font-sans">No items match the filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {data && data.total > filters.limit && (
        <div className="px-5 py-3 border-t border-slate-800 flex justify-between text-xs text-slate-400">
          <button disabled={filters.offset === 0} onClick={() => onFilters({ ...filters, offset: Math.max(0, filters.offset - filters.limit) })} className="disabled:opacity-40">Previous</button>
          <span>{filters.offset + 1}-{Math.min(filters.offset + filters.limit, data.total)} of {data.total}</span>
          <button disabled={filters.offset + filters.limit >= data.total} onClick={() => onFilters({ ...filters, offset: filters.offset + filters.limit })} className="disabled:opacity-40">Next</button>
        </div>
      )}
    </section>
  );
}
