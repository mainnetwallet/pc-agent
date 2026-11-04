import { useState, useEffect, useCallback, useRef } from "react";
import type { FormEvent, ReactNode } from "react";
import { api, setToken } from "./api";

type Task = { id: string; instruction: string; status: string };
type Status = { online?: boolean; streaming?: boolean };

const LABEL: Record<string, string> = {
  QUEUED: "Queued", RUNNING: "Running", WAITING: "Waiting",
  RECOVERING: "Recovering", COMPLETED: "Completed", FAILED: "Failed",
};
const NAV = [
  ["console", "Console"], ["chat", "Chat"], ["tasks", "Tasks"], ["apps", "Apps"],
  ["tools", "Tools"], ["settings", "Settings"],
] as const;
const logout = () => { localStorage.removeItem("pc_agent_token"); location.reload(); };

function Chip({ s }: { s: string }) {
  return <span className={`chip ${s.toLowerCase()}`}>{LABEL[s] ?? s}</span>;
}

function useTasks(filter?: string) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const reload = useCallback(async () => {
    try { setTasks((await (await api.tasks(filter)).json()).tasks ?? []); } catch { /* server unreachable */ }
  }, [filter]);
  useEffect(() => { reload(); const iv = setInterval(reload, 3000); return () => clearInterval(iv); }, [reload]);
  return { tasks, reload };
}

function Actions({ t, done }: { t: Task; done: () => void }) {
  const run = async (fn: (id: string) => Promise<Response>) => {
    try { await fn(t.id); } catch { /* the refreshed list shows the real state */ }
    done();
  };
  return (
    <span className="acts">
      {t.status === "RUNNING" && <button onClick={() => run(api.pauseTask)}>Pause</button>}
      {t.status === "WAITING" && <button onClick={() => run(api.resumeTask)}>Resume</button>}
      {["QUEUED", "RUNNING", "WAITING"].includes(t.status) && <button className="danger" onClick={() => run(api.stopTask)}>Stop</button>}
    </span>
  );
}

function Pairing({ onPaired }: { onPaired: () => void }) {
  const [name, setName] = useState("Web dashboard");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const res = await api.pair(name);
      const data = await res.json();
      if (!res.ok || !data.token) {
        const d = data?.detail;
        throw new Error(typeof d === "string" ? d : d?.[0]?.msg || `The server refused the request (HTTP ${res.status}).`);
      }
      setToken(data.token); onPaired();
    } catch (x: any) {
      setError(x.message === "Failed to fetch" ? "Can't reach the server on port 8000. Start it with “pc”, then try again." : x.message || "Pairing failed.");
    } finally { setBusy(false); }
  };
  return (
    <div className="pair">
      <form onSubmit={submit}>
        <h1>Pair this browser</h1>
        <p className="lead">This creates a revocable token so the browser can send tasks to your PC agent.</p>
        <label htmlFor="dev">Device name</label>
        <input id="dev" value={name} onChange={e => setName(e.target.value)} />
        {error && <p className="err" role="alert">{error}</p>}
        <button className="primary" disabled={busy || !name.trim()}>{busy ? "Pairing" : "Pair browser"}</button>
      </form>
    </div>
  );
}

function Monitor({ online }: { online: boolean }) {
  const [live, setLive] = useState(false);
  const [fps, setFps] = useState(4);
  const [src, setSrc] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!live) return;
    let stop = false;
    const tick = async () => {
      try {
        const url = URL.createObjectURL(await (await api.screenshot()).blob());
        if (stop) { URL.revokeObjectURL(url); return; }
        setSrc(prev => { if (prev) URL.revokeObjectURL(prev); return url; });
      } catch { /* keep the last frame */ }
    };
    tick();
    const iv = setInterval(tick, 1000 / fps);
    return () => { stop = true; clearInterval(iv); };
  }, [live, fps]);
  const toggle = async () => {
    setErr("");
    try {
      if (live) await api.streamStop(); else await api.streamStart({ fps, quality: 70, monitor: 0 });
      setLive(!live);
    } catch (x: any) { setErr(x.message || "Couldn't change the stream."); }
  };
  return (
    <figure className="monitor">
      <div className="bar">
        <span className={`dot ${live ? "on" : ""}`} />
        <strong>{live ? "Live" : "Screen off"}</strong>
        <span className="sp" />
        <label className="fps">Speed <input type="range" min={1} max={15} value={fps} onChange={e => setFps(+e.target.value)} /> {fps} fps</label>
        <button className={live ? "danger" : "primary"} onClick={toggle}>{live ? "Stop watching" : "Watch screen"}</button>
      </div>
      <div className="glass">
        {src ? <img src={src} alt="Live view of the PC screen" /> :
          <p>{online ? "Screen is off. Choose Watch screen to see what the agent sees." : "The PC agent is offline. Start it with “pc”."}</p>}
      </div>
      {err && <p className="err" role="alert">{err}</p>}
    </figure>
  );
}

