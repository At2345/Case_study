import React, { useState } from "react";
import { Plug, RefreshCw, Stethoscope, Trash2 } from "lucide-react";
import { api } from "../services/api";
import { Panel } from "./ui";

const BASE = (import.meta.env && (import.meta.env.REACT_APP_BACKEND_URL || import.meta.env.VITE_API_URL)) || window.location.origin;
const KIND_TONE = { marketplace: "text-amber-300", shop: "text-sky-300", supply: "text-violet-300" };
const field = "w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs";
const when = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "never");

// Connections to marketplaces, shops and EDI suppliers: add (admin), test, sync, enable/disable, remove.
export default function ChannelConnections({ role, types, connections, onChanged, onError, onMessage }) {
  const isAdmin = role === "admin";
  const canWrite = role === "planner" || isAdmin;
  const [adding, setAdding] = useState(null); // selected type name
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(null);
  const typeOf = (name) => types.find((t) => t.name === name);

  const act = async (key, fn, message) => {
    setBusy(key);
    try {
      const result = await fn();
      if (message) onMessage(typeof message === "function" ? message(result) : message);
      onChanged();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const startAdd = (name) => {
    const t = typeOf(name);
    setAdding(name);
    setForm({ name: t ? t.label : "", sync_minutes: 15, low_stock: "",
      ...Object.fromEntries((t ? t.fields : []).map((f) => [f.name, f.default ?? ""])) });
  };

  const save = () => act("add", async () => {
    const t = typeOf(adding);
    const config = Object.fromEntries(t.fields.map((f) => [f.name, form[f.name]]).filter(([, v]) => v !== "" && v !== null));
    await api.addChannelConnection({ type: adding, name: form.name, config, sync_minutes: Number(form.sync_minutes) || 15,
      low_stock: form.low_stock === "" ? null : Number(form.low_stock) });
    setAdding(null);
  }, "Connection added. Credentials are stored encrypted and are never shown again.");

  return (
    <Panel title="Channels" subtitle="Marketplaces, shops and suppliers through their own free APIs and your own credentials."
      testId="channel-connections"
      right={isAdmin && (
        <select data-testid="channel-add-type" value="" onChange={(e) => e.target.value && startAdd(e.target.value)}
          className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs">
          <option value="">+ Connect…</option>
          {types.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
      )}>
      {adding && (
        <div className="mb-4 bg-slate-950 border border-slate-800 rounded-lg p-3 space-y-2 text-xs" data-testid="channel-add-form">
          <div className="text-slate-300">{typeOf(adding).label} <span className="text-slate-500">{typeOf(adding).notes}</span></div>
          {[{ name: "name", label: "Name", secret: false, required: true }, ...typeOf(adding).fields].map((f) => (
            <label key={f.name} className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-400">{f.label}{f.required ? " *" : ""}</span>
              <input data-testid={`channel-field-${f.name}`} type={f.secret ? "password" : "text"} autoComplete="off"
                value={form[f.name] ?? ""} placeholder={f.help || ""} className={field}
                onChange={(e) => setForm((x) => ({ ...x, [f.name]: e.target.value }))} />
            </label>
          ))}
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-400">Sync every (minutes)</span>
              <input data-testid="channel-field-sync_minutes" value={form.sync_minutes} className={field}
                onChange={(e) => setForm((x) => ({ ...x, sync_minutes: e.target.value }))} />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-400">Low-stock alert at</span>
              <input data-testid="channel-field-low_stock" value={form.low_stock} placeholder="off" className={field}
                onChange={(e) => setForm((x) => ({ ...x, low_stock: e.target.value }))} />
            </label>
          </div>
          <div className="flex gap-2">
            <button data-testid="channel-add-save" onClick={save} disabled={busy === "add"}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg flex items-center gap-1">
              <Plug className="h-3.5 w-3.5" /> Connect
            </button>
            <button onClick={() => setAdding(null)} className="px-3 py-1.5 text-slate-400 hover:text-white">Cancel</button>
          </div>
        </div>
      )}
      {connections.length === 0 && <div className="text-xs text-slate-400">No channels connected yet.</div>}
      <ul className="space-y-3">
        {connections.map((c) => {
          const t = typeOf(c.type);
          return (
            <li key={c.id} data-testid={`channel-${c.id}`} className="text-xs border-b border-slate-800 pb-2">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="font-semibold text-slate-200">{c.name}</span>
                <span className={KIND_TONE[t ? t.kind : ""] || "text-slate-400"}>{t ? t.label : c.type}</span>
                <span className={c.enabled ? "text-emerald-300" : "text-slate-500"}>{c.enabled ? "on" : "off"}</span>
                <span className="text-slate-400">last sync {when(c.last_sync_at)}</span>
                {c.last_error && <span className="text-rose-300" data-testid={`channel-error-${c.id}`}>{c.last_error}</span>}
              </div>
              {c.capabilities.includes("webhooks") && (
                <div className="text-[11px] text-slate-500 mt-0.5">Webhook: <code className="break-all">{BASE}{c.webhook_path}</code></div>
              )}
              {canWrite && (
                <div className="flex gap-2 mt-1">
                  <button data-testid={`channel-test-${c.id}`} disabled={busy === c.id}
                    onClick={() => act(c.id, () => api.testChannelConnection(c.id),
                      (r) => (r.ok ? `${c.name}: connected (${r.account || "ok"}).` : `${c.name}: ${r.error}`))}
                    className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded flex items-center gap-1">
                    <Stethoscope className="h-3 w-3" /> Test
                  </button>
                  <button data-testid={`channel-sync-${c.id}`} disabled={busy === c.id}
                    onClick={() => act(c.id, () => api.syncChannelConnection(c.id),
                      (r) => `${c.name}: ${r.orders_created} new orders, ${r.inventory_items} stock items, ${r.events} events`
                        + (r.errors.length ? ` (errors: ${r.errors.join("; ")})` : "") + (r.initial ? " (first import, no events)" : ""))}
                    className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded flex items-center gap-1">
                    <RefreshCw className="h-3 w-3" /> Sync now
                  </button>
                  {isAdmin && (
                    <>
                      <button data-testid={`channel-toggle-${c.id}`}
                        onClick={() => act(c.id, () => api.updateChannelConnection(c.id, { enabled: !c.enabled }))}
                        className="px-2 py-0.5 text-slate-300 hover:text-white">{c.enabled ? "Disable" : "Enable"}</button>
                      <button data-testid={`channel-delete-${c.id}`} aria-label={`Remove ${c.name}`}
                        onClick={() => window.confirm(`Remove ${c.name}? Its stored credentials are deleted.`)
                          && act(c.id, () => api.deleteChannelConnection(c.id), `${c.name} removed.`)}
                        className="px-2 py-0.5 text-rose-300 hover:text-rose-200"><Trash2 className="h-3 w-3" /></button>
                    </>
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
