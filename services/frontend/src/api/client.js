/* 后端 REST API 客户端封装：统一 /api 前缀、身份 header（X-Synetra-User）与会话。 */

const BASE = "/api";
const KEY = "synetra.session";

export function getSession() {
  try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; }
}
export function setSession(s) { localStorage.setItem(KEY, JSON.stringify(s)); }
export function clearSession() { localStorage.removeItem(KEY); }

function headers(json = false) {
  const h = {};
  const s = getSession();
  if (s?.username) h["X-Synetra-User"] = s.username;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

export async function request(method, path, { body, params } = {}) {
  let url = BASE + path;
  if (params) {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ).toString();
    if (q) url += `?${q}`;
  }
  const res = await fetch(url, {
    method,
    headers: headers(body !== undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* 非 JSON */ }
    const e = new Error(detail);
    e.status = res.status;
    throw e;
  }
  return res.json();
}

export async function login(username, password) {
  const data = await request("POST", "/auth/login", { body: { username, password } });
  setSession({ username: data.username, role: data.role });
  return data;
}

export async function logout() {
  clearSession();
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body }),
  put: (path, body) => request("PUT", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path) => request("DELETE", path),
};