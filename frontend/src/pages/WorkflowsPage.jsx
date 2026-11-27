import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CheckCircle2, FlaskConical, Play, Plus, Save, ShieldAlert, Trash2, XCircle } from "lucide-react";
import { api } from "../services/api";
import { DataTable, ErrorNote, Panel } from "../components/ui";
import WorkflowTemplates from "../components/WorkflowTemplates";
import {
  CATEGORY_ORDER, CATEGORY_TONE, EMPTY_META, coerceField, defaultParams, displayValue, fieldsFromSchema,
  fromFlow, makeEdge, missingRequired, newNodeId, toFlow,
} from "../lib/workflowGraph";

const DRAG_TYPE = "application/x-workflow-node";
const STATUS_TONE = {
  SUCCEEDED: "text-emerald-300", FAILED: "text-rose-300", REJECTED: "text-rose-300",
  WAITING: "text-sky-300", WAITING_APPROVAL: "text-amber-300", RUNNING: "text-slate-200",
};
const STEP_TONE = { ok: "bg-emerald-600", failed: "bg-rose-600", waiting: "bg-sky-600", awaiting_approval: "bg-amber-600" };
const EXAMPLE_PAYLOAD = '{\n  "sku": "MAT-BLUE", "marketplace": "DE", "units_on_hand": 1000,\n  "gap": -0.2, "unit_cost": 8.0, "days_to_trigger": 90\n}';

function WfNode({ id, data, selected }) {
  const spec = data.spec;
  const cat = spec ? spec.category : "platform";
  const ports = spec ? spec.ports : ["out"];
  return (
    <div data-testid={`wf-node-${id}`}
      className={`rounded-lg border-2 bg-slate-900 px-3 py-2 min-w-[180px] text-xs shadow ${CATEGORY_TONE[cat] || CATEGORY_TONE.platform} ${selected ? "ring-2 ring-indigo-400" : ""}`}>
      {cat !== "trigger" && <Handle type="target" position={Position.Top} />}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[9px] uppercase tracking-wider opacity-80">{cat}</span>
        {data.runStatus && <span className={`text-[9px] px-1.5 rounded text-white ${STEP_TONE[data.runStatus] || "bg-slate-600"}`}>{data.runStatus}</span>}
      </div>
      <div className="font-semibold text-white mt-0.5">{data.label || data.nodeType}</div>
      <div className="text-[10px] text-slate-400 font-mono">{id}</div>
      {spec && spec.policy.needs_approval && <div className="text-[9px] text-amber-300 mt-1 flex items-center gap-1"><ShieldAlert className="h-3 w-3" />needs approval</div>}
      {!spec && <div className="text-[10px] text-rose-300 mt-1">unknown node type</div>}
      {ports.length > 1 && (
        <div className="flex justify-around text-[9px] text-slate-400 mt-1">{ports.map((p) => <span key={p}>{p}</span>)}</div>
      )}
      {ports.map((p, i) => (
        <Handle key={p} id={p} type="source" position={Position.Bottom} style={{ left: `${((i + 1) / (ports.length + 1)) * 100}%` }} />
      ))}
    </div>
  );
}

const NODE_TYPES = { wfNode: WfNode };

function ParamField({ field, value, onChange, testId }) {
  const [draft, setDraft] = useState(displayValue(field, value));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setDraft(displayValue(field, value)), [field.kind, value]);
  const base = "w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-slate-100";
  let input;
  if (field.kind === "enum") {
    input = (
      <select data-testid={testId} className={base} value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
        {!field.required && <option value="">-</option>}
        {field.options.map((o) => <option key={String(o)} value={o}>{String(o)}</option>)}
      </select>
    );
  } else if (field.kind === "boolean") {
    input = <input data-testid={testId} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  } else {
    input = (
      <input data-testid={testId} className={`${base} font-mono`} value={draft}
        placeholder={field.kind === "string" ? "" : `${field.kind}${field.min !== undefined ? ` ≥ ${field.min}` : ""}${field.max !== undefined ? ` ≤ ${field.max}` : ""} or $reference`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onChange(coerceField(field, draft))} />
    );
  }
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wider text-slate-400">{field.name}{field.required ? " *" : ""}</span>
      {input}
      {field.description && <span className="block text-[10px] text-slate-500 mt-0.5">{field.description}</span>}
    </label>
  );
}

