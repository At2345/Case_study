import React, { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Pencil, X, GitCompare } from "lucide-react";
import { api } from "../services/api";
import { fmtNum, fmtGbpPlain, monthLabel } from "../lib/format";
import { Panel, ErrorNote } from "../components/ui";

const CHANNELS = ["B2B wo Amazon", "amazon", "D2C"];
const METRICS = [
  ["value_12m_gbp", "Planned value (12 months)", "gbp", 1], ["margin_12m_gbp", "Gross margin (12 months)", "gbp", 1],
  ["units_12m", "Units (12 months)", "num", 1], ["value_m1_gbp", "Planned value (first month)", "gbp", 1],
  ["units_m1", "Units (first month)", "num", 1], ["otif_items_at_risk", "OTIF item-months at risk", "num", -1],
  ["otif_penalty_gbp", "OTIF fines (estimate)", "gbp", -1], ["safety_stock_units", "Safety stock (units)", "num", -1],
  ["holding_cost_gbp", "Safety-stock holding cost p.a.", "gbp", -1],
];
const fmt = (v, kind) => (v === null || v === undefined ? "-" : kind === "gbp" ? fmtGbpPlain(v) : fmtNum(v));
const months = (first) => {
  if (!first) return [];
  const d = new Date(first + "T00:00:00Z");
  return Array.from({ length: 12 }, (_, i) => new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + i, 1)).toISOString().slice(0, 10));
};
const describe = (e) => e.type === "pct"
  ? `${e.pct >= 0 ? "+" : ""}${e.pct}% ${e.scope === "all" ? "all items" : e.value}${e.month ? ` in ${monthLabel(e.month)}` : ", all months"}`
  : `Item ${e.item_id} = ${fmtNum(e.qty)} units in ${monthLabel(e.month)}`;
const EMPTY_EDIT = { type: "pct", scope: "channel", value: CHANNELS[0], pct: 10, month: "", item_id: "", qty: "" };

