/* 后端 REST API 客户端封装：统一 /api 前缀、会话票据（Authorization: Bearer）与会话存取。
 *
 * 身份只来自服务端签发的会话票据：登录成功后 token 随会话存入 localStorage，
 * 每个请求带 Authorization 头；收到 401 时清除会话并通知上层回到登录页。
 */

const BASE = "/api";
const KEY = "synetra.session";

let unauthorizedHandler = null;

/** 注册 401 回调（App 用它把界面切回登录页）。 */
export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

export function getSession() {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || "null");
    if (!s || !s.token) return null;
    if (s.exp && s.exp * 1000 <= Date.now()) return null; // 票据已过期视为未登录
    return s;
  } catch {
    return null;
  }
}

export function setSession(s) { localStorage.setItem(KEY, JSON.stringify(s)); }
export function clearSession() { localStorage.removeItem(KEY); }

function headers(json = false) {
  const h = {};
  const s = getSession();
  if (s?.token) h["Authorization"] = `Bearer ${s.token}`;
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
  if (res.status === 401 && !path.startsWith("/auth/login")) {
    clearSession();
    if (typeof unauthorizedHandler === "function") unauthorizedHandler();
    const e = new Error("登录状态已失效，请重新登录");
    e.status = 401;
    throw e;
  }
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
  setSession({
    username: data.username,
    role: data.role,
    token: data.token,
    exp: data.expires_at,
  });
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
