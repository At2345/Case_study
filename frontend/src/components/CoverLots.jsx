import React, { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import { Panel } from "./ui";

const eur = (v) => `€${Number(v).toLocaleString("en-GB", { maximumFractionDigits: 2 })}`;
const STATUS_TONE = { OPEN: "text-sky-300", SOLD: "text-emerald-300", DONATED: "text-emerald-300" };
const input = "bg-slate-950 border border-slate-700 rounded px-1 py-0.5 text-[11px]";

// Recovery marketplace: lots of stock the platform paid claims on, buyer bids, and the admin award.
export default function CoverLots({ role, reloadKey, onError, onChanged }) {
  const canWrite = role === "planner" || role === "admin";
  const [lots, setLots] = useState([]);
  const [forms, setForms] = useState({});

  const load = useCallback(() => api.coverLots().then((r) => setLots(r.lots)).catch((e) => onError(e.message)), [onError]);
  useEffect(() => { load(); }, [load, reloadKey]);

  const form = (id) => forms[id] || { bidder: "", price: "", channel: "b2b" };
  const setField = (id, name, value) => setForms((f) => ({ ...f, [id]: { ...form(id), [name]: value } }));

  const bid = async (lot) => {
    const f = form(lot.id);
    try {
      await api.bidCoverLot(lot.id, { bidder: f.bidder || undefined, channel: f.channel,
        price_per_unit_eur: f.channel === "donation" ? 0 : Number(f.price) });
      setForms((all) => ({ ...all, [lot.id]: undefined }));
      load();
    } catch (e) {
      onError(e.message);
    }
  };

  const doAward = async (lot) => {
    try {
      await api.awardCoverLot(lot.id);
      load();
      onChanged && onChanged();
    } catch (e) {
      onError(e.message);
    }
  };

  return (
    <Panel title="Recovery lots" subtitle="Stock the platform paid claims on. Best bid at or above the reserve wins; donations keep goods out of destruction."
      testId="cover-lots">
      {lots.length === 0 && <div className="text-xs text-slate-400">No recovery lots yet. They open when a claim pays units.</div>}
      <ul className="space-y-3">
        {lots.map((lot) => {
          const f = form(lot.id);
          return (
            <li key={lot.id} data-testid={`cover-lot-${lot.id}`} className="text-xs border-b border-slate-800 pb-2">
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span className="font-semibold text-slate-200">{lot.po_id} · {lot.sku}</span>
                <span className={STATUS_TONE[lot.status] || "text-slate-300"}>{lot.status.toLowerCase()}</span>
                <span className="text-slate-400">{lot.units.toLocaleString("en-GB")} units · cost {eur(lot.cost_value_eur)}</span>
                <span className="text-slate-400">reserve {eur(lot.reserve_price_per_unit_eur)}/unit</span>
                {lot.best_bid && lot.status === "OPEN" && (
                  <span className="text-slate-200">best: {lot.best_bid.bidder} {eur(lot.best_bid.price_per_unit_eur)}/unit ({lot.best_bid.channel})</span>
                )}
                {lot.award && (
                  <span className="text-emerald-300">to {lot.award.bidder}: {eur(lot.award.proceeds_eur)} ({Math.round(lot.award.recovery_rate * 100)}% of cost)</span>
                )}
              </div>
              {lot.status === "OPEN" && canWrite && (
                <div className="flex flex-wrap gap-1 mt-1 items-center">
                  <input className={`${input} w-28`} placeholder="buyer" aria-label="Buyer" value={f.bidder}
                    data-testid={`cover-lot-bidder-${lot.id}`} onChange={(e) => setField(lot.id, "bidder", e.target.value)} />
                  <select className={input} aria-label="Channel" value={f.channel} data-testid={`cover-lot-channel-${lot.id}`}
                    onChange={(e) => setField(lot.id, "channel", e.target.value)}>
                    <option value="b2b">b2b</option><option value="outlet">outlet</option><option value="donation">donation</option>
                  </select>
                  {f.channel !== "donation" && (
                    <input className={`${input} w-20 font-mono`} placeholder="€/unit" aria-label="Price per unit" value={f.price}
                      data-testid={`cover-lot-price-${lot.id}`} onChange={(e) => setField(lot.id, "price", e.target.value)} />
                  )}
                  <button data-testid={`cover-lot-bid-${lot.id}`} onClick={() => bid(lot)}
                    className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded">Add bid</button>
                  {role === "admin" && lot.best_bid && (
                    <button data-testid={`cover-lot-award-${lot.id}`} onClick={() => doAward(lot)}
                      className="px-2 py-0.5 bg-emerald-700 hover:bg-emerald-600 rounded">Award best bid</button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
