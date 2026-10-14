import React, { useCallback, useEffect, useState } from "react";
import { Send, RefreshCw, BadgeCheck, Scale, Sparkles } from "lucide-react";
import { api } from "../services/api";
import { fmtNum, fmtGbp, fmtGbpPlain, monthLabel } from "../lib/format";
import PlanChart from "./PlanChart";
import TelemetryCard from "./TelemetryCard";
import AllocationModal from "./AllocationModal";
import PhasingTable from "./PhasingTable";

export const FLOOR_REASON = "Order Book Floor Applied";

export const REASON_REQUIRED = "Reason code is required.";

const REASON_TEXT = {
  llm_not_configured: "The language model is not configured.",
  llm_error: "The language model could not be reached.",
};
const explainReason = (reasons) =>
  (reasons || []).map((r) => REASON_TEXT[r.split(":")[0]] || "The generated text failed a quality check.")[0] || null;

export default function DetailPanel({ itemId, reasonCodes, role, onChanged }) {
  const [detail, setDetail] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [showAllocation, setShowAllocation] = useState(false);
  const [explain, setExplain] = useState(null);
  const [explaining, setExplaining] = useState(false);
  const [explainError, setExplainError] = useState(null);
  const canWrite = role === "planner" || role === "admin";

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const d = await api.item(itemId);
      setDetail(d);
      const first = d.plan[0];
      setQty(String(d.decision ? d.decision.submitted_quantity : first ? first.final : ""));
    } catch (e) {
      setLoadError(e.message);
    }
  }, [itemId]);

  useEffect(() => {
    setDetail(null);
    setReason("");
    setComment("");
    setError(null);
    setMessage(null);
    setExplain(null);
    setExplainError(null);
    load();
  }, [load]);

  const askExplanation = async () => {
    setExplaining(true);
    setExplainError(null);
    try {
      setExplain(await api.narrative(itemId));
    } catch (e) {
      setExplainError(e.message);
    } finally {
      setExplaining(false);
    }
  };

  const send = async (payload) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.override({ item_id: itemId, ...payload });
      setMessage("Decision recorded in the audit ledger.");
      await load();
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e) => {
    e.preventDefault();
    if (!reason) return setError(REASON_REQUIRED);
    const n = Number(qty);
    if (qty === "" || Number.isNaN(n) || n < 0) return setError("Enter a quantity of zero or more.");
    send({ override_quantity: n, reason_code: reason, comment: comment || null });
  };

  const raiseToOrderBook = () => {
    send({ override_quantity: detail.phasing[0].order_book_floor, reason_code: FLOOR_REASON, comment: "Plan raised to the units already booked in the order book." });
  };

  const accept = () => {
    const first = detail.plan[0];
    send({ override_quantity: first.final, reason_code: reasonCodes.accept });
  };

  if (loadError) return <div className="text-rose-400 text-xs p-5">{loadError}</div>;
  if (!detail) return <div className="text-slate-500 text-xs p-5 flex items-center gap-2"><RefreshCw className="h-4 w-4 animate-spin" /> Loading item...</div>;

  const { item, plan, history, telemetry, decision } = detail;
  const phasing = detail.phasing || [];
  const floor = phasing.length ? phasing[0].order_book_floor : null;
  const first = plan[0];
  const decided = decision !== null;

  return (
    <div className="space-y-5">
      <div className="border-b border-slate-800 pb-4">
        <div className="flex justify-between items-start">
          <span className="text-[11px] font-bold text-indigo-400 uppercase tracking-wider">Causal exception analysis</span>
          <span className="text-xs font-mono text-slate-400">Price: {fmtGbpPlain(item.sales_price)} / unit</span>
        </div>
        <h3 className="text-base font-bold text-white mt-1">{item.product_id} <span className="text-slate-500 text-xs font-normal">item {item.item_id} | {item.channel}</span></h3>
        <p className="text-xs text-slate-300 mt-2 bg-slate-950 p-3 rounded-lg border border-slate-800 leading-relaxed">{item.narrative}</p>
        <button type="button" onClick={askExplanation} disabled={explaining}
          className="mt-2 text-xs text-indigo-300 hover:text-indigo-200 disabled:opacity-50 flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5" /> {explaining ? "Generating..." : "Explain with AI"}
        </button>
        {explainError && <p className="text-xs text-rose-400 mt-1">{explainError}</p>}
        {explain && (
          <div data-testid="ai-explanation" className="mt-2 text-xs text-slate-200 bg-indigo-950/40 border border-indigo-900 rounded-lg p-3 leading-relaxed">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] uppercase tracking-wider text-indigo-300">
                {explain.source === "llm" ? "AI-generated from the item telemetry" : "Rule-based text"}
              </span>
              {explain.source === "llm" && (
                <span className={(explain.cached ? "bg-emerald-900 text-emerald-200" : "bg-amber-900 text-amber-200") + " text-[10px] px-1.5 py-0.5 rounded"}>
                  {explain.cached ? "cached" : "new"}
                </span>
              )}
            </div>
            {explain.text}
            {explain.source !== "llm" && explainReason(explain.reasons) && (
              <span className="block text-[11px] text-slate-400 mt-1">{explainReason(explain.reasons)}</span>
            )}
          </div>
        )}
        {item.risk_flags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {item.risk_flags.map((f) => <span key={f} className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300">{f}</span>)}
          </div>
        )}
      </div>

      <TelemetryCard telemetry={telemetry} />
      <PlanChart history={history} plan={plan} />
      <PhasingTable phasing={phasing} />
      {floor && canWrite && !decision && (
        <div className="flex items-center justify-between gap-3 text-xs bg-amber-950/40 border border-amber-800 rounded-lg p-3 text-amber-200">
          <span>The order book already holds <strong>{fmtNum(floor)}</strong> units for {monthLabel(plan[0].month)}, above the plan of {fmtNum(plan[0].final)}.</span>
          <button type="button" onClick={raiseToOrderBook} disabled={busy}
            className="shrink-0 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white font-semibold rounded-lg">
            Raise plan to order book
          </button>
        </div>
      )}
      <button type="button" onClick={() => setShowAllocation(true)}
        className="text-xs text-indigo-300 hover:text-indigo-200 flex items-center gap-1.5">
        <Scale className="h-3.5 w-3.5" /> Simulate a supply shortage for this product
      </button>
      {showAllocation && <AllocationModal productId={item.product_id} onClose={() => setShowAllocation(false)} />}

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-slate-950 p-3 rounded-lg border border-slate-800">
          <span className="text-[11px] text-slate-400 block">Baseline ({monthLabel(first.month)})</span>
          <span className="text-lg font-bold font-mono text-slate-300 block">{fmtNum(first.baseline)} units</span>
          <span className="text-[10px] text-slate-500">Statistical baseline proxy</span>
        </div>
        <div className="bg-indigo-950/40 p-3 rounded-lg border border-indigo-800/60">
          <span className="text-[11px] text-indigo-400 block font-medium">ML plan ({monthLabel(first.month)})</span>
          <span className="text-lg font-bold font-mono text-indigo-200 block">{fmtNum(first.final)} units</span>
          <span className="text-[10px] text-emerald-400">{fmtGbp(item.value_delta_gbp)} vs baseline ({item.source})</span>
        </div>
      </div>

      {decided && (
        <div className="flex items-start gap-2 text-xs bg-emerald-950/40 border border-emerald-800 rounded-lg p-3 text-emerald-200" data-testid="audit-badge">
          <BadgeCheck className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            Audited: <strong>{fmtNum(decision.submitted_quantity)}</strong> units by {decision.planner_id} ({decision.reason_code}), impact {fmtGbp(decision.financial_impact_gbp)}.
          </span>
        </div>
      )}

      {canWrite ? (
        <form onSubmit={submit} className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-3" aria-label="Planner decision">
          <h4 className="text-xs font-bold text-white uppercase tracking-wider">Planner consensus decision (S&amp;OP audit)</h4>
          <div>
            <label htmlFor="qty" className="text-[11px] text-slate-400 block mb-1">Final quantity (units)</label>
            <input id="qty" type="number" min="0" value={qty} onChange={(e) => setQty(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label htmlFor="reason" className="text-[11px] text-slate-400 block mb-1">Reason code</label>
            <select id="reason" value={reason} onChange={(e) => setReason(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200 text-xs focus:outline-none focus:border-indigo-500">
              <option value="">Select reason...</option>
              {reasonCodes.codes.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="comment" className="text-[11px] text-slate-400 block mb-1">Operational rationale</label>
            <textarea id="comment" rows={2} maxLength={500} value={comment} onChange={(e) => setComment(e.target.value)}
              placeholder="Document the commercial context for the rolling FVA audit..."
              className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-slate-200 text-xs focus:outline-none focus:border-indigo-500" />
          </div>
          {error && <div role="alert" className="text-rose-400 text-xs">{error}</div>}
          {message && <div className="text-emerald-400 text-xs">{message}</div>}
          <div className="flex gap-2">
            <button type="submit" disabled={busy}
              className="flex-1 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-semibold text-xs rounded-lg flex items-center justify-center gap-2">
              {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Commit decision
            </button>
            <button type="button" onClick={accept} disabled={busy}
              className="px-4 py-2.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 border border-slate-700 text-xs font-semibold rounded-lg">
              Accept ML plan
            </button>
          </div>
        </form>
      ) : (
        <div className="text-xs text-slate-500">Read-only access (auditor).</div>
      )}
    </div>
  );
}
