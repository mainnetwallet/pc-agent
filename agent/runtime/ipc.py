"""Relay IPC client.

Connects the Windows agent outbound to the FastAPI server over authenticated WSS
(no port forwarding required). Receives task dispatches, reports status, and
streams heartbeats. Used only when RELAY_URL is set.
"""

import json
import threading
import time
import uuid

import websockets  # type: ignore

from config import RELAY_URL, RELAY_API_KEY, AGENT_ID, AGENT_NAME, RELAY_HEARTBEAT_INTERVAL, RELAY_RECONNECT_INTERVAL
from tasks.engine import task_engine, Task
from core.agent import agent as core_agent


class RelayClient:
    def __init__(self):
        self.ws = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._registered = False

    def run_forever(self):
        while not self._stop.is_set():
            try:
                self._connect_and_run()
            except Exception as e:
                __import__("logging").getLogger("relay").exception("Relay connection error: %s", e)
            if not self._stop.is_set():
                time.sleep(RELAY_RECONNECT_INTERVAL)

    def _connect_and_run(self):
        headers = {"X-Agent-ID": AGENT_ID, "X-API-Key": RELAY_API_KEY or ""}
        url = RELAY_URL.rstrip("/") + "/agent/connect"
        import asyncio
        asyncio.run(self._ws_loop(url, headers))

    async def _ws_loop(self, url, headers):
        async with websockets.connect(url, additional_headers=headers) as ws:
            self.ws = ws
            self._registered = True
            # heartbeat task
            hb = threading.Thread(target=self._heartbeat, daemon=True)
            hb.start()
            try:
                async for message in ws:
                    self._handle_message(message)
            finally:
                self._registered = False
                self.ws = None

    def _heartbeat(self):
        while self._registered and not self._stop.is_set():
            try:
                if self.ws:
                    import asyncio
                    asyncio.run(self.ws.send(json.dumps({"type": "heartbeat", "agent_id": AGENT_ID})))
            except Exception:
                pass
            time.sleep(RELAY_HEARTBEAT_INTERVAL)

    def _handle_message(self, message: str):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        mtype = data.get("type")
        if mtype == "task":
            self._dispatch_task(data)
        elif mtype == "stop":
            task_engine.stop(data.get("task_id", ""))
        elif mtype == "pause":
            task_engine.pause(data.get("task_id", ""))
        elif mtype == "resume":
            task_engine.resume(data.get("task_id", ""))

    def _dispatch_task(self, data: dict):
        task_id = data.get("task_id") or str(uuid.uuid4())
        instruction = data.get("instruction", "")
        task_engine.create_task(instruction, metadata={"source": "relay", "relay_task_id": task_id})
        # start execution
        task_engine.start(task_id)
        core_agent.run_task(Task(id=task_id, instruction=instruction), task_engine)

    def stop(self):
        self._stop.set()


relay_client = RelayClient()
