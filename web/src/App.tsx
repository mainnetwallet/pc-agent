import React, { useState, useEffect, useCallback, useRef } from "react";
import { api, setToken } from "./api";

// ---------- small helpers ----------
function statusColor(s: string) {
  switch (s) {
    case "RUNNING": return "text-green-600 dark:text-green-400";
    case "QUEUED": return "text-yellow-600 dark:text-yellow-400";
    case "COMPLETED": return "text-green-600 dark:text-green-400";
    case "FAILED": return "text-red-600 dark:text-red-400";
    case "WAITING": return "text-blue-600 dark:text-blue-400";
    case "RECOVERING": return "text-orange-600 dark:text-orange-400";
    default: return "text-gray-500";
  }
}

// ===================================================================== PAIRING
function Pairing({ onPaired }: { onPaired: () => void }) {
  const [name, setName] = useState("Web Dashboard");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const pair = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError("");
    try {
      const res = await api.pair(name);
      const data = await res.json();
      setToken(data.token);
      onPaired();
    } catch (err: any) {
      setError(err.message || "Pairing failed");
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className="flex h-screen items-center center bg-gray-50">
      <div className="max-w-md w-full p-6 bg-white rounded shadow">
        <h2 className="text-xl font-bold mb-4">Pair this device</h2>
        <p className="text-sm text-gray-600 mb-4">Generate a revocable token to connect to the PC Agent.</p>
        {error && <div className="mb-3 p-2 bg-red-50 text-red-700 rounded text-sm">{error}</div>}
        <form onSubmit={pair}>
          <label className="block text-sm font-medium mb-1">Device name</label>
          <input className="w-full border p-2 rounded mb-4" value={name} onChange={e => setName(e.target.value)} />
          <button className="w-full bg-blue-600 text-white py-2 rounded" disabled={loading}>
            {loading ? "Pairing..." : "Pair Device"}
          </button>
        </form>
      </div>
    </div>
  );
}

// ===================================================================== LAYOUT
function Layout({ children, nav, setNav }: { children: React.ReactNode, nav: string, setNav: (v: string) => void }) {
  return (
    <div className="flex h-screen bg-gray-50">
      <aside className="w-64 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 font-bold text-lg">PC Agent</div>
        <nav className="flex-1 overflow-y-auto p-2 space-y-1">
          {[
            { key: "dashboard", label: "Dashboard" },
            { key: "chat", label: "Chat" },
            { key: "tasks", label: "Task Manager" },
            { key: "livepc", label: "Live PC" },
            { key: "apps", label: "Applications" },
            { key: "tools", label: "Tools" },
            { key: "settings", label: "Settings" },
          ].map(item => (
            <button key={item.key}
              onClick={() => setNav(item.key)}
              className={`w-full text-left px-3 py-2 rounded ${nav === item.key ? "bg-blue-100 text-blue-700 font-medium" : "text-gray-700 hover:bg-gray-100"}`}
            >{item.label}</button>
          ))}
        </nav>
        <div className="p-4 border-t text-xs text-gray-500">Agent v1.0</div>
      </aside>
      <main className="flex-1 overflow-y-auto p-6">{children}</main>
    </div>
  );
}

