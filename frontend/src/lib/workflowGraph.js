// Conversion between the backend workflow DSL and React Flow nodes/edges, plus schema-driven form helpers.

export const CATEGORY_TONE = {
  trigger: "border-emerald-500 text-emerald-300",
  agent: "border-violet-500 text-violet-300",
  action: "border-amber-500 text-amber-300",
  logic: "border-sky-500 text-sky-300",
  control: "border-rose-500 text-rose-300",
  platform: "border-slate-500 text-slate-300",
};

export const CATEGORY_ORDER = ["trigger", "agent", "action", "logic", "control"];

export const EMPTY_META = { id: "", name: "", budget_eur: 0, approval_threshold_eur: "", template: null };

export const isReference = (v) => typeof v === "string" && (v === "$trigger" || v.startsWith("$trigger.") || v.startsWith("$nodes."));

const autoPosition = (i) => ({ x: 80 + (i % 4) * 260, y: 60 + Math.floor(i / 4) * 170 });

export function toFlow(wf, types) {
  const byName = Object.fromEntries((types || []).map((t) => [t.name, t]));
  const nodes = (wf.nodes || []).map((n, i) => ({
    id: n.id,
    type: "wfNode",
    position: n.position && Number.isFinite(n.position.x) ? { x: n.position.x, y: n.position.y } : autoPosition(i),
    data: { nodeType: n.type, label: n.label || "", params: { ...(n.params || {}) }, spec: byName[n.type] || null },
  }));
  const edges = (wf.edges || []).map((e) => makeEdge(e.source, e.target, e.port || "out"));
  const meta = {
    id: wf.id || "", name: wf.name || "", version: wf.version,
    budget_eur: wf.budget_eur ?? 0,
    approval_threshold_eur: wf.approval_threshold_eur ?? "",
    template: wf.template || null, // keeps the template link (benchmark) across edits
  };
  return { meta, nodes, edges };
}

export function makeEdge(source, target, port = "out") {
  return {
    id: `${source}:${port}->${target}`, source, target, sourceHandle: port,
    label: port !== "out" ? port : undefined,
  };
}

export function fromFlow(meta, nodes, edges) {
  const trigger = nodes.find((n) => n.data.spec && n.data.spec.category === "trigger");
  const threshold = meta.approval_threshold_eur;
  return {
    id: (meta.id || "").trim(),
    name: (meta.name || "").trim(),
    trigger: trigger ? trigger.id : "",
    budget_eur: Number(meta.budget_eur) || 0,
    approval_threshold_eur: threshold === "" || threshold === null || threshold === undefined ? null : Number(threshold),
    template: meta.template || null,
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.nodeType,
      params: cleanParams(n.data.params),
      label: n.data.label || null,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e) => ({ source: e.source, target: e.target, port: e.sourceHandle || "out" })),
  };
}

function cleanParams(params) {
  return Object.fromEntries(Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== ""));
}

export function newNodeId(nodeType, existingIds) {
  let base = nodeType.split(".").pop().toLowerCase().replace(/[^a-z0-9_]/g, "_");
  if (!/^[a-z]/.test(base)) base = `n_${base}`;
  const taken = new Set(existingIds);
  for (let i = 1; ; i += 1) {
    const id = `${base}_${i}`.slice(0, 64);
    if (!taken.has(id)) return id;
  }
}

// ---------------------------------------------------------------- schema-driven forms
export function fieldsFromSchema(schema) {
  const props = (schema && schema.properties) || {};
  const required = new Set((schema && schema.required) || []);
  return Object.entries(props).map(([name, p]) => {
    const main = pickType(p);
    let kind = "json";
    if (Array.isArray(main.enum)) kind = "enum";
    else if (main.type === "integer") kind = "integer";
    else if (main.type === "number") kind = "number";
    else if (main.type === "boolean") kind = "boolean";
    else if (main.type === "string") kind = "string";
    return {
      name, kind, required: required.has(name),
      label: p.title || name,
      description: p.description || main.description || "",
      options: main.enum || null,
      min: main.minimum ?? main.exclusiveMinimum, max: main.maximum ?? main.exclusiveMaximum,
      default: p.default,
    };
  });
}

function pickType(p) {
  if (Array.isArray(p.anyOf)) return p.anyOf.find((x) => x.type !== "null") || {};
  return p;
}

export function defaultParams(spec) {
  const out = {};
  fieldsFromSchema(spec && spec.params_schema).forEach((f) => {
    if (f.default !== undefined) out[f.name] = f.default;
  });
  return out;
}

// Convert a form input string to the value stored in params. References ("$trigger.x") stay strings.
export function coerceField(field, raw) {
  if (typeof raw === "string" && raw.startsWith("$")) return raw;
  if (raw === "" || raw === undefined) return undefined;
  if (field.kind === "number" || field.kind === "integer") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw; // keep bad input so the backend reports it
  }
  if (field.kind === "boolean") return raw === true || raw === "true";
  if (field.kind === "json") {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}

export function displayValue(field, value) {
  if (value === undefined || value === null) return "";
  if (field.kind === "json" && typeof value !== "string") return JSON.stringify(value);
  return String(value);
}

export function missingRequired(spec, params) {
  return fieldsFromSchema(spec && spec.params_schema)
    .filter((f) => f.required && (params[f.name] === undefined || params[f.name] === ""))
    .map((f) => f.name);
}
