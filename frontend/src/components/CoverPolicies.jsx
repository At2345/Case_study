import React, { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import { Panel, Stat } from "./ui";

const STATUS_TONE = {
  ACTIVE: "text-sky-300", AWAITING_SETTLEMENT: "text-amber-300", CLAIMED: "text-rose-300",
  CLOSED_NO_CLAIM: "text-emerald-300", CANCELLED: "text-slate-400",
};
const eur = (v) => (v === null || v === undefined ? "—" : `€${Number(v).toLocaleString("en-GB", { maximumFractionDigits: 2 })}`);
const input = "w-20 bg-slate-950 border border-slate-700 rounded px-1 py-0.5 text-[11px] font-mono";

// Pilot cover policies: portfolio figures, sell-through monitoring and settlement.
export default function CoverPolicies({ role, reloadKey, onError, onChanged }) {
  const canWrite = role === "planner" || role === "admin";
  const [policies, setPolicies] = useState([]);
  const [book, setBook] = useState(null);
  const [forms, setForms] = useState({});
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const [p, b] = await Promise.all([api.coverPolicies(), api.coverPortfolio()]);
      setPolicies(p.policies);
      setBook(b);
    } catch (e) {
      onError(e.message);
    }
  }, [onError]);
  useEffect(() => { load(); }, [load, reloadKey]);

  const setField = (id, name, value) => setForms((f) => ({ ...f, [id]: { ...(f[id] || {}), [name]: value } }));

  const observe = async (p) => {
    const f = forms[p.id] || {};
    try {
      const r = await api.observeCoverPolicy(p.id, { units_sold_to_date: Number(f.sold), units_on_hand: Number(f.onHand) });
      setMessage(r.rescue.started
        ? `Sell-through ${(r.assessment.gap * 100).toFixed(0)}% vs plan: rescue playbook started (run ${r.rescue.run_id}).`
        : `Sales recorded (${(r.assessment.gap * 100).toFixed(0)}% vs plan). No rescue: ${r.rescue.reason}.`);
      load();
    } catch (e) {
      onError(e.message);
    }
  };

  const doSettle = async (p) => {
    const f = forms[p.id] || {};
    try {
      const r = await api.settleCoverPolicy(p.id, { units_unsold: Number(f.unsold) });
      setMessage(`Policy ${p.po_id} settled: ${r.status === "CLAIMED" ? `pays ${eur(r.claim.payout_eur)}` : "no claim"}.`
        + (r.claim && r.claim.lot_id ? " A recovery lot is open for the paid units." : ""));
      if (onChanged) onChanged(); else load();
    } catch (e) {
      onError(e.message);
    }
  };

  return (
    <Panel title="Cover policies (pilot)" subtitle="Bound quotes. Sales snapshots compare sell-through with plan and start the linked rescue playbook when it falls behind."
      testId="cover-policies">
      {book && (
        <div className="grid grid-cols-2 md:grid-cols-7 gap-3 mb-4" data-testid="cover-portfolio">
          <Stat label="Policies" value={book.policies} />
          <Stat label="Premium written" value={eur(book.premium_written_eur)} />
          <Stat label="Claims paid" value={eur(book.claims_paid_eur)} />
          <Stat label="Recovered" testId="cover-recovered" value={eur(book.recovered_eur || 0)}
            sub={book.net_loss_ratio === null || book.net_loss_ratio === undefined ? "" : `net loss ratio ${Math.round(book.net_loss_ratio * 100)}%`} />
          <Stat label="Loss ratio" value={book.loss_ratio === null ? "—" : `${Math.round(book.loss_ratio * 100)}%`} />
          <Stat label="Open max exposure" value={eur(book.open_max_exposure_eur)} sub={`${book.rescue_runs} rescue runs`} />
          <Stat label="Actual / expected" testId="cover-ae"
            value={book.experience && book.experience.a_to_e !== null ? book.experience.a_to_e : "—"}
            sub={book.experience ? `pricing ×${book.experience.factor} · ${Math.round(book.experience.credibility * 100)}% credible` : ""} />
        </div>
      )}
      {message && <div className="text-xs text-emerald-200 mb-3" data-testid="cover-policy-message">{message}</div>}
      {policies.length === 0 && <div className="text-xs text-slate-400">No policies yet. Bind an offered quote above.</div>}
      <ul className="space-y-3">
        {policies.map((p) => {
          const last = p.last_observation;
          return (
            <li key={p.id} data-testid={`cover-policy-${p.id}`} className="text-xs border-b border-slate-800 pb-2">
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span className="font-semibold text-slate-200">{p.po_id}</span>
                <span className={STATUS_TONE[p.effective_status]}>{p.effective_status.replace(/_/g, " ").toLowerCase()}</span>
                <span className="text-slate-400">{p.covered_units.toLocaleString("en-GB")} covered · premium {eur(p.premium_eur)}</span>
                <span className="text-slate-400">ends {p.ends_at.slice(0, 10)}</span>
                {p.rescue_playbook_id && <span className="text-slate-400">rescue: {p.rescue_playbook_id} ({p.rescue_runs.length} runs)</span>}
                {last && <span className={last.gap < p.rescue_gap_threshold ? "text-rose-300" : "text-slate-300"}>
                  last: {last.units_sold_to_date} sold, {(last.gap * 100).toFixed(0)}% vs {last.curve === "seasonal" ? "seasonal " : ""}plan</span>}
                {p.claim && <span className="text-slate-200">claim {eur(p.claim.payout_eur)} ({p.claim.paid_units} units)</span>}
              </div>
              {canWrite && p.effective_status === "ACTIVE" && (
                <div className="flex gap-1 mt-1 items-center">
                  <input className={input} placeholder="sold" aria-label="Units sold to date" data-testid={`cover-sold-${p.id}`}
                    onChange={(e) => setField(p.id, "sold", e.target.value)} />
                  <input className={input} placeholder="on hand" aria-label="Units on hand" data-testid={`cover-onhand-${p.id}`}
                    onChange={(e) => setField(p.id, "onHand", e.target.value)} />
                  <button data-testid={`cover-observe-${p.id}`} onClick={() => observe(p)}
                    className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded">Record sales</button>
                </div>
              )}
              {role === "admin" && p.effective_status === "AWAITING_SETTLEMENT" && (
                <div className="flex gap-1 mt-1 items-center">
                  <input className={input} placeholder="unsold" aria-label="Final units unsold" data-testid={`cover-unsold-${p.id}`}
                    onChange={(e) => setField(p.id, "unsold", e.target.value)} />
                  <button data-testid={`cover-settle-${p.id}`} onClick={() => doSettle(p)}
                    className="px-2 py-0.5 bg-amber-700 hover:bg-amber-600 rounded">Settle</button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
