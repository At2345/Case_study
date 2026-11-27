import React, { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api } from "../services/api";
import { DataTable, ErrorNote, Panel, Stat } from "../components/ui";
import CoverPolicies from "../components/CoverPolicies";
import CoverLots from "../components/CoverLots";

const GRADE_TONE = { A: "text-emerald-300", B: "text-emerald-300", C: "text-amber-300", D: "text-orange-300", E: "text-rose-300" };
const EMPTY = { po_id: "", sku: "", units: "", unit_landed_cost_eur: "", item_id: "", demand_mean_units: "", demand_sd_units: "" };
const eur = (v) => (v === null || v === undefined ? "—" : `€${Number(v).toLocaleString("en-GB", { maximumFractionDigits: 2 })}`);
const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function buildBody(form, source) {
  const num = (v) => (v === "" ? undefined : Number(v));
  const body = { po_id: form.po_id.trim(), sku: form.sku.trim() || undefined, units: num(form.units),
    unit_landed_cost_eur: num(form.unit_landed_cost_eur) };
  if (source === "forecast") body.item_id = num(form.item_id);
  else Object.assign(body, { demand_mean_units: num(form.demand_mean_units), demand_sd_units: num(form.demand_sd_units) });
  return body;
}

export default function CoverPage({ role }) {
  const canWrite = role === "planner" || role === "admin";
  const [form, setForm] = useState(EMPTY);
  const [source, setSource] = useState("forecast");
  const [quote, setQuote] = useState(null);
  const [quotes, setQuotes] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const [playbooks, setPlaybooks] = useState([]);
  const [bind, setBind] = useState({ marketplace: "DE", rescue_playbook_id: "" });
  const [bound, setBound] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  const refresh = useCallback(() => api.coverQuotes().then((r) => setQuotes(r.quotes)).catch((e) => setError(e.message)), []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (canWrite) api.workflows().then((r) => setPlaybooks(r.workflows)).catch(() => setPlaybooks([]));
  }, [canWrite]);

  const bindQuote = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await api.bindCoverQuote(quote.id, { marketplace: bind.marketplace || null,
        rescue_playbook_id: bind.rescue_playbook_id || null });
      setBound(p);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const field = (name, label, props = {}) => (
    <label className="block" key={name}>
      <span className="text-[10px] uppercase tracking-wider text-slate-400">{label}</span>
      <input data-testid={`cover-${name}`} value={form[name]} onChange={(e) => setForm((f) => ({ ...f, [name]: e.target.value }))}
        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs font-mono" {...props} />
    </label>
  );

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      setQuote(await api.coverQuote(buildBody(form, source)));
      setBound(null);
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex-1 px-8 py-6 space-y-6" data-testid="cover-page">
      <ErrorNote error={error} />
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Panel title="Quote overstock cover" subtitle="Indicative price for a purchase order, from our own demand forecast. Not binding."
          className="xl:col-span-4">
          {!canWrite && <div className="text-xs text-slate-400">Planners and admins can request quotes.</div>}
          {canWrite && (
            <div className="space-y-3">
              {field("po_id", "Purchase order")}
              {field("sku", "SKU (optional)")}
              <div className="grid grid-cols-2 gap-2">
                {field("units", "Units ordered", { inputMode: "numeric" })}
                {field("unit_landed_cost_eur", "Landed cost / unit (€)", { inputMode: "decimal" })}
              </div>
              <div className="flex gap-3 text-xs" role="radiogroup" aria-label="Demand source">
                {[["forecast", "Platform forecast"], ["manual", "Manual demand"]].map(([v, l]) => (
                  <label key={v} className="flex items-center gap-1">
                    <input type="radio" name="source" data-testid={`cover-source-${v}`} checked={source === v} onChange={() => setSource(v)} /> {l}
                  </label>
                ))}
              </div>
              {source === "forecast" ? field("item_id", "Item id", { inputMode: "numeric" }) : (
                <div className="grid grid-cols-2 gap-2">
                  {field("demand_mean_units", "Demand, 180 days")}
                  {field("demand_sd_units", "Uncertainty (sd)")}
                </div>
              )}
              <button data-testid="cover-submit" onClick={submit} disabled={busy || !form.po_id || !form.units || !form.unit_landed_cost_eur}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold rounded-lg flex items-center gap-2">
                <ShieldCheck className="h-4 w-4" /> Get quote
              </button>
            </div>
          )}
        </Panel>

        <Panel title={quote ? `Quote for ${quote.po_id}` : "Quote"} className="xl:col-span-8" testId="cover-result">
          {!quote && <div className="text-xs text-slate-400">Fill in a purchase order to see the price and the recommended quantity.</div>}
          {quote && (
            <div className="space-y-4 text-xs">
              <div className="flex items-center gap-3">
                <span className={`text-3xl font-bold ${GRADE_TONE[quote.risk_grade]}`} data-testid="cover-grade">{quote.risk_grade}</span>
                <span data-testid="cover-verdict" className={quote.offered ? "text-emerald-300" : "text-rose-300"}>
                  {quote.offered ? `Cover offered: ${eur(quote.premium_eur)} (${pct(quote.premium_rate)} of covered value)` : "Not offered"}
                </span>
              </div>
              {quote.decline_reasons.length > 0 && <div className="text-rose-200">{quote.decline_reasons.join("; ")}</div>}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Stat label="Recommended qty" value={quote.recommended_units.toLocaleString("en-GB")} testId="cover-recommended" />
                <Stat label="Covered units" value={quote.covered_units.toLocaleString("en-GB")}
                  sub={quote.excluded_units > 0 ? `${quote.excluded_units.toLocaleString("en-GB")} above the cap stay with you` : "whole order"} />
                <Stat label="Expected unsold" value={quote.expected_unsold_units.toLocaleString("en-GB")} sub={pct(quote.expected_unsold_share)} />
                <Stat label="Chance of a claim" value={pct(quote.claim_probability)} />
              </div>
              <div className="text-slate-400">
                Pays {eur(quote.payout_per_unit_eur)} per unsold unit after day {quote.terms.horizon_days}, beyond the first{" "}
                {quote.deductible_units.toLocaleString("en-GB")} units (excess). Demand {quote.demand.mean_units.toLocaleString("en-GB")}
                {" "}± {quote.demand.sd_units.toLocaleString("en-GB")} units ({quote.demand.source}). Valid until {quote.valid_until.slice(0, 10)}.
              </div>
              {quote.experience && (
                <div className="text-slate-400" data-testid="cover-experience">
                  {quote.experience.policies > 0
                    ? `Experience factor ×${quote.experience.factor} from ${quote.experience.policies} settled `
                      + `${quote.experience.policies === 1 ? "policy" : "policies"} (actual/expected ${quote.experience.a_to_e}, `
                      + `credibility ${Math.round(quote.experience.credibility * 100)}%)`
                      + (quote.experience.complement_source === "pooled"
                        ? `, the rest from the market benchmark of ${quote.experience.pooled_tenants} companies.` : ".")
                    : quote.experience.complement_source === "pooled"
                      ? `No settled policies yet: priced on the market benchmark of ${quote.experience.pooled_tenants} companies (×${quote.experience.factor}).`
                      : "No settled policies yet: priced on the forecast alone (factor ×1)."}
                </div>
              )}
              {canWrite && quote.offered && !bound && (
                <div className="flex flex-wrap gap-2 items-end border-t border-slate-800 pt-3" data-testid="cover-bind">
                  <label className="block">
                    <span className="text-[10px] uppercase tracking-wider text-slate-400">Marketplace</span>
                    <input data-testid="cover-bind-marketplace" value={bind.marketplace}
                      onChange={(e) => setBind((b) => ({ ...b, marketplace: e.target.value.toUpperCase() }))}
                      className="w-16 bg-slate-950 border border-slate-700 rounded px-2 py-1 font-mono" />
                  </label>
                  <label className="block">
                    <span className="text-[10px] uppercase tracking-wider text-slate-400">Rescue playbook</span>
                    <select data-testid="cover-bind-playbook" value={bind.rescue_playbook_id}
                      onChange={(e) => setBind((b) => ({ ...b, rescue_playbook_id: e.target.value }))}
                      className="bg-slate-950 border border-slate-700 rounded px-2 py-1">
                      <option value="">none</option>
                      {playbooks.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                  </label>
                  <button data-testid="cover-bind-submit" onClick={bindQuote} disabled={busy}
                    className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white rounded-lg">Bind cover (pilot)</button>
                </div>
              )}
              {bound && <div className="text-emerald-300" data-testid="cover-bound">
                Bound as pilot policy {bound.id}, ends {bound.ends_at.slice(0, 10)}.</div>}
            </div>
          )}
        </Panel>
      </div>

      <CoverPolicies role={role} reloadKey={reloadKey} onError={setError} onChanged={() => setReloadKey((k) => k + 1)} />
      <CoverLots role={role} reloadKey={reloadKey} onError={setError} onChanged={() => setReloadKey((k) => k + 1)} />

      <Panel title="Recent quotes">
        <DataTable testId="cover-quotes" rows={quotes} empty="No quotes yet."
          columns={[
            { key: "po_id", label: "PO" },
            { key: "risk_grade", label: "Grade", render: (r) => <span className={GRADE_TONE[r.risk_grade]}>{r.risk_grade}</span> },
            { key: "units", label: "Units", right: true },
            { key: "covered_units", label: "Covered", right: true },
            { key: "premium_eur", label: "Premium", right: true, render: (r) => (r.offered ? eur(r.premium_eur) : "declined") },
            { key: "created_by", label: "By" },
            { key: "created_at", label: "When", render: (r) => r.created_at.slice(0, 16).replace("T", " ") },
          ]} />
      </Panel>
    </main>
  );
}