function Console({ online }: { online: boolean }) {
  const { tasks, reload } = useTasks();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setSending(true); setErr("");
    try { await api.createTask(text.trim()); setText(""); reload(); }
    catch (x: any) { setErr(x.message || "Couldn't send the task."); }
    finally { setSending(false); }
  };
  return (
    <div className="console">
      <section>
        <Monitor online={online} />
        <form className="command" onSubmit={send}>
          <label htmlFor="cmd">What should the agent do?</label>
          <div className="row">
            <input id="cmd" value={text} onChange={e => setText(e.target.value)} placeholder="Open Notepad and save a note to the Desktop" autoComplete="off" />
            <button className="primary" disabled={sending || !text.trim()}>{sending ? "Sending" : "Send task"}</button>
          </div>
          {err ? <p className="err" role="alert">{err}</p>
            : <p className="hint">Tasks wait in the queue while the agent is offline. Emergency stop on the PC: Ctrl+Alt+Shift+X.</p>}
        </form>
      </section>
      <section className="queue">
        <h2>Task queue</h2>
        {tasks.length === 0 ? <p className="empty">No tasks yet. Send one and it will show up here.</p> : (
          <ul>{tasks.slice(0, 8).map(t => (
            <li key={t.id}>
              <p className="ins">{t.instruction}</p>
              <div className="meta"><Chip s={t.status} /><Actions t={t} done={reload} /></div>
            </li>
          ))}</ul>
        )}
      </section>
    </div>
  );
}

type Msg = { role: "user" | "agent"; text: string; task?: { id: string; status: string }; fail?: boolean };
const CHAT_KEY = "pc_agent_chat";

