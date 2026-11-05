import { useState, useEffect, useCallback, useRef } from "react";
import type { FormEvent, ReactNode } from "react";
import { api, setToken } from "./api";

type Task = { id: string; instruction: string; status: string; result?: string | null };
type Status = { online?: boolean; streaming?: boolean };

const LABEL: Record<string, string> = {
  QUEUED: "Queued", RUNNING: "Running", WAITING: "Waiting",
  RECOVERING: "Recovering", COMPLETED: "Completed", FAILED: "Failed",
  NEEDS_USER_ACTION: "Needs your reply", CANCELLED: "Stopped", PLANNING: "Planning", DISPATCHED: "Sent to PC",
};
const NAV = [
  ["console", "Console"], ["chat", "Chat"], ["action", "Action"], ["tasks", "Tasks"], ["apps", "Apps"],
  ["tools", "Tools"], ["settings", "Settings"],
] as const;
/** Hash routes: #/<view> or #/action/<task id>. Every task chat has its own link. */
type Route = { view: string; id: string };
const VIEWS: string[] = NAV.map(n => n[0]);
const readRoute = (): Route => {
  const [v = "", id = ""] = location.hash.replace(/^#\/?/, "").split("/");
  let tid = id; try { tid = decodeURIComponent(id); } catch { /* keep raw */ }
  return { view: VIEWS.includes(v) ? v : "console", id: tid };
};
const go = (view: string, id = "") => { location.hash = `/${view}${id ? "/" + encodeURIComponent(id) : ""}`; };
function useRoute() {
  const [route, setRoute] = useState<Route>(readRoute);
  useEffect(() => {
    const on = () => setRoute(readRoute());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
const logout = () => { localStorage.removeItem("pc_agent_token"); location.reload(); };

function Chip({ s }: { s: string }) {
  return <span className={`chip ${s.toLowerCase()}`}>{LABEL[s] ?? s}</span>;
}

function useTasks(filter?: string) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loaded, setLoaded] = useState(false);
  const reload = useCallback(async () => {
    try { setTasks((await (await api.tasks(filter)).json()).tasks ?? []); setLoaded(true); } catch { /* server unreachable */ }
  }, [filter]);
  useEffect(() => { reload(); const iv = setInterval(reload, 3000); return () => clearInterval(iv); }, [reload]);
  return { tasks, reload, loaded };
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
      {["QUEUED", "RUNNING", "WAITING", "NEEDS_USER_ACTION"].includes(t.status) && <button className="danger" onClick={() => run(api.stopTask)}>Stop</button>}
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

function Console({ online, openTasks, openChat }: { online: boolean; openTasks: () => void; openChat: (id: string) => void }) {
  const { tasks, reload } = useTasks();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setSending(true); setErr("");
    try {
      const res = await api.createTask(text.trim());
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(typeof d?.detail === "string" ? d.detail : `The server refused the task (HTTP ${res.status}).`);
      }
      setText(""); reload();
    }
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
              <div className="meta"><Chip s={t.status} />{t.status === "NEEDS_USER_ACTION" && <button className="primary" onClick={() => openChat(t.id)}>Reply</button>}<Actions t={t} done={reload} /></div>
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

const THREAD_KEY = (id: string) => `pc_agent_thread_${id}`;
type Turn = { role: "agent" | "user"; text: string };
const loadThread = (id: string): Turn[] => {
  try { const v = JSON.parse(localStorage.getItem(THREAD_KEY(id)) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
};
const saveThread = (id: string, turns: Turn[]) => {
  try { localStorage.setItem(THREAD_KEY(id), JSON.stringify(turns.slice(-40))); } catch { /* storage blocked */ }
};
/** Task ids whose reply was just sent -> when. Until the status moves on we show "sent" instead of the old question. */
const SENT = new Map<string, number>();
const SENT_TTL = 20000;
/** What the agent asked: the first paragraph of the task result (the rest is internal evidence). */
const askedQuestion = (t: Task) => (t.result ?? "").trim().split("\n\n")[0].trim();

function NavBadge() {
  const { tasks } = useTasks("NEEDS_USER_ACTION");
  return tasks.length ? <span className="badge" aria-label={`${tasks.length} tasks need your reply`}>{tasks.length}</span> : null;
}

function TaskChat({ t, reload }: { t: Task; reload: () => void }) {
  const [turns, setTurns] = useState<Turn[]>(() => loadThread(t.id));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const asking = t.status === "NEEDS_USER_ACTION";
  const q = askedQuestion(t);
  const end = useRef<HTMLDivElement>(null);
  // The status moved on, so the agent got the reply: forget the "sent" marker.
  useEffect(() => { if (!asking) SENT.delete(t.id); }, [asking, t.id]);
  const sentAt = SENT.get(t.id);
  const waiting = asking && sentAt !== undefined && Date.now() - sentAt < SENT_TTL;
  const timedOut = asking && sentAt !== undefined && !waiting;
  const shown: Turn[] = [{ role: "user", text: t.instruction }, ...turns];
  if (asking && sentAt === undefined && q) shown.push({ role: "agent", text: q });
  // A finished task shows its result. A cancelled one shows the open question only if the user never replied.
  const showResult = ["COMPLETED", "FAILED"].includes(t.status) || (t.status === "CANCELLED" && turns.length === 0);
  if (showResult && q && shown[shown.length - 1]?.text !== q) shown.push({ role: "agent", text: q });

  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [shown.length, waiting]);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    setBusy(true); setErr("");
    try {
      const res = await api.taskMessage(t.id, message);
      if (!res.ok) throw new Error(`The server refused the reply (HTTP ${res.status}).`);
      const cancelled = !!(await res.json().catch(() => ({})))?.cancelled;
      const next: Turn[] = [...turns];
      if (asking && q && next[next.length - 1]?.text !== q) next.push({ role: "agent", text: q });
      next.push({ role: "user", text: message });
      if (cancelled) next.push({ role: "agent", text: "Task cancelled." });
      setTurns(next); saveThread(t.id, next);
      if (asking && !cancelled) SENT.set(t.id, Date.now());
      setText(""); reload();
    } catch (x: any) {
      setErr(x.message === "Failed to fetch" ? "Can't reach the server on port 8000. Start it with “pc”, then try again." : x.message || "Couldn't send the reply.");
    } finally { setBusy(false); }
  };

  return (
    <div className="tchat">
      <div className="tlog" aria-live="polite">
        {shown.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}><p>{m.text}</p></div>
        ))}
        <div ref={end} />
      </div>
      {waiting && <p className="hint">Reply sent. The agent is continuing the task…</p>}
      {timedOut && <p className="err" role="alert">The PC hasn't picked up the reply yet. Check that the agent is running, then send it again.</p>}
      {asking && !waiting && (
        <form className="compose" onSubmit={send}>
          <input value={text} onChange={e => setText(e.target.value)} placeholder="Type your answer for the agent" aria-label="Your answer to the agent" autoComplete="off" />
          <button className="primary" disabled={busy || !text.trim()}>{busy ? "Sending" : "Send reply"}</button>
        </form>
      )}
      {err && <p className="err" role="alert">{err}</p>}
    </div>
  );
}

function ActionPage({ selected }: { selected: string }) {
  const { tasks, reload, loaded } = useTasks();
  const [copied, setCopied] = useState(false);
  const asking = tasks.filter(t => t.status === "NEEDS_USER_ACTION");
  const earlier = tasks.filter(t => t.status !== "NEEDS_USER_ACTION" && (loadThread(t.id).length > 0 || t.id === selected));
  // No id in the link: open the first task that needs an answer (desktop shows list + chat side by side).
  const current = selected ? tasks.find(t => t.id === selected) : asking[0];
  const link = `${location.origin}${location.pathname}#/action/${encodeURIComponent(current?.id ?? "")}`;
  const copy = async () => {
    try { await navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  const row = (t: Task) => (
    <li key={t.id}>
      <button className={`thread${current?.id === t.id ? " on" : ""}`} aria-current={current?.id === t.id ? "true" : undefined} onClick={() => go("action", t.id)}>
        <span className="ins">{t.instruction}</span>
        <span className="tmeta"><Chip s={t.status} /><code>{t.id.slice(0, 8)}</code></span>
      </button>
    </li>
  );
  return (
    <div className={`inbox${selected ? " open" : ""}`}>
      <aside className="threads" aria-label="Task chats">
        <h2>Needs your reply{asking.length > 0 && <span className="badge">{asking.length}</span>}</h2>
        {asking.length === 0 ? <p className="empty">Nothing is waiting for you. When the agent needs an answer, its chat appears here.</p> : <ul>{asking.map(row)}</ul>}
        {earlier.length > 0 && <><h2>Earlier chats</h2><ul>{earlier.map(row)}</ul></>}
      </aside>
      <section className="room" aria-label="Task chat">
        {!current ? (
          <p className="empty">{selected ? (loaded ? "No task with this id. It may have been deleted." : "Loading…") : "Select a task to open its chat."}</p>
        ) : (
          <>
            <div className="roomhead">
              <button className="back" onClick={() => go("action")}>← All chats</button>
              <p className="ins">{current.instruction}</p>
              <div className="meta">
                <Chip s={current.status} />
                <span className="cid">Chat ID <code>{current.id}</code></span>
                <button onClick={copy}>{copied ? "Copied" : "Copy link"}</button>
                <Actions t={current} done={reload} />
              </div>
            </div>
            <TaskChat key={current.id} t={current} reload={reload} />
          </>
        )}
      </section>
    </div>
  );
}

function Tasks({ openChat }: { openChat: (id: string) => void }) {
  const [filter, setFilter] = useState("all");
  const { tasks, reload } = useTasks(filter === "all" ? undefined : filter);
  // Tasks that wait for the user come first so they are never buried.
  const ordered = [...tasks].sort((a, b) => Number(b.status === "NEEDS_USER_ACTION") - Number(a.status === "NEEDS_USER_ACTION"));
  return (
    <div>
      <div className="tabs">
        {["all", "NEEDS_USER_ACTION", "QUEUED", "RUNNING", "COMPLETED", "FAILED"].map(k => (
          <button key={k} className={filter === k ? "on" : ""} aria-pressed={filter === k} onClick={() => setFilter(k)}>{k === "all" ? "All" : k === "NEEDS_USER_ACTION" ? "Needs reply" : LABEL[k]}</button>
        ))}
      </div>
      {ordered.length === 0 ? <p className="empty">{filter === "all" ? "No tasks yet. Send one from the Console." : "No tasks have this status."}</p> : (
        <ul className="rows">{ordered.map(t => (
          <li key={t.id} className={t.status === "NEEDS_USER_ACTION" ? "ask" : ""}>
            <div><p className="ins">{t.instruction}</p><small>{t.id.slice(0, 8)}</small></div>
            <Chip s={t.status} /><Actions t={t} done={reload} />
            {(t.status === "NEEDS_USER_ACTION" || loadThread(t.id).length > 0) && (
              <button className={t.status === "NEEDS_USER_ACTION" ? "primary" : ""} onClick={() => openChat(t.id)}>{t.status === "NEEDS_USER_ACTION" ? "Reply" : "Open chat"}</button>
            )}
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
  const { view, id: chatId } = useRoute();
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
            <button key={k} className={view === k ? "on" : ""} aria-current={view === k ? "page" : undefined} onClick={() => go(k)}>{label}{k === "action" && <NavBadge />}</button>
          ))}
        </nav>
        <p className="state">{status ? (status.online ? "PC online" : "PC offline") : "Server unreachable"}</p>
      </header>
      <main>
        <h1>{NAV.find(n => n[0] === view)?.[1]}</h1>
        {view === "console" && <Console online={!!status?.online} openTasks={() => go("tasks")} openChat={id => go("action", id)} />}
        {view === "chat" && <Chat openTasks={() => go("tasks")} />}
        {view === "action" && <ActionPage selected={chatId} />}
        {view === "tasks" && <Tasks openChat={id => go("action", id)} />}
        {view === "apps" && <Note title="Installed apps">Nothing to list yet. The agent discovers apps on the PC but doesn't report them to this dashboard. To use one, name it in a task, for example “Open Chrome and search for today's weather.”</Note>}
        {view === "tools" && <Note title="Learned tools">Nothing to list yet. The agent registers tools as it discovers applications, but this dashboard can't show them yet.</Note>}
        {view === "settings" && <Settings />}
      </main>
    </div>
  );
}