// ===================================================================== DASHBOARD
function Dashboard() {
  const [status, setStatus] = useState<any>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const load = async () => {
    try {
      const [s, t] = await Promise.all([api.status(), api.tasks()]);
      setStatus(await s.json());
      setTasks((await t.json()).tasks);
    } catch { } finally { setLoading(false); }
  };
  useEffect(() => { load(); const iv = setInterval(load, 5000); return () => clearInterval(iv); }, []);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <span className={`px-2 py-1 rounded-full text-xs font-medium ${status?.online ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
          {status?.online ? "PC Online" : "PC Offline"}
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white p-4 rounded shadow border">
          <div className="text-gray-500 text-sm uppercase font-semibold">Agent Status</div>
          <div className="text-2xl font-bold">{status?.online ? "Online" : "Offline"}</div>
        </div>
        <div className="bg-white p-4 rounded shadow border">
          <div className="text-gray-500 text-sm uppercase font-semibold">Streaming</div>
          <div className="text-2xl font-bold">{status?.streaming ? "Live" : "Idle"}</div>
        </div>
        <div className="bg-white p-4 rounded shadow border">
          <div className="text-gray-500 text-sm uppercase font-semibold">Tasks</div>
          <div className="text-2xl font-bold">{tasks.length}</div>
        </div>
      </div>
      <div className="bg-white rounded shadow border">
        <div className="p-4 border-b font-semibold">Recent Tasks</div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="bg-gray-50"><th className="p-2">ID</th><th className="p-2">Instruction</th><th className="p-2">Status</th></tr></thead>
            <tbody>
              {tasks.slice(0, 10).map(t => (
                <tr key={t.id} className="border-b hover:bg-gray-50">
                  <td className="p-2 text-gray-500">{t.id.slice(0, 8)}</td>
                  <td className="p-2">{t.instruction}</td>
                  <td className="p-2"><span className={`px-2 py-0.5 rounded text-xs font-medium ${statusColor(t.status)}`}>{t.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ===================================================================== CHAT
function Chat() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<{role: string, text: string}[]>([]);
  const [loading, setLoading] = useState(false);
  const send = async () => {
    if (!input.trim()) return;
    const userMsg = input;
    setInput("");
    setMessages([...messages, { role: "user", text: userMsg }]);
    setLoading(true);
    try {
      const res = await api.createTask(userMsg);
      const data = await res.json();
      setMessages([...messages, { role: "agent", text: `Task created: ${data.task_id} (${data.status}). I'll work on it and report back.` }]);
    } catch (err: any) {
      setMessages([...messages, { role: "agent", text: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-50/50 rounded-lg border">
        {messages.map((m, i) => (
          <div key={i} className={`p-3 rounded-lg max-w-[80%] ${m.role === "user" ? "ml-auto bg-blue-600 text-white" : "mr-auto bg-white border"}`}>
            {m.text}
          </div>
        ))}
        {loading && <div className="mr-auto p-3 text-sm text-gray-500">Agent typing...</div>}
      </div>
      <div className="p-3 border-t">
        <div className="flex gap-2">
          <input className="flex-1 border p-2 rounded" value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === "Enter" && send()} placeholder="Send a task..." />
          <button className="bg-blue-600 text-white px-4 py-2 rounded" onClick={send} disabled={loading}>Send</button>
        </div>
      </div>
    </div>
  );
}

// ===================================================================== TASK MANAGER
function TaskManager() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [filter, setFilter] = useState("all");
  const load = async () => {
    try {
      const res = await api.tasks(filter === "all" ? undefined : filter);
      setTasks((await res.json()).tasks);
    } catch { }
  };
  useEffect(() => { load(); const iv = setInterval(load, 3000); return () => clearInterval(iv); }, [filter]);
  const act = async (id: string, action: "stop" | "pause" | "resume") => {
    try { await api[`${action}Task`](id); load(); } catch { }
  };
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Task Manager</h1>
      <div className="flex gap-2 mb-4">
        {[
          ["all", "All"],
          ["QUEUED", "Queued"],
          ["RUNNING", "Running"],
          ["COMPLETED", "Completed"],
          ["FAILED", "Failed"],
        ].map(([k, label]) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            className={`px-3 py-1 rounded text-sm border ${
              filter === k
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 border-gray-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="bg-white rounded shadow border overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead><tr className="bg-gray-50"><th className="p-2">ID</th><th className="p-2">Instruction</th><th className="p-2">Status</th><th className="p-2">Actions</th></tr></thead>
          <tbody>
            {tasks.map(t => (
              <tr key={t.id} className="border-b">
                <td className="p-2 text-gray-500">{t.id.slice(0,8)}</td>
                <td className="p-2">{t.instruction}</td>
                <td className="p-2"><span className={`px-2 py-0.5 rounded text-xs font-medium ${statusColor(t.status)}`}>{t.status}</span></td>
                <td className="p-2">
                  {t.status === "RUNNING" && <button onClick={() => act(t.id,"pause")} className="px-2 py-1 text-xs border rounded text-blue-600 border-blue-200 hover:bg-blue-50">Pause</button>}
                  {t.status === "WAITING" && <button onClick={() => act(t.id,"resume")} className="px-2 py-1 text-xs border rounded text-blue-600 border-blue-200 hover:bg-blue-50">Resume</button>}
                  {(t.status === "QUEUED" || t.status === "RUNNING" || t.status === "WAITING") && <button onClick={() => act(t.id,"stop")} className="px-2 py-1 text-xs border rounded text-red-600 border-red-200 hover:bg-red-50">Stop</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ===================================================================== LIVE PC
function LivePC() {
  const [streaming, setStreaming] = useState(false);
  const [fps, setFps] = useState(5);
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const poll = async () => {
    try {
      const res = await api.screenshot();
        const arr = await res.arrayBuffer();
      const base64 = btoa(String.fromCharCode(...new Uint8Array(arr)));
      setScreenshot(`data:image/jpeg;base64,${base64}`);
    } catch { }
  };
  useEffect(() => {
    if (!streaming) return;
    poll();
    const iv = setInterval(poll, 1000 / fps);
    return () => clearInterval(iv);
  }, [streaming, fps]);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Live PC View</h1>
      <div className="flex gap-3 items-center">
        <button onClick={() => { api.streamStart({fps, quality:70, monitor:0}).then(() => setStreaming(true)); }} className="bg-green-600 text-white px-3 py-1 rounded">Start Stream</button>
        <button onClick={() => { api.streamStop().then(() => setStreaming(false)); }} className="bg-red-600 text-white px-3 py-1 rounded">Stop Stream</button>
        <label>FPS <input type="range" min="1" max="15" value={fps} onChange={e => setFps(Number(e.target.value))} className="ml-2" /></label>
        <span className="text-sm text-gray-500">{streaming ? "LIVE" : "IDLE"}</span>
      </div>
      <div className="relative bg-black rounded overflow-hidden border max-w-4xl">
        {screenshot ? (
          <img ref={imgRef} src={screenshot} alt="PC screen" className="w-full max-h-[60vh] object-contain" />
        ) : (
          <div className="h-[60vh] flex flex-col items-center justify-center text-gray-500">No stream. Click Start Stream.</div>
        )}
      </div>
      <p className="text-sm text-gray-500">Live PC screenshots polled from the agent. Use WebRTC for low-latency video.</p>
    </div>
  );
}

// ===================================================================== APPS
function AppManager() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Application Manager</h1>
      <div className="bg-white rounded shadow border p-4">
        <p className="text-sm text-gray-500">Application discovery and launch controls come from the Windows agent. Installed apps are discovered dynamically via the registry and start menu.</p>
      </div>
    </div>
  );
}

// ===================================================================== TOOLS
function ToolManager() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Tool Manager</h1>
      <div className="bg-white rounded shadow border p-4">
        <p className="text-sm text-gray-500">Registered tools and discovered capabilities are managed by the Windows agent's Dynamic Tool Registry.</p>
      </div>
    </div>
  );
}

// ===================================================================== SETTINGS
function Settings() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Settings</h1>
      <div className="bg-white rounded shadow border p-4">
        <div className="mb-3"><label className="block text-sm font-medium">LLM Provider</label><select className="border p-1 rounded"><option>openai</option><option>anthropic</option><option>custom</option><option>mock</option></select></div>
        <div className="mb-3"><label className="block text-sm font-medium">Telegram Bot Token</label><input className="border w-full p-1 rounded" type="password" /></div>
        <div className="mb-3"><label className="block text-sm font-medium">Allowed Telegram IDs</label><input className="border w-full p-1 rounded" placeholder="comma-separated numeric IDs" /></div>
      </div>
    </div>
  );
}

// ===================================================================== APP
export default function App() {
  const [paired, setPaired] = useState(false);
  const [nav, setNav] = useState("dashboard");
  if (!paired) return <Pairing onPaired={() => setPaired(true)} />;
  const viewMap: Record<string, React.ReactNode> = {
    dashboard: <Dashboard />, chat: <Chat />, tasks: <TaskManager />,
    livepc: <LivePC />, apps: <AppManager />, tools: <ToolManager />, settings: <Settings />,
  };
  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between shadow-sm">
        <div className="font-bold text-lg">PC Agent Dashboard</div>
        <div className="flex gap-4">
          <button onClick={() => { localStorage.removeItem("pc_agent_token"); location.reload(); }} className="text-sm text-red-600 hover:underline">Logout</button>
        </div>
      </nav>
      <Layout nav={nav} setNav={setNav}>
        {viewMap[nav] || <Dashboard />}
      </Layout>
    </div>
  );
}
