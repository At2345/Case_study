const BASE = (import.meta.env && import.meta.env.VITE_API_URL) || "http://localhost:8000";
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
    sessionStorage.setItem(TOKEN_KEY, data.access_token);
    sessionStorage.setItem(USER_KEY, JSON.stringify({ username: data.username, role: data.role }));
    return { username: data.username, role: data.role };
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
  async exportDraft() {
    const res = await request("/api/v1/export/consensus", { raw: true });
    return res.blob();
  },
};