function EditBuilder({ monthList, onAdd }) {
  const [e, setE] = useState(EMPTY_EDIT);
  const [hint, setHint] = useState(null);
  const set = (k) => (ev) => setE({ ...e, [k]: ev.target.value });
  const input = "bg-slate-950 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200";
  const add = () => {
    setHint(null);
    if (e.type === "pct") {
      if (e.scope === "product" && !e.value.trim()) return setHint("Enter a product id, e.g. PROD_1320.");
      if (e.pct === "" || Number.isNaN(Number(e.pct))) return setHint("Enter a % change.");
      onAdd({ type: "pct", scope: e.scope, value: e.scope === "all" ? null : e.value.trim(), pct: Number(e.pct), month: e.month || null });
    } else {
      if (!e.item_id || e.qty === "" || !e.month) return setHint("Enter an item id, units and a month.");
      onAdd({ type: "item", item_id: Number(e.item_id), qty: Number(e.qty), month: e.month });
    }
  };
  return (
    <div className="flex flex-wrap gap-2 items-center bg-slate-950/50 border border-slate-800 rounded-lg p-2">
      <select data-testid="edit-type-select" value={e.type} onChange={(ev) => setE({ ...EMPTY_EDIT, type: ev.target.value, month: ev.target.value === "item" ? monthList[0] : "" })} className={input}>
        <option value="pct">% change</option><option value="item">Item quantity</option>
      </select>
      {e.type === "pct" ? (
        <>
          <select data-testid="edit-scope-select" value={e.scope} onChange={(ev) => setE({ ...e, scope: ev.target.value, value: ev.target.value === "channel" ? CHANNELS[0] : "" })} className={input}>
            <option value="all">All items</option><option value="channel">Channel</option><option value="product">Product</option>
          </select>
          {e.scope === "channel" && (
            <select data-testid="edit-channel-select" value={e.value} onChange={set("value")} className={input}>
              {CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          {e.scope === "product" && <input data-testid="edit-product-input" placeholder="PROD_1320" value={e.value} onChange={set("value")} className={`${input} w-28`} />}
          <input data-testid="edit-pct-input" type="number" value={e.pct} onChange={set("pct")} className={`${input} w-20`} /> <span className="text-xs text-slate-400">%</span>
        </>
      ) : (
        <>
          <input data-testid="edit-item-input" type="number" placeholder="Item id" value={e.item_id} onChange={set("item_id")} className={`${input} w-24`} />
          <input data-testid="edit-qty-input" type="number" min="0" placeholder="Units" value={e.qty} onChange={set("qty")} className={`${input} w-24`} />
        </>
      )}
      <select data-testid="edit-month-select" value={e.month} onChange={set("month")} className={input}>
        {e.type === "pct" && <option value="">All months</option>}
        {monthList.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
      </select>
      <button data-testid="edit-add-btn" type="button" onClick={add} className="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-xs flex items-center gap-1">
        <Plus className="h-3.5 w-3.5" /> Add edit
      </button>
      {hint && <span data-testid="edit-hint" className="w-full text-[11px] text-amber-300">{hint}</span>}
    </div>
  );
}

function ScenarioForm({ initial, monthList, onSave, onCancel }) {
  const [name, setName] = useState(initial ? initial.name : "");
  const [description, setDescription] = useState(initial ? initial.description : "");
  const [edits, setEdits] = useState(initial ? initial.edits : []);
  const [error, setError] = useState(null);
  const submit = async (ev) => {
    ev.preventDefault();
    if (!name.trim()) return setError("Give the scenario a name.");
    try { await onSave({ name: name.trim(), description, edits }); } catch (e) { setError(e.message); }
  };
  return (
    <form onSubmit={submit} data-testid="scenario-form" className="space-y-3 bg-slate-950 border border-indigo-900/60 rounded-xl p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold text-white uppercase tracking-wider">{initial ? "Edit scenario" : "New scenario from the current consensus"}</h3>
        <button type="button" onClick={onCancel} className="text-slate-400 hover:text-white"><X className="h-4 w-4" /></button>
      </div>
      <input data-testid="scenario-name-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. B2B campaign upside"
        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white" />
      <textarea data-testid="scenario-description-input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500}
        placeholder="Assumption behind this version" className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200" />
      <EditBuilder monthList={monthList} onAdd={(e) => setEdits([...edits, e])} />
      <ul className="space-y-1" data-testid="scenario-edit-list">
        {edits.length === 0 && <li className="text-[11px] text-slate-500">No edits: the scenario equals the current consensus.</li>}
        {edits.map((e, i) => (
          <li key={i} className="flex items-center justify-between text-xs text-slate-300 bg-slate-900 rounded-md px-2 py-1">
            <span>{i + 1}. {describe(e)}</span>
            <button type="button" data-testid={`edit-remove-${i}`} onClick={() => setEdits(edits.filter((_, j) => j !== i))} className="text-slate-500 hover:text-rose-400"><X className="h-3.5 w-3.5" /></button>
          </li>
        ))}
      </ul>
      <ErrorNote error={error} />
      <button data-testid="scenario-save-btn" type="submit" className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-semibold text-white">Save scenario</button>
    </form>
  );
}

function Delta({ v, kind, good }) {
  if (v === null || v === undefined || Math.abs(v) < 0.5) return <span className="text-slate-600">no change</span>;
  const better = v * good > 0;
  return <span className={better ? "text-emerald-400" : "text-rose-400"}>{v > 0 ? "+" : "-"}{fmt(Math.abs(v), kind)}</span>;
}

function CompareTable({ data }) {
  const cols = data.columns;
  const channels = Object.keys(cols[0].metrics.by_channel);
  return (
    <div className="overflow-x-auto" data-testid="scenario-compare-table">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-slate-800 text-left">
            <th className="py-2 pr-3 text-[10px] uppercase tracking-wider text-slate-500 font-medium">Metric</th>
            {cols.map((c) => (
              <th key={c.id} className="py-2 pr-3 text-right align-bottom">
                <span className={`block text-xs font-semibold ${c.id === "current" ? "text-slate-300" : "text-indigo-300"}`}>{c.name}</span>
                {c.created_by && <span className="block text-[10px] text-slate-500 font-normal">by {c.created_by}, {c.edits.length} edit{c.edits.length === 1 ? "" : "s"}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRICS.map(([k, label, kind, good]) => (
            <tr key={k} className="border-b border-slate-800/60">
              <td className="py-2 pr-3 text-slate-400">{label}</td>
              {cols.map((c) => (
                <td key={c.id} className="py-2 pr-3 text-right font-mono" data-testid={`cmp-${k}-${c.id}`}>
                  <span className="block text-slate-200">{fmt(c.metrics[k], kind)}</span>
                  {c.delta && <span className="block text-[10px]"><Delta v={c.delta[k]} kind={kind} good={good} /></span>}
                </td>
              ))}
            </tr>
          ))}
          {channels.map((ch) => (
            <tr key={ch} className="border-b border-slate-800/60">
              <td className="py-2 pr-3 text-slate-500">Value, {ch}</td>
              {cols.map((c) => <td key={c.id} className="py-2 pr-3 text-right font-mono text-slate-300">{fmtGbpPlain(c.metrics.by_channel[ch].value_gbp)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[10px] text-slate-500 mt-3">Green means better for the business: more value or margin, or less OTIF exposure, stock or holding cost. Safety stock scales with the first-month plan. Scenarios are a sandbox and are not written to the audit ledger.</p>
    </div>
  );
}

export default function ScenariosPage({ user, summary }) {
  const [list, setList] = useState(null);
  const [selected, setSelected] = useState([]);
  const [compare, setCompare] = useState(null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState(null);
  const canWrite = user.role === "planner" || user.role === "admin";
  const monthList = months(summary && summary.first_month);

  const load = useCallback(() => api.scenarios().then(setList).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api.compareScenarios(selected).then(setCompare).catch((e) => setError(e.message));
  }, [selected, list]);

  const toggle = (id) => {
    setError(null);
    if (selected.includes(id)) return setSelected(selected.filter((x) => x !== id));
    if (selected.length >= 3) return setError("Compare at most 3 scenarios at a time.");
    setSelected([...selected, id]);
  };
  const save = async (body) => {
    const s = editing === "new" ? await api.createScenario(body) : await api.updateScenario(editing.id, body);
    setEditing(null);
    if (!selected.includes(s.id) && selected.length < 3) setSelected([...selected, s.id]);
    load();
  };
  const remove = async (s) => {
    if (!window.confirm(`Delete scenario "${s.name}"?`)) return;
    try { await api.deleteScenario(s.id); setSelected(selected.filter((x) => x !== s.id)); load(); } catch (e) { setError(e.message); }
  };
  const mine = (s) => s.created_by === user.username || user.role === "admin";

  return (
    <div className="px-8 py-6 grid grid-cols-1 xl:grid-cols-12 gap-6" data-testid="scenarios-page">
      <aside className="xl:col-span-4 space-y-4">
        <Panel title="What-if scenarios" subtitle="Copy the current consensus, apply edits and compare up to 3 versions before committing."
          right={canWrite && !editing && (
            <button data-testid="new-scenario-btn" onClick={() => setEditing("new")}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-semibold flex items-center gap-1.5"><Plus className="h-3.5 w-3.5" /> New</button>
          )}>
          {editing && <ScenarioForm key={editing === "new" ? "new" : editing.id} initial={editing === "new" ? null : editing} monthList={monthList} onSave={save} onCancel={() => setEditing(null)} />}
          <ul className="space-y-2 mt-3" data-testid="scenario-list">
            {list && list.scenarios.length === 0 && <li className="text-xs text-slate-500">No scenarios yet for this cycle.</li>}
            {list && list.scenarios.map((s) => (
              <li key={s.id} data-testid={`scenario-${s.id}`}
                className={`rounded-lg border p-3 ${selected.includes(s.id) ? "border-indigo-500 bg-indigo-950/30" : "border-slate-800 bg-slate-950/50"}`}>
                <div className="flex items-start gap-2">
                  <input type="checkbox" data-testid={`scenario-select-${s.id}`} checked={selected.includes(s.id)} onChange={() => toggle(s.id)} className="mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-semibold text-white">{s.name}</div>
                    <div className="text-[10px] text-slate-500">by {s.created_by} | {s.edits.length} edit{s.edits.length === 1 ? "" : "s"}</div>
                    {s.description && <div className="text-[11px] text-slate-400 mt-1">{s.description}</div>}
                    <ul className="mt-1 text-[10px] text-slate-400">{s.edits.slice(0, 3).map((e, i) => <li key={i}>{describe(e)}</li>)}{s.edits.length > 3 && <li>...</li>}</ul>
                  </div>
                  {canWrite && mine(s) && (
                    <div className="flex gap-1">
                      <button data-testid={`scenario-edit-${s.id}`} onClick={() => setEditing(s)} className="p-1 text-slate-400 hover:text-white"><Pencil className="h-3.5 w-3.5" /></button>
                      <button data-testid={`scenario-delete-${s.id}`} onClick={() => remove(s)} className="p-1 text-slate-400 hover:text-rose-400"><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </aside>
      <div className="xl:col-span-8 space-y-4">
        <ErrorNote error={error} />
        <Panel title="Side-by-side comparison" testId="scenario-compare-panel"
          subtitle={`${selected.length} of 3 selected, compared with the current consensus (planner decisions included).`}
          right={<GitCompare className="h-4 w-4 text-indigo-400" />}>
          {compare ? <CompareTable data={compare} /> : <div className="text-xs text-slate-500">Loading...</div>}
        </Panel>
      </div>
    </div>
  );
}
