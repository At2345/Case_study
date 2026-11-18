const BASE = (import.meta.env && (import.meta.env.REACT_APP_BACKEND_URL || import.meta.env.VITE_API_URL)) || "";
const TOKEN_KEY = "sop_token";
const USER_KEY = "sop_user";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let unauthorizedHandler = () => {};
export const onUnauthorized = (fn) => {
  unauthorizedHandler = fn;
};

export const getToken = () => sessionStorage.getItem(TOKEN_KEY);
export const getStoredUser = () => {
  try {
    return JSON.parse(sessionStorage.getItem(USER_KEY));
  } catch {
    return null;
  }
};
export const clearSession = () => {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
};

export const detailToMessage = (detail) => {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return "Request failed";
};

async function request(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(`${BASE}${path}`, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "Cannot reach the API. Is the backend running?");
  }
  if (res.status === 401 && path !== "/api/v1/auth/login") {
    clearSession();
    unauthorizedHandler();
  }
  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detailToMessage(detail));
  }
  return raw ? res : res.json();
}

const qs = (params) => {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  async login(username, password) {
    const data = await request("/api/v1/auth/login", { method: "POST", body: { username, password } });
    const user = { username: data.username, role: data.role, tenant: data.tenant };
    sessionStorage.setItem(TOKEN_KEY, data.access_token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(user));
    return user;
  },
  logout: clearSession,
  summary: () => request("/api/v1/summary"),
  exceptions: (params) => request(`/api/v1/exceptions${qs(params)}`),
  item: (id) => request(`/api/v1/items/${id}`),
  narrative: (id) => request(`/api/v1/items/${id}/narrative`, { method: "POST" }),
  override: (payload) => request("/api/v1/override", { method: "POST", body: payload }),
  reasonCodes: () => request("/api/v1/reason-codes"),
  allocate: (payload) => request("/api/v1/allocation/simulate", { method: "POST", body: payload }),
  fva: (groupBy = "planner_id") => request(`/api/v1/fva${qs({ group_by: groupBy })}`),
  commit: (cutoff) => request(`/api/v1/cycles/${cutoff}/commit`, { method: "POST" }),
  otif: (limit = 50) => request(`/api/v1/otif${qs({ limit })}`),
  disaggregate: (payload) => request("/api/v1/disaggregate", { method: "POST", body: payload }),
  dynamicForecast: (itemId, method = "auto") => request(`/api/v1/forecast/items/${itemId}${qs({ method })}`),
  agenticForecast: (itemId, method = "auto") => request(`/api/v1/forecast/items/${itemId}/agentic${qs({ method })}`),
  safetyStock: (params) => request(`/api/v1/inventory/safety-stock${qs(params)}`),
  runs: () => request("/api/v1/cycles/runs"),
  runDetail: (runId) => request(`/api/v1/cycles/runs/${runId}`),
  rerunNode: (runId, node) => request("/api/v1/admin/cycles/rerun-node", { method: "POST", body: { run_id: runId, node } }),
  riskFlags: () => request("/api/v1/supply/risk-flags"),
  cycleAgentActions: () => request("/api/v1/cycles/agent-actions"),
  runCycle: () => request("/api/v1/admin/cycles/run", { method: "POST" }),
  reload: () => request("/api/v1/admin/reload", { method: "POST" }),
  autopilot: (segment = "channel") => request(`/api/v1/fva/autopilot${qs({ segment })}`),
  applyAutopilot: (body) => request("/api/v1/fva/autopilot/apply", { method: "POST", body }),
  overrides: (limit = 200) => request(`/api/v1/overrides${qs({ limit })}`),
  auditVerify: () => request("/api/v1/audit/verify"),
  assistantHistory: () => request("/api/v1/assistant/history"),
  assistantClear: () => request("/api/v1/assistant/history", { method: "DELETE" }),
  async assistantStream(question, onDelta, onActions, onMeta, onSuggestions) {
    const res = await request("/api/v1/assistant/stream", { method: "POST", body: { question }, raw: true });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop();
      for (const ev of events) {
        const line = ev.trim();
        if (!line.startsWith("data:")) continue;
        const msg = JSON.parse(line.slice(5));
        if (msg.error) throw new ApiError(503, msg.error);
        if (msg.delta) onDelta(msg.delta);
        if (msg.actions && onActions) onActions(msg.actions);
        if (msg.suggestions && onSuggestions) onSuggestions(msg.suggestions);
        if (onMeta && (msg.tier || msg.sources)) onMeta({ tier: msg.tier, sources: msg.sources });
      }
    }
  },
  schedules: () => request("/api/v1/schedules"),
  updateSchedule: (job, body) => request(`/api/v1/admin/schedules/${job}`, { method: "PUT", body }),
  notifications: () => request("/api/v1/notifications"),
  readNotification: (id) => request(`/api/v1/notifications/${id}/read`, { method: "POST" }),
  readAllNotifications: () => request("/api/v1/notifications/read-all", { method: "POST" }),
  tasks: () => request("/api/v1/tasks"),
  runTask: (id) => request(`/api/v1/tasks/${id}/run`, { method: "POST" }),
  mcpServers: () => request("/api/v1/mcp/servers"),
  biStatus: () => request("/api/v1/bi/status"),
  biDashboard: () => request("/api/v1/bi/dashboard"),
  biDashboardExceptions: (params) => request(`/api/v1/bi/dashboard/exceptions${qs(params)}`),
  biKpiTree: () => request("/api/v1/bi/kpi-tree"),
  biMetrics: () => request("/api/v1/bi/metrics"),
  biDiagnostics: (series) => request(`/api/v1/bi/diagnostics${qs({ series })}`),
  biContracts: () => request("/api/v1/bi/contracts"),
  biFinops: (days) => request(`/api/v1/bi/finops${qs({ days })}`),
  biServing: (days) => request(`/api/v1/bi/serving${qs({ days })}`),
  biTransport: (tables) => request(`/api/v1/bi/transport${qs({ tables: (tables || []).join(",") })}`),
  biExportColumnar: async (table, format) => {
    const res = await request(`/api/v1/bi/export/columnar${qs({ table, format })}`, { raw: true });
    return res.blob();
  },
  biAnnotations: (params) => request(`/api/v1/bi/annotations${qs(params)}`),
  biAnnotate: (body) => request("/api/v1/bi/annotations", { method: "POST", body }),
  biActions: () => request("/api/v1/bi/actions"),
  biProposeAction: (body) => request("/api/v1/bi/actions", { method: "POST", body }),
  biDecideAction: (id, body) => request(`/api/v1/bi/actions/${id}/decide`, { method: "POST", body }),
  biWritebackVerify: () => request("/api/v1/bi/writeback/verify"),
  biAsk: (question) => request("/api/v1/bi/ask", { method: "POST", body: { question } }),
  biExport: () => request("/api/v1/bi/export", { method: "POST" }),
  createMcpServer: (body) => request("/api/v1/mcp/servers", { method: "POST", body }),
  updateMcpServer: (id, body) => request(`/api/v1/mcp/servers/${id}`, { method: "PUT", body }),
  deleteMcpServer: (id) => request(`/api/v1/mcp/servers/${id}`, { method: "DELETE" }),
  testMcpServer: (id) => request(`/api/v1/mcp/servers/${id}/test`, { method: "POST" }),
  mcpCalls: (limit = 50) => request(`/api/v1/mcp/calls${qs({ limit })}`),
  scenarios: () => request("/api/v1/scenarios"),
  generateScenario: (prompt) => request("/api/v1/scenarios/generate", { method: "POST", body: { prompt } }),
  scenarioSuggestions: () => request("/api/v1/scenarios/suggestions"),
  createScenario: (body) => request("/api/v1/scenarios", { method: "POST", body }),
  updateScenario: (id, body) => request(`/api/v1/scenarios/${id}`, { method: "PUT", body }),
  deleteScenario: (id) => request(`/api/v1/scenarios/${id}`, { method: "DELETE" }),
  compareScenarios: (ids) => request(`/api/v1/scenarios/compare${qs({ ids: ids.join(",") })}`),
  promoteScenario: (id) => request(`/api/v1/scenarios/${id}/promote`, { method: "POST" }),
  async briefing() {
    const res = await request("/api/v1/export/briefing.pdf", { raw: true });
    return res.blob();
  },
  async exportDraft() {
    const res = await request("/api/v1/export/consensus", { raw: true });
    return res.blob();
  },
};
