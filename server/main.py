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
    return {"ok": True}

@app.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.stop", "task_id": task_id})
    return {"ok": True}

@app.post("/tasks/{task_id}/pause")
def pause_task(task_id: str, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.pause", "task_id": task_id})
    return {"ok": True}

@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, _auth=Depends(verify_api_key)):
    _broadcast({"type": "task.resume", "task_id": task_id})
    return {"ok": True}

# --------------------------------------------------------------------------- PC status / screenshot / stream
@app.get("/pc/status")
def pc_status(_auth=Depends(verify_api_key)):
    return {
        "online": True,
        "agent_id": os.getenv("AGENT_ID", "pc-agent"),
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


def _broadcast(message: dict, to_device: Optional[str] = None):
    try:
        asyncio.get_event_loop().create_task(manager.broadcast(message, to_device))
    except Exception:
        pass

# --------------------------------------------------------------------------- startup/shutdown
@app.on_event("startup")
def _startup():
    logger.info("PC Agent Relay started")

@app.get("/health")
def health():
    return {"status": "ok"}
