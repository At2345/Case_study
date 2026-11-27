import React, { useCallback, useEffect, useState } from "react";
import { KeyRound, Share2 } from "lucide-react";
import { api } from "../services/api";
import { DataTable, ErrorNote, Panel } from "../components/ui";

const BASE = (import.meta.env && (import.meta.env.REACT_APP_BACKEND_URL || import.meta.env.VITE_API_URL)) || window.location.origin;
const when = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");

// Admin: API keys for machine clients (n8n, ERP, buyers, AI agents) and the benchmark sharing switch.
export default function IntegrationsPage() {
  const [keys, setKeys] = useState([]);
  const [form, setForm] = useState({ name: "", role: "planner" });
  const [created, setCreated] = useState(null);
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [k, s] = await Promise.all([api.apiKeys(), api.workflowSettings()]);
      setKeys(k.keys);
      setSettings(s);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const create = () => act(async () => {
    const k = await api.createApiKey({ name: form.name.trim(), role: form.role });
    setCreated(k);
    setForm({ name: "", role: "planner" });
    await load();
  });

  const revoke = (k) => {
    if (!window.confirm(`Revoke the key "${k.name}"? Systems using it stop working immediately.`)) return;
    act(async () => {
      await api.revokeApiKey(k.id);
      await load();
    });
  };

  const toggleSharing = () => act(async () => {
    setSettings(await api.updateWorkflowSettings({ share_benchmarks: !settings.share_benchmarks }));
  });

  return (
    <main className="flex-1 px-8 py-6 space-y-6" data-testid="integrations-page">
      <ErrorNote error={error} />
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Panel title="API keys" subtitle="For n8n, ERP feeds, buyer systems and AI agents. Keys reach only the workflow, cover-policy and recovery-lot endpoints."
          className="xl:col-span-8" testId="api-keys">
          <div className="flex flex-wrap gap-2 items-end mb-4">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-400">Name</span>
              <input data-testid="api-key-name" value={form.name} placeholder="e.g. n8n production"
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs w-56" />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-400">Role</span>
              <select data-testid="api-key-role" value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs">
                <option value="planner">planner (read and act)</option>
                <option value="auditor">auditor (read only)</option>
              </select>
            </label>
            <button data-testid="api-key-create" onClick={create} disabled={busy || !form.name.trim()}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs rounded-lg flex items-center gap-1">
              <KeyRound className="h-3.5 w-3.5" /> Create key
            </button>
          </div>
          {created && (
            <div role="status" data-testid="api-key-created" className="mb-4 bg-amber-950/50 border border-amber-800 rounded-lg p-3 text-xs">
              <div className="text-amber-200 font-semibold">Copy this key now. It is shown only once.</div>
              <code className="block mt-1 break-all text-amber-100" data-testid="api-key-value">{created.key}</code>
              <button onClick={() => setCreated(null)} className="mt-2 text-amber-300 hover:text-amber-100">I have stored it</button>
            </div>
          )}
          <DataTable testId="api-key-list" rows={keys} empty="No API keys yet."
            columns={[
              { key: "name", label: "Name" },
              { key: "role", label: "Role" },
              { key: "created_by", label: "Created by" },
              { key: "created_at", label: "Created", render: (k) => when(k.created_at) },
              { key: "last_used_at", label: "Last used", render: (k) => when(k.last_used_at) },
              { key: "status", label: "Status", render: (k) => (k.revoked_at
                ? <span className="text-slate-500">revoked {when(k.revoked_at)}</span>
                : <button data-testid={`api-key-revoke-${k.id}`} onClick={() => revoke(k)} disabled={busy}
                    className="text-rose-300 hover:text-rose-200">Revoke</button>) },
            ]} />
        </Panel>

        <div className="xl:col-span-4 space-y-6">
          <Panel title="Benchmark sharing" testId="sharing">
            {settings && (
              <div className="space-y-3 text-xs">
                <p className="text-slate-400">
                  When on, your anonymised playbook results and cover experience join the cross-company pool, and you
                  see the pooled benchmark in return. Pooled figures appear only when at least 3 companies contribute,
                  and only as totals.
                </p>
                <div className="flex items-center gap-3">
                  <span data-testid="sharing-state" className={settings.share_benchmarks ? "text-emerald-300" : "text-slate-300"}>
                    {settings.share_benchmarks ? "Sharing is on" : "Sharing is off"}
                  </span>
                  <button data-testid="sharing-toggle" onClick={toggleSharing} disabled={busy}
                    className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg flex items-center gap-1">
                    <Share2 className="h-3.5 w-3.5" /> {settings.share_benchmarks ? "Stop sharing" : "Start sharing"}
                  </button>
                </div>
              </div>
            )}
          </Panel>
          <Panel title="Connect" testId="connect-help">
            <ul className="text-xs text-slate-400 space-y-2">
              <li><b className="text-slate-200">n8n:</b> install <code>n8n-nodes-sop-workbench</code>, add credentials with base URL{" "}
                <code className="break-all">{BASE}</code> and a planner key.</li>
              <li><b className="text-slate-200">AI agents (MCP):</b> <code className="break-all">{BASE}/api/v1/workflows/mcp</code> with header{" "}
                <code>Authorization: Bearer &lt;key&gt;</code>.</li>
              <li><b className="text-slate-200">ERP sales feed:</b> POST snapshots to <code>/api/v1/cover/policies/&lt;id&gt;/observations</code>.</li>
              <li><b className="text-slate-200">Buyers:</b> bid on <code>/api/v1/cover/lots/&lt;id&gt;/bids</code>.</li>
            </ul>
          </Panel>
        </div>
      </div>
    </main>
  );
}
