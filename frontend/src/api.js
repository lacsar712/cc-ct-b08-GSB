const TOKEN_KEY = "cnc_offset_token";
const USER_KEY = "cnc_offset_user";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getUser() {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { ...options, headers });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg = data?.detail || data?.message || `请求失败 (${res.status})`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export function login(username, password) {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function fetchSubmissions() {
  return request("/submissions");
}

export function fetchSubmission(id) {
  return request(`/submissions/${id}`);
}

export function createSubmission(tool_code, offset_um) {
  return request("/submissions", {
    method: "POST",
    body: JSON.stringify({ tool_code, offset_um: Number(offset_um) }),
  });
}

// ---- 冻结归档台 ----
export function fetchPackages() {
  return request("/archive/packages");
}

export function fetchPackage(id) {
  return request(`/archive/packages/${id}`);
}

export function createPackage(name) {
  return request("/archive/packages", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function queueEntry(packageId, submissionId) {
  return request(`/archive/packages/${packageId}/entries`, {
    method: "POST",
    body: JSON.stringify({ submission_id: submissionId }),
  });
}

export function signEntry(entryId) {
  return request(`/archive/entries/${entryId}/sign`, { method: "POST" });
}

export function removeEntry(entryId) {
  return request(`/archive/entries/${entryId}`, { method: "DELETE" });
}

export function sealPackage(packageId) {
  return request(`/archive/packages/${packageId}/seal`, { method: "POST" });
}
