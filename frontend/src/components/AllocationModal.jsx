import React, { useState } from "react";
import { X, Scale, RefreshCw } from "lucide-react";
import { api } from "../services/api";
import { fmtNum, fmtGbpPlain, fmtGbp, monthLabel } from "../lib/format";

const STRATEGIES = {
  margin_optimal: { label: "Margin-optimal (recommended)", note: "Fills the highest-value channels first after minimum fills" },
  priority_wholesale: { label: "Wholesale first", note: "Amazon, then B2B, then D2C" },
  proportional: { label: "Pro rata", note: "Same fill rate for every channel" },
};

export default function AllocationModal({ productId, onClose }) {
  const [supply, setSupply] = useState("");
  const [amazonFloor, setAmazonFloor] = useState("60");
  const [b2bFloor, setB2bFloor] = useState("0");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async (e) => {
    e.preventDefault();
    const units = Number(supply);
    if (supply === "" || Number.isNaN(units) || units < 0) return setError("Enter the available units (zero or more).");
    setBusy(true);
    setError(null);
    try {
      setResult(await api.allocate({
        product_id: productId, available_units: units,
        amazon_floor_pct: Number(amazonFloor) / 100, b2b_floor_pct: Number(b2bFloor) / 100,
      }));
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const channels = result ? result.scenarios[0].channels.map((c) => c.channel) : [];
  const input = "w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-indigo-500";

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Supply allocation simulator">
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[90vh] overflow-y-auto p-6 space-y-4">
        <div className="flex justify-between items-start">
          <div>
            <h2 className="text-base font-bold text-white flex items-center gap-2"><Scale className="h-5 w-5 text-indigo-400" /> Supply allocation simulator</h2>
            <p className="text-xs text-slate-400 mt-1">Product {productId}: how to split constrained supply across Amazon, B2B and D2C. Read-only what-if.</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-white"><X className="h-5 w-5" /></button>
        </div>

        <form onSubmit={run} className="grid grid-cols-3 gap-3 items-end">
          <div>
            <label htmlFor="supply" className="text-[11px] text-slate-400 block mb-1">Available units</label>
            <input id="supply" type="number" min="0" value={supply} onChange={(e) => setSupply(e.target.value)} className={input} />
          </div>
          <div>
            <label htmlFor="amzfloor" className="text-[11px] text-slate-400 block mb-1">Amazon minimum confirmation %</label>
            <input id="amzfloor" type="number" min="0" max="100" value={amazonFloor} onChange={(e) => setAmazonFloor(e.target.value)} className={input} />
          </div>
          <div>
            <label htmlFor="b2bfloor" className="text-[11px] text-slate-400 block mb-1">B2B minimum fill %</label>
            <input id="b2bfloor" type="number" min="0" max="100" value={b2bFloor} onChange={(e) => setB2bFloor(e.target.value)} className={input} />
          </div>
          <button type="submit" disabled={busy}
            className="col-span-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg flex items-center justify-center gap-2">
            {busy && <RefreshCw className="h-4 w-4 animate-spin" />} Simulate
          </button>
        </form>
        {error && <div role="alert" className="text-xs text-rose-400">{error}</div>}

        {result && (
          <div className="space-y-3">
            <p className="text-xs text-slate-300">
              {monthLabel(result.month)}: demand <strong>{fmtNum(result.total_demand)}</strong> units, supply <strong>{fmtNum(result.supply)}</strong>
              {result.shortage > 0 ? <>, shortage <strong className="text-rose-400">{fmtNum(result.shortage)}</strong></> : ", no shortage"}.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left">
                <thead className="text-slate-400 uppercase tracking-wider border-b border-slate-800">
                  <tr>
                    <th className="py-2 pr-3">Strategy</th>
                    {channels.map((c) => <th key={c} className="py-2 px-2 text-right">{c}</th>)}
                    <th className="py-2 px-2 text-right">Profit</th>
                    <th className="py-2 pl-2 text-right">vs best</th>
                  </tr>
                </thead>
                <tbody className="font-mono divide-y divide-slate-800">
                  {result.scenarios.map((s) => (
                    <tr key={s.strategy} data-testid={`scenario-${s.strategy}`} className={s.strategy === "margin_optimal" ? "bg-indigo-950/40" : ""}>
                      <td className="py-2 pr-3 font-sans">
                        <div className="text-white font-semibold">{STRATEGIES[s.strategy].label}</div>
                        <div className="text-[10px] text-slate-500">{STRATEGIES[s.strategy].note}</div>
                      </td>
                      {s.channels.map((c) => (
                        <td key={c.channel} className="py-2 px-2 text-right">
                          {fmtNum(c.allocated)} <span className="text-slate-500">({(c.fill_rate * 100).toFixed(0)}%)</span>
                        </td>
                      ))}
                      <td className="py-2 px-2 text-right text-white">{fmtGbpPlain(s.total_profit_gbp)}</td>
                      <td className={`py-2 pl-2 text-right ${s.profit_vs_best_gbp < -0.5 ? "text-rose-400" : "text-emerald-400"}`}>
                        {s.profit_vs_best_gbp < -0.5 ? fmtGbp(s.profit_vs_best_gbp) : "best"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[11px] text-slate-500">
              Assumptions (editable in the API): Amazon chargeback {(result.assumptions.chargeback_pct * 100).toFixed(0)}% of COGS per unmet unit,
              B2B OTIF fine {(result.assumptions.otif_penalty_pct * 100).toFixed(0)}% of sales value per unmet unit. These are business assumptions, not data.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