function Editor({ role }) {
  const canWrite = role === "planner" || role === "admin";
  const rf = useReactFlow();
  const [types, setTypes] = useState([]);
  const [list, setList] = useState([]);
  const [meta, setMeta] = useState(EMPTY_META);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [errors, setErrors] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [payloadText, setPayloadText] = useState("{}");
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [perf, setPerf] = useState([]);
  const [ledger, setLedger] = useState(null);
  const [outcome, setOutcome] = useState({ metric: "loss_avoided_eur", value: "", note: "" });
  const [describe, setDescribe] = useState("");

  const draftFromText = () => guard(async () => {
    const out = await api.draftWorkflow(describe.trim());
    const g = toFlow(out.spec, types);
    setMeta({ ...g.meta, version: undefined }); // a draft is never saved until the planner saves it
    setNodes(g.nodes);
    setEdges(g.edges);
    setSelectedId(null);
    setRun(null);
    setRuns([]);
    setErrors(out.errors);
    const how = out.method === "llm" ? "Drafted with the AI model" : "Drafted from the closest template";
    setNotice([`${how}. Review every step, then save.`, ...(out.notes || [])].join(" "));
  });

  const refreshPerf = useCallback(() => api.workflowPerformance()
    .then((r) => setPerf(r.playbooks))
    .catch(() => setPerf([])), []);

  // Ledger rows for the open run; reload whenever its status or step count changes.
  const runKey = run ? `${run.run_id}:${run.status}:${run.steps.length}` : "";
  useEffect(() => {
    if (!run) { setLedger(null); return; }
    api.workflowRunLedger(run.run_id).then(setLedger).catch(() => setLedger(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey]);

  const guard = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e.message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    guard(async () => {
      const [t, w] = await Promise.all([api.workflowNodeTypes(), api.workflows()]);
      setTypes(t.node_types);
      setList(w.workflows);
    });
    refreshPerf();
  }, [guard, refreshPerf]);

  const refreshRuns = useCallback(async (workflowId) => {
    if (!workflowId) return setRuns([]);
    const r = await api.workflowRuns({ workflow_id: workflowId, limit: 20 });
    setRuns(r.runs);
  }, []);

  const loadWorkflow = (id) => guard(async () => {
    const wf = await api.workflow(id);
    const g = toFlow(wf, types);
    setMeta(g.meta);
    setNodes(g.nodes);
    setEdges(g.edges);
    setSelectedId(null);
    setErrors([]);
    setRun(null);
    setNotice(null);
    await refreshRuns(wf.id);
  });

  const newWorkflow = () => {
    setMeta(EMPTY_META);
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
    setErrors([]);
    setRuns([]);
    setRun(null);
    setNotice(null);
  };

  const addNode = (spec, position) => {
    if (spec.category === "trigger" && nodes.some((n) => n.data.spec && n.data.spec.category === "trigger")) {
      setError("A workflow has exactly one trigger. Delete the current trigger first.");
      return;
    }
    const id = newNodeId(spec.name, nodes.map((n) => n.id));
    const pos = position || { x: 80 + (nodes.length % 4) * 260, y: 60 + Math.floor(nodes.length / 4) * 170 };
    setNodes((ns) => [...ns, { id, type: "wfNode", position: pos, data: { nodeType: spec.name, label: "", params: defaultParams(spec), spec } }]);
    setSelectedId(id);
    setError(null);
  };

  const onConnect = useCallback((c) => setEdges((es) => (
    es.some((e) => e.source === c.source && e.target === c.target && e.sourceHandle === c.sourceHandle)
      ? es : [...es, makeEdge(c.source, c.target, c.sourceHandle || "out")]
  )), [setEdges]);

  const onDrop = (e) => {
    e.preventDefault();
    const spec = types.find((t) => t.name === e.dataTransfer.getData(DRAG_TYPE));
    if (spec) addNode(spec, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
  };

  const updateNode = (id, patch) => setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
  const setParam = (node, name, value) => updateNode(node.id, { params: { ...node.data.params, [name]: value } });
  const deleteNode = (id) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
    setSelectedId(null);
  };

  const spec = useMemo(() => fromFlow(meta, nodes, edges), [meta, nodes, edges]);
  const selected = nodes.find((n) => n.id === selectedId) || null;
  const saved = !!meta.version;

  const validate = () => guard(async () => {
    const r = await api.validateWorkflow(spec);
    setErrors(r.errors);
    setNotice(r.valid ? "Workflow is valid." : null);
  });

  const save = () => guard(async () => {
    const r = await api.saveWorkflow(spec);
    setMeta((m) => ({ ...m, version: r.version }));
    setErrors([]);
    setNotice(`Saved ${r.id} as version ${r.version}.`);
    setList((await api.workflows()).workflows);
    await refreshRuns(r.id);
  });

  const startRun = (dryRun) => guard(async () => {
    let payload;
    try {
      payload = JSON.parse(payloadText || "{}");
    } catch {
      throw new Error("Run payload is not valid JSON.");
    }
    const r = await api.startWorkflowRun(meta.id, { payload, dry_run: dryRun });
    setRun(r);
    setNotice(`${dryRun ? "Dry run" : "Run"} ${r.run_id}: ${r.status}`);
    await refreshRuns(meta.id);
    if (!dryRun) refreshPerf();
  });

  const openRun = (runId) => guard(async () => setRun(await api.workflowRun(runId)));

  const decide = (approved) => guard(async () => {
    const r = await api.approveWorkflowRun(run.run_id, { node: run.pending.node, approved });
    setRun(r);
    setNotice(`Step ${run.pending.node} ${approved ? "approved" : "rejected"}. Run is now ${r.status}.`);
    await refreshRuns(r.workflow_id);
    refreshPerf();
  });

  const recordOutcome = () => guard(async () => {
    const value = Number(outcome.value);
    if (outcome.value === "" || !Number.isFinite(value)) throw new Error("Enter a numeric outcome value.");
    await api.recordWorkflowOutcome(run.run_id, { metric: outcome.metric, value, note: outcome.note || null });
    setLedger(await api.workflowRunLedger(run.run_id));
    setOutcome((o) => ({ ...o, value: "", note: "" }));
    setNotice(`Outcome recorded for run ${run.run_id}.`);
    refreshPerf();
  });

  // Colour nodes by their latest step status when a run of this exact version is open.
  const shownNodes = useMemo(() => {
    if (!run || run.workflow_id !== meta.id || run.workflow_version !== meta.version) return nodes;
    const last = {};
    run.steps.forEach((s) => { last[s.node] = s.status; });
    return nodes.map((n) => (last[n.id] ? { ...n, data: { ...n.data, runStatus: last[n.id] } } : n));
  }, [nodes, run, meta.id, meta.version]);

  const palette = CATEGORY_ORDER.map((cat) => [cat, types.filter((t) => t.category === cat)]).filter(([, ts]) => ts.length);
  const missing = selected ? missingRequired(selected.data.spec, selected.data.params) : [];

  return (
    <main className="flex-1 px-8 py-6 space-y-6" data-testid="workflows-page">
      <ErrorNote error={error} />
      {notice && <div className="bg-emerald-950/50 border border-emerald-800 text-emerald-200 rounded-lg px-4 py-3 text-xs">{notice}</div>}
      {errors.length > 0 && (
        <div role="alert" data-testid="wf-errors" className="bg-amber-950/50 border border-amber-800 text-amber-200 rounded-lg px-4 py-3 text-xs">
          <div className="font-semibold mb-1">Fix before saving:</div>
          <ul className="list-disc ml-4">{errors.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-2 space-y-4">
          <Panel title="Playbooks" right={canWrite && (
            <button data-testid="wf-new" onClick={newWorkflow} className="p-1 text-slate-300 hover:text-white" aria-label="New workflow"><Plus className="h-4 w-4" /></button>
          )}>
            {list.length === 0 && <div className="text-xs text-slate-400">No saved workflows yet.</div>}
            <ul className="space-y-1">
              {list.map((w) => (
                <li key={w.id}>
                  <button data-testid={`wf-open-${w.id}`} onClick={() => loadWorkflow(w.id)}
                    className={`w-full text-left text-xs px-2 py-1.5 rounded ${meta.id === w.id ? "bg-indigo-900/60 text-white" : "text-slate-300 hover:bg-slate-800"}`}>
                    {w.name} <span className="text-slate-500">v{w.version}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
          {canWrite && (
            <Panel title="Describe a playbook" subtitle="Plain words in, a draft out. Nothing is saved until you save it.">
              <textarea data-testid="wf-describe" value={describe} onChange={(e) => setDescribe(e.target.value)} rows={4}
                placeholder="e.g. Discount slow DE stock, transfer deep gaps to FR, budget €1,500"
                aria-label="Describe a playbook"
                className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs" />
              <button data-testid="wf-draft" onClick={draftFromText} disabled={busy || describe.trim().length < 5}
                className="mt-2 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs rounded-lg">
                Draft playbook
              </button>
            </Panel>
          )}
          <WorkflowTemplates canWrite={canWrite} onError={setError}
            onInstalled={async (wf, examplePayload) => {
              setList((await api.workflows()).workflows);
              await loadWorkflow(wf.id);
              if (examplePayload) setPayloadText(JSON.stringify(examplePayload, null, 2));
              setNotice(`Installed ${wf.name} as ${wf.id}. Review it, then run a dry run.`);
            }} />
          <Panel title="Results" subtitle="Real runs, ranked by measured value per € spent." testId="wf-performance">
            {perf.length === 0 && <div className="text-xs text-slate-400">No real runs recorded yet.</div>}
            <ol className="space-y-2">
              {perf.map((p) => (
                <li key={p.workflow_id} data-testid={`wf-perf-${p.workflow_id}`} className="text-[11px]">
                  <div className="flex justify-between gap-2">
                    <span className="text-slate-200 truncate">{p.workflow_id}</span>
                    <span className={`font-mono ${p.value_per_eur === null ? "text-slate-500" : p.value_per_eur >= 1 ? "text-emerald-300" : "text-rose-300"}`}>
                      {p.value_per_eur === null ? "not measured" : `€${p.value_per_eur} / €1`}
                    </span>
                  </div>
                  <div className="text-slate-500">
                    {p.runs} runs · spent €{p.spent_eur} · value €{p.value_eur}
                    {p.approval_rate !== null && ` · ${Math.round(p.approval_rate * 100)}% approved`}
                  </div>
                </li>
              ))}
            </ol>
          </Panel>
          {canWrite && (
            <Panel title="Nodes" subtitle="Drag onto the canvas or click to add.">
              {palette.map(([cat, ts]) => (
                <div key={cat} className="mb-3">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{cat}</div>
                  {ts.map((t) => (
                    <button key={t.name} data-testid={`wf-palette-${t.name}`} draggable title={t.description}
                      onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, t.name); e.dataTransfer.effectAllowed = "move"; }}
                      onClick={() => addNode(t)}
                      className={`w-full text-left text-[11px] px-2 py-1 mb-1 rounded border-l-2 bg-slate-950 hover:bg-slate-800 ${CATEGORY_TONE[cat]}`}>
                      {t.name}
                    </button>
                  ))}
                </div>
              ))}
            </Panel>
          )}
        </div>

        <div className="xl:col-span-7">
          <Panel title={meta.name || "Untitled workflow"} subtitle={saved ? `${meta.id} · version ${meta.version}` : "Not saved yet"}
            right={(
              <div className="flex gap-2">
                <button data-testid="wf-validate" onClick={validate} disabled={busy}
                  className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs rounded-lg flex items-center gap-1 disabled:opacity-40">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Validate
                </button>
                {canWrite && (
                  <button data-testid="wf-save" onClick={save} disabled={busy || !meta.id || !nodes.length}
                    className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs rounded-lg flex items-center gap-1 disabled:opacity-40">
                    <Save className="h-3.5 w-3.5" /> Save new version
                  </button>
                )}
              </div>
            )}>
            <div className="h-[560px] bg-slate-950 rounded-lg border border-slate-800" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
              <ReactFlow nodes={shownNodes} edges={edges} nodeTypes={NODE_TYPES}
                onNodesChange={canWrite ? onNodesChange : undefined} onEdgesChange={canWrite ? onEdgesChange : undefined}
                onConnect={canWrite ? onConnect : undefined} isValidConnection={(c) => c.source !== c.target}
                onNodeClick={(_, n) => setSelectedId(n.id)} onPaneClick={() => setSelectedId(null)}
                nodesDraggable={canWrite} nodesConnectable={canWrite} colorMode="dark" fitView>
                <Background />
                <Controls />
                <MiniMap pannable zoomable />
              </ReactFlow>
            </div>
          </Panel>
        </div>

        <div className="xl:col-span-3">
          {selected ? (
            <Panel title={selected.data.spec ? selected.data.spec.name : selected.data.nodeType}
              subtitle={selected.data.spec ? selected.data.spec.description : "Unknown node type"} testId="wf-inspector"
              right={canWrite && (
                <button data-testid="wf-delete-node" onClick={() => deleteNode(selected.id)} aria-label="Delete node" className="p-1 text-rose-300 hover:text-rose-200">
                  <Trash2 className="h-4 w-4" />
                </button>
              )}>
              <fieldset disabled={!canWrite} className="space-y-3">
                <div className="text-[10px] text-slate-500 font-mono">id: {selected.id}</div>
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-400">label</span>
                  <input data-testid="wf-node-label" className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs"
                    value={selected.data.label} onChange={(e) => updateNode(selected.id, { label: e.target.value })} />
                </label>
                {fieldsFromSchema(selected.data.spec && selected.data.spec.params_schema).map((f) => (
                  <ParamField key={`${selected.id}:${f.name}`} field={f} value={selected.data.params[f.name]} testId={`wf-param-${f.name}`}
                    onChange={(v) => setParam(selected, f.name, v)} />
                ))}
                {missing.length > 0 && <div className="text-[10px] text-amber-300">Required: {missing.join(", ")}</div>}
                <p className="text-[10px] text-slate-500">
                  Use <code>$trigger.field</code> or <code>$nodes.&lt;id&gt;.field</code> to pass values between steps.
                </p>
              </fieldset>
            </Panel>
          ) : (
            <Panel title="Workflow settings" testId="wf-settings">
              <fieldset disabled={!canWrite} className="space-y-3">
                {[["id", "Workflow id", "text", saved], ["name", "Name", "text", false],
                  ["budget_eur", "Budget per run (EUR)", "number", false],
                  ["approval_threshold_eur", "Approval above (EUR, blank = never)", "number", false]].map(([key, label, type, locked]) => (
                  <label key={key} className="block">
                    <span className="text-[10px] uppercase tracking-wider text-slate-400">{label}</span>
                    <input data-testid={`wf-meta-${key}`} type={type} disabled={locked} value={meta[key] ?? ""}
                      onChange={(e) => setMeta((m) => ({ ...m, [key]: e.target.value }))}
                      className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs disabled:opacity-50" />
                  </label>
                ))}
                <p className="text-[10px] text-slate-500">Select a node to edit its settings. Delete a node or edge with Backspace.</p>
              </fieldset>
            </Panel>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Panel title="Run" subtitle="Runs use the saved version. Dry runs simulate actions and spend nothing." className="xl:col-span-4">
          <textarea data-testid="wf-payload" value={payloadText} onChange={(e) => setPayloadText(e.target.value)} rows={7}
            className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs font-mono" aria-label="Run payload (JSON)" />
          <div className="flex gap-2 mt-2">
            <button onClick={() => setPayloadText(EXAMPLE_PAYLOAD)} className="px-2 py-1 text-[11px] text-slate-300 hover:text-white">Example payload</button>
            <div className="flex-1" />
            {canWrite && (
              <>
                <button data-testid="wf-dry-run" onClick={() => startRun(true)} disabled={busy || !saved}
                  className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs rounded-lg flex items-center gap-1 disabled:opacity-40">
                  <FlaskConical className="h-3.5 w-3.5" /> Dry run
                </button>
                <button data-testid="wf-run" onClick={() => startRun(false)} disabled={busy || !saved}
                  className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white text-xs rounded-lg flex items-center gap-1 disabled:opacity-40">
                  <Play className="h-3.5 w-3.5" /> Run
                </button>
              </>
            )}
          </div>
        </Panel>

        <Panel title="Run history" className="xl:col-span-4">
          <DataTable testId="wf-runs" rows={runs.map((r) => ({ ...r, id: r.run_id }))} empty="No runs yet."
            columns={[
              { key: "run_id", label: "Run", render: (r) => <button className="font-mono text-indigo-300 hover:underline" onClick={() => openRun(r.run_id)}>{r.run_id}</button> },
              { key: "status", label: "Status", render: (r) => <span className={STATUS_TONE[r.status]}>{r.status}{r.dry_run ? " (dry)" : ""}</span> },
              { key: "workflow_version", label: "v", right: true },
              { key: "spent_eur", label: "Spent €", right: true },
            ]} />
        </Panel>

        <Panel title={run ? `Run ${run.run_id}` : "Run detail"} className="xl:col-span-4" testId="wf-run-detail">
          {!run && <div className="text-xs text-slate-400">Start a run or pick one from the history.</div>}
          {run && (
            <div className="space-y-3 text-xs">
              <div>
                Status <span className={`font-semibold ${STATUS_TONE[run.status]}`} data-testid="wf-run-status">{run.status}</span>
                {" · "}spent €{run.spent_eur} · v{run.workflow_version}
              </div>
              {run.error && <div className="text-rose-300">{run.error}</div>}
              {run.status === "WAITING" && run.pending && <div className="text-sky-300">Resumes at {run.pending.resume_at}</div>}
              {run.status === "WAITING_APPROVAL" && run.pending && (
                <div className="bg-amber-950/40 border border-amber-800 rounded p-2">
                  <div className="text-amber-200">Step <b>{run.pending.node}</b>: {run.pending.reason}</div>
                  {canWrite && (
                    <div className="flex gap-2 mt-2">
                      <button data-testid="wf-approve" onClick={() => decide(true)} disabled={busy}
                        className="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 rounded flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" /> Approve</button>
                      <button data-testid="wf-reject" onClick={() => decide(false)} disabled={busy}
                        className="px-3 py-1 bg-rose-800 hover:bg-rose-700 rounded flex items-center gap-1"><XCircle className="h-3.5 w-3.5" /> Reject</button>
                    </div>
                  )}
                </div>
              )}
              <ol className="space-y-1" data-testid="wf-steps">
                {run.steps.map((s, i) => (
                  <li key={i} className="flex gap-2 items-baseline">
                    <span className={`text-[9px] px-1.5 rounded text-white ${STEP_TONE[s.status] || "bg-slate-600"}`}>{s.status}</span>
                    <span className="font-mono">{s.node}</span>
                    {s.port && s.port !== "out" && <span className="text-slate-400">→ {s.port}</span>}
                    {s.cost_eur ? <span className="text-slate-400">€{s.cost_eur}</span> : null}
                  </li>
                ))}
              </ol>
              {ledger && (
                <div className="border-t border-slate-800 pt-2" data-testid="wf-ledger">
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Decision ledger</div>
                  {ledger.decisions.length === 0 && <div className="text-slate-500">No decisions recorded.</div>}
                  <ul className="space-y-0.5">
                    {ledger.decisions.map((d) => (
                      <li key={d.id} className="text-[11px] text-slate-300">
                        <span className="text-slate-500">{d.kind.replace("_", " ")}</span> {d.node_id} · {d.status}
                        {d.cost_eur ? ` · €${d.cost_eur}` : ""}{d.kind === "human_decision" && d.actor ? ` · by ${d.actor}` : ""}
                      </li>
                    ))}
                  </ul>
                  {ledger.outcomes.length > 0 && (
                    <ul className="mt-1 space-y-0.5" data-testid="wf-outcomes">
                      {ledger.outcomes.map((o) => (
                        <li key={o.id} className="text-[11px] text-emerald-300">
                          outcome {o.metric.replace(/_/g, " ")}: {o.value} <span className="text-slate-500">({o.recorded_by})</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {canWrite && !run.dry_run && (
                    <div className="flex gap-1 mt-2 items-center">
                      <select data-testid="wf-outcome-metric" value={outcome.metric} aria-label="Outcome metric"
                        onChange={(e) => setOutcome((o) => ({ ...o, metric: e.target.value }))}
                        className="bg-slate-950 border border-slate-700 rounded px-1 py-1 text-[11px]">
                        {["loss_avoided_eur", "recovered_eur", "units_sold", "sell_through_pct", "other"].map((m) => (
                          <option key={m} value={m}>{m.replace(/_/g, " ")}</option>
                        ))}
                      </select>
                      <input data-testid="wf-outcome-value" value={outcome.value} placeholder="value" aria-label="Outcome value"
                        onChange={(e) => setOutcome((o) => ({ ...o, value: e.target.value }))}
                        className="w-20 bg-slate-950 border border-slate-700 rounded px-1 py-1 text-[11px] font-mono" />
                      <button data-testid="wf-outcome-save" onClick={recordOutcome} disabled={busy}
                        className="px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-[11px]">Record</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </main>
  );
}

export default function WorkflowsPage({ role }) {
  return (
    <ReactFlowProvider>
      <Editor role={role} />
    </ReactFlowProvider>
  );
}
