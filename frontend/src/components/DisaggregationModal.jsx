import React, { useState } from "react";
import { X, Split } from "lucide-react";
import { api } from "../services/api";
import { fmtNum, monthLabel } from "../lib/format";
import { DataTable, ErrorNote } from "./ui";

const METHOD_LABEL = { mint_diag: "MinT (band variance)", proportional: "Proportional", ols: "Equal shift (OLS)" };

export default function DisaggregationModal({ productId, month, reasonCodes, canWrite, onClose, onApplied }) {
  const [total, setTotal] = useState("");
  const [method, setMethod] = useState("mint_diag");
  const [result, setResult] = useState(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState(null);
  const [notes, setNotes] = useState([]);
  const [busy, setBusy] = useState(false);

  const preview = async (e) => {
    e.preventDefault();
    setError(null);
    setNotes([]);
    const n = Number(total);
    if (total === "" || Number.isNaN(n) || n < 0) return setError("Enter a product total of zero or more.");
    try { setResult(await api.disaggregate({ product_id: productId, total: n, method, month })); } catch (err) { setError(err.message); }
  };

  const apply = async () => {
    if (!reason) return setError("Reason code is required.");
    setBusy(true);
    const out = [];
    for (const r of result.rows.filter((x) => Math.abs(x.change) >= 0.5)) {
      try {
        await api.override({ item_id: r.item_id, override_quantity: Math.round(r.proposed * 100) / 100, reason_code: reason,
          target_month: result.month, comment: `Disaggregated product total ${fmtNum(result.target_total)} (${METHOD_LABEL[result.method]}).` });
        out.push(`Item ${r.item_id}: recorded.`);
      } catch (err) {
        out.push(`Item ${r.item_id}: ${err.message}`);
      }
    }
    setNotes(out.length ? out : ["No item changes by half a unit or more; nothing recorded."]);
    setBusy(false);
    onApplied();
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4" data-testid="disaggregation-modal">
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-bold text-white flex items-center gap-2"><Split className="h-4 w-4 text-indigo-400" /> Disaggregate a product total</h3>
            <p className="text-[11px] text-slate-400 mt-1">{productId} | {monthLabel(month)}. Set the agreed product number; the gap is split across its channel items (F-SOP-01).</p>
          </div>
          <button data-testid="disaggregation-close-btn" onClick={onClose} className="text-slate-400 hover:text-white"><X className="h-4 w-4" /></button>
        </div>
        <form onSubmit={preview} className="flex flex-wrap gap-2 items-end">
          <label className="text-[11px] text-slate-400">Product total (units)
            <input data-testid="disaggregation-total-input" type="number" min="0" value={total} onChange={(e) => setTotal(e.target.value)}
              className="block mt-1 w-40 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono text-white" />
          </label>
          <label className="text-[11px] text-slate-400">Method
            <select data-testid="disaggregation-method-select" value={method} onChange={(e) => setMethod(e.target.value)}
              className="block mt-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200">
              {Object.entries(METHOD_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <button data-testid="disaggregation-preview-btn" type="submit" className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-semibold">Preview split</button>
        </form>
        <ErrorNote error={error} />
        {result && (
          <>
            <div className="text-xs text-slate-400">Current total {fmtNum(result.current_total)} to {fmtNum(result.target_total)} units.</div>
            <DataTable testId="disaggregation-table" rows={result.rows}
              columns={[{ key: "item_id", label: "Item" }, { key: "channel", label: "Channel" },
                { key: "current", label: "Current", right: true, render: (r) => fmtNum(r.current, 1) },
                { key: "sigma", label: "Sigma", right: true, render: (r) => fmtNum(r.sigma, 1) },
                { key: "proposed", label: "Proposed", right: true, render: (r) => fmtNum(r.proposed, 1) },
                { key: "change", label: "Change", right: true, render: (r) => `${r.change >= 0 ? "+" : ""}${fmtNum(r.change, 1)}` }]} />
            {canWrite && (
              <div className="flex gap-2 items-center">
                <select data-testid="disaggregation-reason-select" value={reason} onChange={(e) => setReason(e.target.value)}
                  className="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200">
                  <option value="">Select reason...</option>
                  {reasonCodes.codes.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <button data-testid="disaggregation-apply-btn" onClick={apply} disabled={busy}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-xs font-semibold">Record as decisions</button>
              </div>
            )}
          </>
        )}
        {notes.length > 0 && <ul data-testid="disaggregation-results" className="text-[11px] text-slate-300 space-y-0.5">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      </div>
    </div>
  );
}