function Chat({ openTasks }: { openTasks: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try { return JSON.parse(localStorage.getItem(CHAT_KEY) || "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(msgs.slice(-50))); } catch { /* storage blocked */ }
    end.current?.scrollIntoView({ block: "end" });
  }, [msgs, busy]);
  const send = async (e: FormEvent) => {
    e.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    const history = msgs.filter(m => !m.fail).slice(-10).map(m => ({ role: m.role === "user" ? "user" : "assistant", content: m.text }));
    setMsgs(m => [...m, { role: "user", text: message }]); setText(""); setBusy(true);
    try {
      const res = await api.chat(message, history);
      const d = await res.json();
      if (!res.ok) throw new Error(typeof d?.detail === "string" ? d.detail : `The server refused the request (HTTP ${res.status}).`);
      setMsgs(m => [...m, { role: "agent", text: d.reply, task: d.task_id ? { id: d.task_id, status: d.status } : undefined }]);
    } catch (x: any) {
      setMsgs(m => [...m, { role: "agent", fail: true, text: x.message === "Failed to fetch" ? "Can't reach the server on port 8000. Start it with “pc”, then try again." : x.message || "Something went wrong." }]);
    } finally { setBusy(false); }
  };
  return (
    <div className="chat">
      <div className="log" aria-live="polite">
        {msgs.length === 0 && <p className="empty">Ask about your PC agent, or tell it what to do. For example: “What is running now?” or “Open Notepad and write a note.”</p>}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}${m.fail ? " fail" : ""}`}>
            <p>{m.text}</p>
            {m.task && <div className="made"><Chip s={m.task.status} /><span>Task {m.task.id.slice(0, 8)} created</span><button onClick={openTasks}>View tasks</button></div>}
          </div>
        ))}
        {busy && <div className="msg agent"><p className="wait">Thinking</p></div>}
        <div ref={end} />
      </div>
      <form className="compose" onSubmit={send}>
        <input value={text} onChange={e => setText(e.target.value)} placeholder="Message the agent" aria-label="Message" autoComplete="off" />
        <button className="primary" disabled={busy || !text.trim()}>Send</button>
        {msgs.length > 0 && <button type="button" onClick={() => setMsgs([])}>Clear</button>}
      </form>
    </div>
  );
}

function Tasks() {
  const [filter, setFilter] = useState("all");
  const { tasks, reload } = useTasks(filter === "all" ? undefined : filter);
  return (
    <div>
      <div className="tabs">
        {["all", "QUEUED", "RUNNING", "COMPLETED", "FAILED"].map(k => (
          <button key={k} className={filter === k ? "on" : ""} aria-pressed={filter === k} onClick={() => setFilter(k)}>{k === "all" ? "All" : LABEL[k]}</button>
        ))}
      </div>
      {tasks.length === 0 ? <p className="empty">{filter === "all" ? "No tasks yet. Send one from the Console." : "No tasks have this status."}</p> : (
        <ul className="rows">{tasks.map(t => (
          <li key={t.id}>
            <div><p className="ins">{t.instruction}</p><small>{t.id.slice(0, 8)}</small></div>
            <Chip s={t.status} /><Actions t={t} done={reload} />
          </li>
        ))}</ul>
      )}
    </div>
  );
}

function Note({ title, children }: { title: string; children: ReactNode }) {
  return <div className="note"><h2>{title}</h2><p>{children}</p></div>;
}

function Settings() {
  return (
    <div className="note">
      <h2>This browser</h2>
      <p>Paired with a revocable token. Log out to remove the token from this browser.</p>
      <button className="danger" onClick={logout}>Log out</button>
      <h2>Agent settings</h2>
      <p>The model provider, Telegram bot token and allowed Telegram IDs are set in the <code>.env</code> file on the PC. Restart with <code>pc</code> after changing them.</p>
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<"in" | "out">(() => localStorage.getItem("pc_agent_token") ? "in" : "out");
  const [view, setView] = useState("console");
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    if (auth !== "in") return;
    const load = async () => {
      try { setStatus(await (await api.status()).json()); }
      catch (e: any) { if (e.message === "Unauthorized") setAuth("out"); else setStatus(null); }
    };
    load(); const iv = setInterval(load, 4000); return () => clearInterval(iv);
  }, [auth]);

  if (auth === "out") return <Pairing onPaired={() => setAuth("in")} />;

  return (
    <div className="app">
      <header className="rail">
        <div className="brand"><span className={`dot ${status?.online ? "on" : ""}`} /><b>PC Agent</b></div>
        <nav>
          {NAV.map(([k, label]) => (
            <button key={k} className={view === k ? "on" : ""} aria-current={view === k ? "page" : undefined} onClick={() => setView(k)}>{label}</button>
          ))}
        </nav>
        <p className="state">{status ? (status.online ? "PC online" : "PC offline") : "Server unreachable"}</p>
      </header>
      <main>
        <h1>{NAV.find(n => n[0] === view)?.[1]}</h1>
        {view === "console" && <Console online={!!status?.online} />}
        {view === "chat" && <Chat openTasks={() => setView("tasks")} />}
        {view === "tasks" && <Tasks />}
        {view === "apps" && <Note title="Installed apps">Nothing to list yet. The agent discovers apps on the PC but doesn't report them to this dashboard. To use one, name it in a task, for example “Open Chrome and search for today's weather.”</Note>}
        {view === "tools" && <Note title="Learned tools">Nothing to list yet. The agent registers tools as it discovers applications, but this dashboard can't show them yet.</Note>}
        {view === "settings" && <Settings />}
      </main>
    </div>
  );
}
