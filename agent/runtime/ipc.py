"""Relay IPC client.

Connects the agent outbound to the FastAPI server (``/agent/connect``) over
ws:// or wss:// (no port forwarding required). It receives task dispatches
from the server, queues them in the local task engine, and reports task status
back. Used only when RELAY_URL is set. ``http(s)://`` URLs are accepted and
converted to ``ws(s)://``.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Dict, Optional

import websockets  # type: ignore

from config import (
    RELAY_URL, RELAY_API_KEY, AGENT_ID,
    RELAY_HEARTBEAT_INTERVAL, RELAY_RECONNECT_INTERVAL,
)
from tasks.engine import task_engine

logger = logging.getLogger("relay")

FINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}


def relay_ws_url(base: str) -> str:
    base = base.strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    elif not base.startswith(("ws://", "wss://")):
        base = "ws://" + base
    return base + "/agent/connect"


class RelayClient:
    def __init__(self):
        self._stop = threading.Event()
        self._tracked: Dict[str, Optional[str]] = {}  # task_id -> last status sent

    # ------------------------------------------------------------ lifecycle
    def run_forever(self):
        while not self._stop.is_set():
            try:
                asyncio.run(self._session())
            except Exception as e:
                logger.warning("Relay disconnected (%s: %s); retrying in %ss",
                               type(e).__name__, e, RELAY_RECONNECT_INTERVAL)
            if not self._stop.is_set():
                time.sleep(RELAY_RECONNECT_INTERVAL)

    def stop(self):
        self._stop.set()

    async def _session(self):
        url = relay_ws_url(RELAY_URL)
        headers = {"X-Agent-ID": AGENT_ID, "X-API-Key": RELAY_API_KEY or ""}
        async with websockets.connect(url, additional_headers=headers, open_timeout=10) as ws:
            logger.info("Connected to relay %s", url)
            for tid in self._tracked:           # re-report everything after a reconnect
                self._tracked[tid] = None
            tasks = [asyncio.ensure_future(c) for c in
                     (self._reader(ws), self._heartbeat(ws), self._reporter(ws))]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            for d in done:
                d.result()  # re-raise errors so run_forever reconnects

    # ------------------------------------------------------------ coroutines
    async def _reader(self, ws):
        async for message in ws:
            self._handle_message(message)

    async def _heartbeat(self, ws):
        while True:
            await ws.send(json.dumps({"type": "heartbeat", "agent_id": AGENT_ID}))
            await asyncio.sleep(RELAY_HEARTBEAT_INTERVAL)

    async def _reporter(self, ws):
        """Push task status changes back to the server."""
        while True:
            for tid, last in list(self._tracked.items()):
                d = task_engine.get_task(tid)
                if d is None:
                    self._tracked.pop(tid, None)
                    continue
                if d["status"] != last:
                    await ws.send(json.dumps({
                        "type": "task.update", "task_id": tid, "status": d["status"],
                        "result": d.get("result"), "error": d.get("error"),
                    }, default=str))
                    self._tracked[tid] = d["status"]
                    if d["status"] in FINAL_STATES:
                        self._tracked.pop(tid, None)
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------ handlers
    def _handle_message(self, message):
        try:
            data = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        mtype = data.get("type")
        tid = data.get("task_id", "")
        if mtype == "task":
            self._accept_task(data)
        elif mtype == "stop":
            task_engine.stop(tid)
        elif mtype == "pause":
            task_engine.pause(tid)
        elif mtype == "resume":
            task_engine.resume(tid)
        elif mtype == "message":
            task_engine.inject_message(tid, data.get("message", ""))

    def _accept_task(self, data: dict):
        tid = data.get("task_id")
        instruction = (data.get("instruction") or "").strip()
        if not tid or not instruction or task_engine.get_task(tid):
            return
        # The queue worker in main.py picks it up and runs it.
        task_engine.create_task(instruction, task_id=tid, metadata={"source": "relay"})
        self._tracked[tid] = None
        logger.info("Accepted task %s from relay", tid[:8])
