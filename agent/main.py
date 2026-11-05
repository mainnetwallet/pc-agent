"""Autonomous PC Agent - main entry point.

Always: task worker + local Telegram polling + on-demand screen capture.

Standalone mode (RELAY_URL empty):
  * Runs the task engine background worker.
  * Runs Telegram long-polling locally.
  * Runs screen capture on demand.

Relay mode (RELAY_URL set, e.g. ws://localhost:8000):
  * ALSO connects outbound to the FastAPI server (ws/wss, /agent/connect).
  * Executes tasks dispatched from the server and reports their status back.
"""

import os
import sys

# Ensure the agent package directory is importable as top-level modules
# (e.g. `from config import ...`) regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import signal
import sys
import time
import threading
import os

from config import (
    AGENT_NAME, AGENT_ID, RELAY_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_MODE,
    WATCHDOG_ENABLED, AUTO_STARTUP, LOGS_DIR,
)
from tasks.engine import task_engine
from core.agent import agent as core_agent
from discovery.adapters import register_builtin_adapters
from screen.capture import capture_engine
from memory.sqlite_store import memory


def setup_logging():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(LOGS_DIR, "agent.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("pc_agent")


logger = setup_logging()


def task_queue_worker():
    """Continuously process the task queue."""
    logger.info("Task queue worker started")
    while True:
        try:
            task = task_engine.next_queued()
            if task is None:
                for tid in task_engine.due_tasks():
                    task_engine.enqueue(tid)
                time.sleep(1.0)
                continue
            # Mark running and execute via the core agent in a background thread.
            if task_engine.mark_running(task.id):
                threading.Thread(
                    target=core_agent.run_task, args=(task, task_engine),
                    daemon=True, name=f"task-{task.id[:8]}",
                ).start()
        except Exception as e:
            logger.exception("Queue worker error: %s", e)
            time.sleep(2.0)


def start_relay_thread():
    """Connect outbound to the relay server (ws/wss) in a background thread."""
    from runtime.ipc import RelayClient
    t = threading.Thread(target=RelayClient().run_forever, daemon=True, name="relay")
    t.start()
    logger.info("Relay client started -> %s", RELAY_URL)
    return t


def run_standalone_mode():
    """Run Telegram polling locally + task worker + screen capture."""
    threads = []

    # Task queue worker
    t = threading.Thread(target=task_queue_worker, daemon=True, name="task-worker")
    t.start()
    threads.append(t)

    # Telegram bot (long-polling) if token configured
    if TELEGRAM_BOT_TOKEN:
        try:
            from telegram_bot.bot import TelegramBot
            bot = TelegramBot()
            t = threading.Thread(target=bot.run_polling, daemon=True, name="telegram")
            t.start()
            threads.append(t)
            logger.info("Telegram polling started")
        except Exception as e:
            logger.exception("Telegram failed to start (agent keeps running): %s", e)
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set; Telegram disabled")

    # Screen capture stays idle until requested (capture_engine)

    logger.info(f"{AGENT_NAME} ({AGENT_ID}) running in STANDALONE mode")
    # Keep main thread alive
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def run_watchdog():
    """Watchdog: monitor core agent and restart if hung."""
    if not WATCHDOG_ENABLED:
        return
    def loop():
        while True:
            try:
                # If a task is stuck, flag recovery
                for t in task_engine.list_tasks():
                    if t["status"] == "RUNNING":
                        # rough stuck check handled by recovery engine elsewhere
                        pass
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="watchdog").start()


def main():
    # Register built-in adapters/tools at startup
    try:
        register_builtin_adapters()
        logger.info("Built-in adapters registered")
    except Exception as e:
        logger.exception("Failed to register adapters: %s", e)

    memory.audit(None, "agent.startup", {"agent": AGENT_NAME, "id": AGENT_ID,
                                         "mode": "standalone+relay" if RELAY_URL else "standalone"})

    # Tasks that were running when the agent last stopped come back PAUSED; nothing resumes
    # on its own. The user resumes them from the dashboard / Telegram.
    try:
        paused = task_engine.restore_interrupted()
        if paused:
            logger.info("Restart: %d interrupted task(s) restored as paused (resume to continue)", len(paused))
    except Exception as e:
        logger.exception("Failed to restore interrupted tasks: %s", e)

    run_watchdog()

    # Relay (dashboard/server) and local Telegram + task worker run together.
    if RELAY_URL:
        try:
            start_relay_thread()
        except Exception as e:
            logger.exception("Relay client failed to start: %s", e)
    run_standalone_mode()


if __name__ == "__main__":
    main()
