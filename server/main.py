"""FastAPI relay server.

Provides REST APIs, WebSockets, WebRTC signaling, device pairing, authentication,
task synchronization, heartbeats, and notifications for the PC Agent.

Endpoints:
  POST   /tasks
  GET    /tasks
  GET    /tasks/{id}
  POST   /tasks/{id}/message
  POST   /tasks/{id}/stop
  POST   /tasks/{id}/pause
  POST   /tasks/{id}/resume
  GET    /pc/status
  GET    /pc/screenshot
  POST   /pc/stream/start
  POST   /pc/stream/stop
  POST   /devices/pair
  GET    /devices/me
  POST   /chat
  POST   /auth/token
  WebSocket /ws/{device_token}
  POST   /webrtc/signal
"""

import asyncio
import json
import logging
import os
import secrets
import sys
import time
import uuid
from typing import Dict, Optional

# Make the agent package importable (screen.capture, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import get_conn, USE_POSTGRES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

app = FastAPI(title="PC Agent Relay", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RELAY_API_KEY = os.getenv("RELAY_API_KEY", "")

# --------------------------------------------------------------------------- models
class TaskCreate(BaseModel):
    instruction: str
    priority: int = 0
    scheduled_at: Optional[float] = None
    source: str = "web"

class TaskMessage(BaseModel):
    message: str

class StreamStart(BaseModel):
    fps: int = 5
    quality: int = 60
    monitor: int = 0

# --------------------------------------------------------------------------- auth
def verify_api_key(x_api_key: Optional[str] = None):
    if RELAY_API_KEY and x_api_key != RELAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True

def verify_token(authorization: Optional[str] = None):
    # Device token auth (pairing). Simple bearer token.
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        return _validate_device_token(token)
    return None

def _validate_device_token(token: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT id FROM devices WHERE token=%s AND paired=TRUE", (token,))
        else:
            cur.execute("SELECT id FROM devices WHERE token=? AND paired=TRUE", (token,))
        return cur.fetchone() is not None
    finally:
        conn.close()

# --------------------------------------------------------------------------- devices / pairing
@app.post("/devices/pair")
def pair_device(name: str = Body(...)):
    """Pair a device (web/mobile/Android) and issue a revocable token."""
    device_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    conn = get_conn()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("INSERT INTO devices(id, name, paired, token, created_at, last_seen) VALUES(%s,%s,TRUE,%s,%s,%s)",
                        (device_id, name, token, time.time(), time.time()))
        else:
            cur.execute("INSERT INTO devices(id, name, paired, token, created_at, last_seen) VALUES(?,?,TRUE,?,?,?)",
                        (device_id, name, token, time.time(), time.time()))
        conn.commit()
    finally:
        conn.close()
    return {"device_id": device_id, "token": token, "paired": True}

@app.get("/devices/me")
def device_me(authorization: Optional[str] = None):
    dev = verify_token(authorization)
    if not dev:
        raise HTTPException(status_code=401, detail="Not paired")
    return {"paired": True}

# --------------------------------------------------------------------------- tasks
@app.post("/tasks")
def create_task(task: TaskCreate, _auth=Depends(verify_api_key)):
    task_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("INSERT INTO tasks(id, instruction, status, plan, result, error, created_at, updated_at, source, metadata) VALUES(%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s,%s)",
                        (task_id, task.instruction, None, None, None, time.time(), time.time(), task.source, json.dumps({})))
        else:
            cur.execute("INSERT INTO tasks(id, instruction, status, plan, result, error, created_at, updated_at, source, metadata) VALUES(?,?,'QUEUED',?,?,?,?,?,?,?)",
                        (task_id, task.instruction, None, None, None, time.time(), time.time(), task.source, json.dumps({})))
        conn.commit()
    finally:
        conn.close()
    _broadcast({"type": "task.created", "task_id": task_id, "instruction": task.instruction})
    _run_async(_dispatch_queued())
    return {"task_id": task_id, "status": "QUEUED"}

@app.get("/tasks")
def list_tasks(status: Optional[str] = Query(None), _auth=Depends(verify_api_key)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if status:
            if USE_POSTGRES:
                cur.execute("SELECT * FROM tasks WHERE status=%s ORDER BY created_at DESC", (status,))
            else:
                cur.execute("SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC", (status,))
        else:
            cur.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        out = [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()
    return {"tasks": out}

@app.get("/tasks/{task_id}")
def get_task(task_id: str, _auth=Depends(verify_api_key)):
    conn = get_conn()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT * FROM tasks WHERE id=%s", (task_id,))
        else:
            cur.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))

@app.post("/tasks/{task_id}/message")
def task_message(task_id: str, msg: TaskMessage, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.message", "task_id": task_id, "message": msg.message})
    _run_async(_send_agent({"type": "message", "task_id": task_id, "message": msg.message}))
    return {"ok": True}

@app.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.stop", "task_id": task_id})
    _run_async(_send_agent({"type": "stop", "task_id": task_id}))
    return {"ok": True}

@app.post("/tasks/{task_id}/pause")
def pause_task(task_id: str, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.pause", "task_id": task_id})
    _run_async(_send_agent({"type": "pause", "task_id": task_id}))
    return {"ok": True}

@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.resume", "task_id": task_id})
    _run_async(_send_agent({"type": "resume", "task_id": task_id}))
    return {"ok": True}

# --------------------------------------------------------------------------- PC status / screenshot / stream
@app.get("/pc/status")
def pc_status(_auth=Depends(verify_api_key)):
    return {
        "online": _agent["ws"] is not None,
        "agent_id": _agent["id"] or os.getenv("AGENT_ID", "pc-agent"),
        "streaming": _stream_state.get("active", False),
    }

@app.get("/pc/screenshot")
def pc_screenshot(_auth=Depends(verify_api_key)):
    from screen.capture import capture_engine
    path, jpeg = capture_engine.capture()
    if jpeg is None:
        raise HTTPException(status_code=500, detail="Capture failed")
    from fastapi.responses import Response
    return Response(content=jpeg, media_type="image/jpeg")

_stream_state = {"active": False, "fps": 5, "quality": 60, "monitor": 0}

@app.post("/pc/stream/start")
def stream_start(cfg: StreamStart, _auth=Depends(verify_api_key)):
    _stream_state.update({"active": True, "fps": cfg.fps, "quality": cfg.quality, "monitor": cfg.monitor})
    from screen.capture import capture_engine
    capture_engine.set_fps(cfg.fps)
    capture_engine.set_quality(cfg.quality)
    capture_engine.set_monitor(cfg.monitor)
    _broadcast({"type": "pc.stream.status", "active": True, "fps": cfg.fps})
    return {"ok": True, "streaming": True}

@app.post("/pc/stream/stop")
def stream_stop(_auth=Depends(verify_api_key)):
    _stream_state["active"] = False
    _broadcast({"type": "pc.stream.status", "active": False})
    return {"ok": True, "streaming": False}

# --------------------------------------------------------------------------- WebRTC signaling
@app.post("/webrtc/signal")
def webrtc_signal(sdp: dict = Body(...), _auth=Depends(verify_api_key)):
    """Relay WebRTC signaling offer/answer/candidate between devices."""
    target = sdp.get("target")
    _broadcast({"type": "webrtc.signal", "to": target, "data": sdp}, to_device=target)
    return {"ok": True}

# --------------------------------------------------------------------------- WebSocket
class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, device_id: str):
        await websocket.accept()
        self.active[device_id] = websocket

    def disconnect(self, device_id: str):
        self.active.pop(device_id, None)

    async def send(self, device_id: str, message: dict):
        ws = self.active.get(device_id)
        if ws:
            await websocket_send(ws, message)

    async def broadcast(self, message: dict, to_device: Optional[str] = None):
        payload = json.dumps(message)
        if to_device:
            ws = self.active.get(to_device)
            if ws:
                await websocket_send(ws, payload)
        else:
            for ws in list(self.active.values()):
                await websocket_send(ws, payload)


manager = ConnectionManager()

async def websocket_send(ws: WebSocket, data):
    try:
        if isinstance(data, str):
            await ws.send_text(data)
        else:
            await ws.send_text(json.dumps(data))
    except Exception:
        pass


@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    await manager.connect(websocket, device_id)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                msg = {"raw": data}
            # Echo task events back; in a full system this routes to the agent
            await manager.send(device_id, {"type": "ack", "received": msg})
    except WebSocketDisconnect:
        manager.disconnect(device_id)


# --------------------------------------------------------------------------- agent link
_loop: Optional[asyncio.AbstractEventLoop] = None
_agent = {"ws": None, "id": None, "last_seen": 0.0}
FINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED")


def _run_async(coro):
    """Schedule a coroutine on the server loop from sync (threadpool) or async code."""
    if _loop is None:
        coro.close()
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _loop.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, _loop)


def _broadcast(message: dict, to_device: Optional[str] = None):
    _run_async(manager.broadcast(message, to_device))


async def _send_agent(msg: dict) -> bool:
    ws = _agent["ws"]
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(msg))
        return True
    except Exception:
        return False


def _q(sql: str) -> str:
    return sql.replace("?", "%s") if USE_POSTGRES else sql


def _queued_tasks():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, instruction FROM tasks WHERE status='QUEUED' ORDER BY created_at ASC")
        return [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        conn.close()


def _update_task(task_id: str, status: str, result=None, error=None):
    now = time.time()
    done = now if status in FINAL_STATES else None
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_q("UPDATE tasks SET status=?, result=COALESCE(?, result), error=COALESCE(?, error), "
                       "updated_at=?, completed_at=COALESCE(?, completed_at) WHERE id=?"),
                    (status, result, error, now, done, task_id))
        conn.commit()
    finally:
        conn.close()


async def _dispatch_queued():
    """Send every QUEUED task to the connected agent."""
    if _agent["ws"] is None:
        return
    for tid, instruction in await asyncio.to_thread(_queued_tasks):
        if await _send_agent({"type": "task", "task_id": tid, "instruction": instruction}):
            await asyncio.to_thread(_update_task, tid, "DISPATCHED")
            await manager.broadcast({"type": "task.updated", "task_id": tid, "status": "DISPATCHED"})


@app.websocket("/agent/connect")
async def agent_connect(ws: WebSocket):
    if RELAY_API_KEY and ws.headers.get("x-api-key") != RELAY_API_KEY:
        await ws.close(code=1008)
        return
    await ws.accept()
    old = _agent["ws"]
    _agent.update(ws=ws, id=ws.headers.get("x-agent-id") or "pc-agent", last_seen=time.time())
    if old is not None:
        try:
            await old.close()
        except Exception:
            pass
    logger.info("Agent connected: %s", _agent["id"])
    await manager.broadcast({"type": "agent.online", "agent_id": _agent["id"]})
    try:
        await _dispatch_queued()
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            _agent["last_seen"] = time.time()
            if msg.get("type") == "task.update" and msg.get("task_id") and msg.get("status") != "QUEUED":
                await asyncio.to_thread(_update_task, msg["task_id"], msg.get("status", "RUNNING"),
                                        msg.get("result"), msg.get("error"))
                await manager.broadcast({"type": "task.updated", "task_id": msg["task_id"],
                                         "status": msg.get("status"), "result": msg.get("result"),
                                         "error": msg.get("error")})
    except WebSocketDisconnect:
        pass
    finally:
        if _agent["ws"] is ws:
            _agent.update(ws=None)
            logger.info("Agent disconnected")
            await manager.broadcast({"type": "agent.offline"})

# --------------------------------------------------------------------------- startup/shutdown
# --------------------------------------------------------------------------- chat
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []

_CHAT_SYSTEM = (
    "You are the assistant in the dashboard of an autonomous Windows PC agent. "
    "Answer questions briefly, in the same language the user writes in. "
    "If the user asks you to DO something on the PC (open an app, create or move files, browse, type, save...), "
    "do not claim to have done it. Reply with ONLY this JSON: "
    '{"action":"task","instruction":"<clear instruction in English>","reply":"<one short sentence confirming it, in the same language as the user>"}. '
    'For everything else reply with ONLY this JSON: {"action":"reply","reply":"<your answer>"}. '
)
_VERBS = ("open ", "launch ", "start ", "create ", "make ", "write ", "type ", "save ", "search ", "find ",
          "download ", "move ", "copy ", "delete ", "close ", "run ", "take ", "send ", "click ", "play ",
          "install ", "go to ")

@app.post("/chat")
def chat(req: ChatRequest, _auth=Depends(verify_api_key)):
    """Answer a dashboard chat message, or turn it into a task when it asks the agent to do something."""
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    from llm.llm import llm  # same provider settings as the agent (.env)
    recent = "; ".join(f"{t['status']}: {t['instruction'][:60]}" for t in list_tasks(status=None)["tasks"][:5]) or "none"
    system = _CHAT_SYSTEM + f"PC agent online: {pc_status()['online']}. Recent tasks: {recent}."
    hist = [{"role": m.role, "content": m.content} for m in req.history[-10:] if m.role in ("user", "assistant")]
    while hist and hist[0]["role"] != "user":
        hist.pop(0)
    resp = llm.chat([{"role": "system", "content": system}, *hist, {"role": "user", "content": text}],
                    temperature=0.3, max_tokens=500)
    raw = (resp.text or "").strip()
    working = bool(llm.api_key) and llm.provider != "mock" and not raw.startswith(("[FALLBACK", "[LLM provider error"))
    instruction, reply = None, raw
    if working:
        data = llm._extract_json(raw)
        if data:
            reply = str(data.get("reply") or "")
            if data.get("action") == "task":
                instruction = str(data.get("instruction") or "").strip() or None
    else:
        low = text.lower()
        if low.startswith("task:"):
            instruction = text[5:].strip() or None
        elif low.startswith(_VERBS):
            instruction = text
        why = raw.splitlines()[0].strip("[]") if raw.startswith("[LLM provider error") else "check LLM_API_KEY in .env"
        reply = "Creating that as a task." if instruction else (
            f"I can't answer questions right now because the language model isn't working ({why}). "
            "Tell me what to do on the PC, like 'Open Notepad', and I'll create a task.")
    out = {"reply": reply or ("On it." if instruction else "The language model returned an empty reply."),
           "task_id": None, "status": None}
    if instruction:
        t = create_task(TaskCreate(instruction=instruction))
        out.update(task_id=t["task_id"], status=t["status"])
    return out


@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_running_loop()
    logger.info("PC Agent Relay started")

@app.get("/health")
def health():
    return {"status": "ok"}
