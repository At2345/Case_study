import React, { useCallback, useEffect, useState } from "react";
import { Plug, Plus, Trash2, RefreshCw, Save, X, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { api } from "../services/api";
import { Panel, ErrorNote } from "../components/ui";

const TRANSPORTS = ["streamable_http", "http_sse"];
const EMPTY = { id: "", name: "", kind: "erp", transport: "streamable_http", url: "",
  headers: "", enabled: true, tools: "", timeout_s: 10 };

const STATUS_STYLES = {
  connected: "bg-emerald-950 text-emerald-300 border-emerald-800",
  failed: "bg-rose-950 text-rose-300 border-rose-800",
  never_tested: "bg-slate-800 text-slate-400 border-slate-700",
};

function headersToText(headers) {
  return Object.entries(headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
}

function textToHeaders(text) {
  const out = {};
  String(text || "").split("\n").map((l) => l.trim()).filter(Boolean).forEach((line) => {
    const i = line.indexOf(":");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  });
  return out;
}

function textToTools(text) {
  return String(text || "").split(/[\n,]/).map((t) => t.trim()).filter(Boolean);
}

function ServerForm({ kinds, initial, onSave, onCancel, busy }) {
  const [form, setForm] = useState(initial);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const editing = Boolean(initial.id);
  const kindMeta = kinds?.[form.kind] || { tools: [], write_tools: [] };
  const submit = () => {
    const body = {
      id: form.id.trim(), name: form.name.trim(), kind: form.kind, transport: form.transport,
      url: form.url.trim(), headers: textToHeaders(form.headers), enabled: form.enabled,
      tools: textToTools(form.tools), timeout_s: Number(form.timeout_s) || 10,
    };
    onSave(body);
  };
  return (
    <div data-testid="mcp-form" className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-white">{editing ? `Edit ${initial.id}` : "Add MCP server"}</span>
        <button type="button" onClick={onCancel} className="text-slate-400 hover:text-white"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-[10px] text-slate-400 space-y-1">
          Server id (slug)
          <input data-testid="mcp-field-id" value={form.id} onChange={(e) => set("id", e.target.value)} disabled={editing}
            placeholder="erp_sap" className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100 disabled:opacity-50" />
        </label>
        <label className="text-[10px] text-slate-400 space-y-1">
          Display name
          <input data-testid="mcp-field-name" value={form.name} onChange={(e) => set("name", e.target.value)}
            placeholder="SAP ERP" className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100" />
        </label>
        <label className="text-[10px] text-slate-400 space-y-1">
          Kind
          <select data-testid="mcp-field-kind" value={form.kind} onChange={(e) => set("kind", e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100">
            {Object.entries(kinds || {}).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </label>
        <label className="text-[10px] text-slate-400 space-y-1">
          Transport
          <select data-testid="mcp-field-transport" value={form.transport} onChange={(e) => set("transport", e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100">
            {TRANSPORTS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>
      <label className="block text-[10px] text-slate-400 space-y-1">
        Endpoint URL
        <input data-testid="mcp-field-url" value={form.url} onChange={(e) => set("url", e.target.value)}
          placeholder={kindMeta.sample_url || "https://mcp.example.com"}
          className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100" />
      </label>
      <label className="block text-[10px] text-slate-400 space-y-1">
        Auth headers (one per line, e.g. <code className="text-slate-300">Authorization: Bearer sk-...</code>) — stored server-side, masked in the UI
        <textarea data-testid="mcp-field-headers" value={form.headers} onChange={(e) => set("headers", e.target.value)} rows={2}
          className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100 font-mono" />
      </label>
      <label className="block text-[10px] text-slate-400 space-y-1">
        Tool allowlist (comma or line separated; empty = all discovered tools allowed)
        <textarea data-testid="mcp-field-tools" value={form.tools} onChange={(e) => set("tools", e.target.value)} rows={2}
          placeholder={kindMeta.tools?.join(", ")}
          className="w-full bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100 font-mono" />
      </label>
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 text-[10px] text-slate-400">
          <input data-testid="mcp-field-enabled" type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)}
            className="accent-indigo-500" />
          Enabled
        </label>
        <label className="text-[10px] text-slate-400 flex items-center gap-2">
          Timeout (s)
          <input data-testid="mcp-field-timeout" type="number" min={1} max={120} value={form.timeout_s}
            onChange={(e) => set("timeout_s", e.target.value)}
            className="w-20 bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs text-slate-100" />
        </label>
      </div>
      <div className="flex gap-2">
        <button data-testid="mcp-save-btn" type="button" onClick={submit} disabled={busy || !form.id.trim() || !form.name.trim() || !form.url.trim()}
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 rounded-md text-xs font-semibold text-white flex items-center gap-1.5">
          <Save className="h-3.5 w-3.5" /> {editing ? "Save changes" : "Add server"}
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-md text-xs text-slate-300">Cancel</button>
      </div>
    </div>
  );
}

export default function McpPage({ role }) {
  const [servers, setServers] = useState([]);
  const [kinds, setKinds] = useState({});
  const [calls, setCalls] = useState([]);
  const [form, setForm] = useState(null);
  const [testing, setTesting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const isAdmin = role === "admin";

  const load = useCallback(async () => {
    try {
      const d = await api.mcpServers();
      setServers(d.servers || []);
      setKinds(d.kinds || {});
      setError(null);
    } catch (e) { setError(e.message); }
  }, []);

  const loadCalls = useCallback(async () => {
    try { setCalls((await api.mcpCalls(20)).calls || []); } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { load(); loadCalls(); }, [load, loadCalls]);

  const save = async (body) => {
    setBusy(true); setError(null);
    try {
      if (form.id) await api.updateMcpServer(form.id, body);
      else await api.createMcpServer(body);
      setNotice(form.id ? `Server ${form.id} updated.` : `Server ${body.id} added.`);
      setForm(null);
      await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const remove = async (id) => {
    if (!window.confirm(`Delete MCP server ${id}?`)) return;
    setBusy(true); setError(null);
    try {
      await api.deleteMcpServer(id);
      setNotice(`Server ${id} deleted.`);
      await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const test = async (id) => {
    setTesting(id); setError(null);
    try {
      const r = await api.testMcpServer(id);
      setNotice(`${id}: ${r.status} — ${(r.tools || []).length} tools discovered.`);
      await load();
    } catch (e) { setError(e.message); } finally { setTesting(null); }
  };

  const edit = (s) => setForm({ ...s, headers: headersToText(s.headers), tools: (s.tools || []).join("\n") });

  return (
    <div className="px-8 py-6 space-y-4" data-testid="mcp-page">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2"><Plug className="h-4 w-4 text-emerald-400" /> MCP connections</h2>
          <p className="text-[11px] text-slate-400 mt-0.5">
            External systems the co-pilot can query (ERP, CRM, Jira). Secrets are masked; every tool call is written to the audit log.
          </p>
        </div>
        {isAdmin && (
          <button data-testid="mcp-add-btn" type="button" onClick={() => setForm({ ...EMPTY, tools: kinds?.erp?.tools?.join("\n") || "" })}
            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-semibold text-white flex items-center gap-1.5">
            <Plus className="h-4 w-4" /> Add server
          </button>
        )}
      </div>
      <ErrorNote error={error} />
      {notice && <div className="bg-emerald-950/50 border border-emerald-800 text-emerald-200 rounded-lg px-4 py-3 text-xs">{notice}</div>}
      {form && <ServerForm kinds={kinds} initial={form} onSave={save} onCancel={() => setForm(null)} busy={busy} />}
      <Panel title="Configured servers" subtitle="Admin manages connections; the co-pilot uses enabled servers when a question targets an external system.">
        {servers.length === 0 && <div className="text-xs text-slate-500 py-6 text-center">No MCP servers configured yet.</div>}
        <div className="space-y-2">
          {servers.map((s) => (
            <div key={s.id} data-testid={`mcp-server-${s.id}`} className="bg-slate-950 border border-slate-800 rounded-lg p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-bold text-slate-100">{s.name}</span>
                <span className="px-1.5 py-0.5 bg-slate-800 rounded text-[9px] text-slate-400">{s.kind_label}</span>
                <span data-testid={`mcp-status-${s.id}`} className={`px-1.5 py-0.5 rounded text-[9px] font-semibold border ${STATUS_STYLES[s.status] || STATUS_STYLES.never_tested}`}>
                  {s.status === "connected" ? <CheckCircle2 className="inline h-3 w-3 mr-0.5" /> : s.status === "failed" ? <XCircle className="inline h-3 w-3 mr-0.5" /> : <AlertTriangle className="inline h-3 w-3 mr-0.5" />}
                  {s.status}
                </span>
                {s.enabled ? <span className="text-[9px] text-emerald-300">enabled</span> : <span className="text-[9px] text-slate-500">disabled</span>}
                <span className="text-[9px] text-slate-500 font-mono ml-auto">{s.url}</span>
              </div>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <span className="text-[9px] text-slate-500">Headers: {Object.entries(s.headers || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "none"}</span>
                <span className="text-[9px] text-slate-500">Allowlist: {(s.tools || []).length ? s.tools.join(", ") : "all tools"}</span>
              </div>
              {(s.discovered_tools || []).length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {(s.discovered_tools || []).slice(0, 8).map((t) => (
                    <span key={t.name} className="px-1.5 py-0.5 bg-emerald-950/60 border border-emerald-900 rounded text-[9px] text-emerald-300">{t.name}</span>
                  ))}
                  {(s.discovered_tools || []).length > 8 && <span className="text-[9px] text-slate-500">+{(s.discovered_tools || []).length - 8} more</span>}
                </div>
              )}
              {s.last_error && <div className="mt-1 text-[9px] text-rose-300">{s.last_error}</div>}
              {isAdmin && (
                <div className="flex gap-2 mt-2">
                  <button data-testid={`mcp-test-${s.id}`} type="button" onClick={() => test(s.id)} disabled={testing === s.id}
                    className="px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md text-[10px] font-semibold text-slate-200 flex items-center gap-1">
                    <RefreshCw className={`h-3 w-3 ${testing === s.id ? "animate-spin" : ""}`} /> Test connection
                  </button>
                  <button data-testid={`mcp-edit-${s.id}`} type="button" onClick={() => edit(s)}
                    className="px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md text-[10px] text-slate-200">Edit</button>
                  <button data-testid={`mcp-delete-${s.id}`} type="button" onClick={() => remove(s.id)}
                    className="px-2 py-1 bg-rose-950 hover:bg-rose-900 border border-rose-900 rounded-md text-[10px] text-rose-300 flex items-center gap-1">
                    <Trash2 className="h-3 w-3" /> Delete
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Tool calls (audit log)" subtitle="Every MCP tool call, successful or not, is recorded here.">
        {calls.length === 0 && <div className="text-xs text-slate-500 py-6 text-center">No tool calls yet. Ask the co-pilot about an external system, e.g. "what open purchase orders are in SAP?".</div>}
        {calls.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                  <th className="py-2 pr-3 font-medium">When</th><th className="py-2 pr-3 font-medium">Server</th>
                  <th className="py-2 pr-3 font-medium">Tool</th><th className="py-2 pr-3 font-medium">User</th>
                  <th className="py-2 pr-3 font-medium">Latency</th><th className="py-2 pr-3 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c, i) => (
                  <tr key={i} className="border-b border-slate-800/60">
                    <td className="py-2 pr-3 text-slate-400">{new Date(c.at).toLocaleString("en-GB")}</td>
                    <td className="py-2 pr-3 text-slate-200 font-mono">{c.server_id}</td>
                    <td className="py-2 pr-3 text-slate-200 font-mono" title={c.args ? `args: ${c.args}` : ""}>{c.tool}</td>
                    <td className="py-2 pr-3 text-slate-300">{c.user}</td>
                    <td className="py-2 pr-3 text-slate-400">{c.latency_ms != null ? `${c.latency_ms} ms` : "-"}</td>
                    <td className="py-2 pr-3">{c.ok ? <span className="text-emerald-300">ok</span> : <span className="text-rose-300">{c.error || "failed"}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
