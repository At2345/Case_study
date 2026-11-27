import React, { useCallback, useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api } from "../services/api";
import { Panel } from "./ui";

const STATUS_TEXT = {
  share_to_see: "Share your results (admin setting) to see the cross-company benchmark.",
  insufficient_data: "Not enough companies yet for a benchmark.",
};

function ratio(block) {
  if (!block || block.value_per_eur === null || block.value_per_eur === undefined) return null;
  return `€${block.value_per_eur} / €1`;
}

// Playbook template library: your results per template and, when available, the pooled benchmark.
export default function WorkflowTemplates({ canWrite, onInstalled, onError }) {
  const [rows, setRows] = useState([]);
  const [open, setOpen] = useState(null); // template id with the install form open
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.workflowTemplates()
    .then((r) => setRows(r.templates))
    .catch((e) => onError && onError(e.message)), [onError]);

  useEffect(() => { load(); }, [load]);

  const startInstall = (tpl) => {
    const values = Object.fromEntries(tpl.params.map((p) => [p.name, p.default ?? ""]));
    setForm({ workflow_id: tpl.id.replace(/[^a-z0-9_-]/g, "-"), ...values });
    setOpen(tpl.id);
  };

  const install = async (tpl) => {
    setBusy(true);
    try {
      const { workflow_id: workflowId, ...values } = form;
      const params = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "" && v !== null));
      const wf = await api.installWorkflowTemplate(tpl.id, { workflow_id: workflowId, params });
      setOpen(null);
      onInstalled(wf, tpl.example_payload);
      load();
    } catch (e) {
      onError && onError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Templates" subtitle="Proven playbooks. Install one, then adjust it in the editor." testId="wf-templates">
      <ul className="space-y-3">
        {rows.map(({ template: tpl, yours, pooled, pooled_status: status }) => (
          <li key={tpl.id} data-testid={`wf-template-${tpl.id}`} className="text-[11px]">
            <div className="flex items-start justify-between gap-2">
              <span className="text-slate-200 font-semibold" title={tpl.description}>{tpl.name}</span>
              {canWrite && open !== tpl.id && (
                <button data-testid={`wf-template-install-${tpl.id}`} onClick={() => startInstall(tpl)}
                  className="text-indigo-300 hover:text-indigo-200 flex items-center gap-1 shrink-0">
                  <Download className="h-3 w-3" /> Install
                </button>
              )}
            </div>
            <div className="text-slate-500">{tpl.description}</div>
            <div className="mt-1 text-slate-400">
              Yours: {yours ? `${yours.runs} runs${ratio(yours) ? ` · ${ratio(yours)}` : ""}` : "not used yet"}
            </div>
            <div className={pooled ? "text-emerald-300" : "text-slate-500"} data-testid={`wf-template-pooled-${tpl.id}`}>
              {pooled
                ? `Benchmark: ${pooled.tenants} companies · ${pooled.runs} runs${ratio(pooled) ? ` · ${ratio(pooled)}` : ""}`
                : STATUS_TEXT[status] || ""}
            </div>
            {open === tpl.id && (
              <div className="mt-2 space-y-1 bg-slate-950 border border-slate-800 rounded p-2">
                {[{ name: "workflow_id", description: "New playbook id", required: true }, ...tpl.params].map((p) => (
                  <label key={p.name} className="block">
                    <span className="text-[10px] uppercase tracking-wider text-slate-400">{p.name}{p.required ? " *" : ""}</span>
                    <input data-testid={`wf-template-field-${p.name}`} value={form[p.name] ?? ""} placeholder={p.description}
                      onChange={(e) => setForm((f) => ({ ...f, [p.name]: e.target.value }))}
                      className="w-full bg-slate-900 border border-slate-700 rounded px-1 py-0.5 font-mono" />
                  </label>
                ))}
                <div className="flex gap-2 pt-1">
                  <button data-testid={`wf-template-confirm-${tpl.id}`} onClick={() => install(tpl)} disabled={busy}
                    className="px-2 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded">Install</button>
                  <button onClick={() => setOpen(null)} className="px-2 py-1 text-slate-400 hover:text-white">Cancel</button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
