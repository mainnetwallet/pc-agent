export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
let token = localStorage.getItem("pc_agent_token") || "";

export function setToken(t: string) { token = t; localStorage.setItem("pc_agent_token", t); }

async function request(path: string, opts: any = {}) {
  const headers: any = { ...opts.headers };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (res.status === 401) throw new Error("Unauthorized");
  return res;
}

export const api = {
  pair: (name: string) => request("/devices/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(name) /* server declares `name: str = Body(...)`, so it expects the bare JSON string */ }),
  me: () => request("/devices/me"),
  status: () => request("/pc/status"),
  screenshot: () => request("/pc/screenshot"),
  streamStart: (cfg: any) => request("/pc/stream/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) }),
  streamStop: () => request("/pc/stream/stop", { method: "POST" }),
  tasks: (status?: string) => request("/tasks" + (status ? `?status=${status}` : "")),
  getTask: (id: string) => request(`/tasks/${id}`),
  createTask: (instruction: string) => request("/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instruction }) }),
  stopTask: (id: string) => request(`/tasks/${id}/stop`, { method: "POST" }),
  deleteTask: (id: string) => request(`/tasks/${id}`, { method: "DELETE" }),
  pauseTask: (id: string) => request(`/tasks/${id}/pause`, { method: "POST" }),
  resumeTask: (id: string) => request(`/tasks/${id}/resume`, { method: "POST" }),
  taskMessage: (id: string, message: string) => request(`/tasks/${id}/message`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) }),
  chat: (message: string, history: { role: string; content: string }[]) => request("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, history }) }),
};
