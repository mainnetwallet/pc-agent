"""Telegram Bot interface for the Autonomous PC Agent.

Commands:
  /start /status /tasks /screen /live N /stoplive /record N /stoprecord
  /stop /pause /resume /schedule /help

Only numeric user IDs in OWNER_TELEGRAM_IDS may control the PC.
Supports long-polling (standalone) and webhook modes (one at a time).
"""

import os
import threading
import time
import asyncio
from typing import Optional

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import (
    TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_IDS, TELEGRAM_MODE,
    TELEGRAM_WEBHOOK_URL, TELEGRAM_WEBHOOK_PORT,
)
from tasks.engine import task_engine
from llm.llm import llm, SETUP_HINT
from core.agent import agent as core_agent
from screen.capture import capture_engine
from control.desktop import DesktopControl


def _is_authorized(user_id: int) -> bool:
    if not OWNER_TELEGRAM_IDS:
        # If no allowlist configured, deny everyone (secure by default)
        return False
    return user_id in OWNER_TELEGRAM_IDS


class TelegramBot:
    def __init__(self):
        self.application: Optional[Application] = None
        self._desktop = DesktopControl()
        self._live_jobs: dict = {}  # chat_id -> job info
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._application_started = False

    # ---------------------------------------------------------------- command handlers
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            await update.message.reply_text("Unauthorized. Contact the owner to be added to the allowlist.")
            return
        await update.message.reply_text(
            f"PC Agent ({user.first_name}) is online.\n"
            "Send me a task in plain English, Bengali, or Banglish.\n"
            "Use /help for commands."
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        text = (
            "Commands:\n"
            "/status - PC and agent status\n"
            "/tasks - list tasks\n"
            "/screen - current desktop screenshot\n"
            "/live N - periodic screenshots every N seconds\n"
            "/stoplive - stop live updates\n"
            "/record N - record screen for N seconds and send video\n"
            "/stoprecord - stop recording\n"
            "/stop <task> - cancel a task\n"
            "/pause <task> - pause a task\n"
            "/resume <task> - resume a task\n"
            "/schedule <time> <instruction> - schedule a task\n"
            "/help - this help\n"
            "\nSend any other text as a task instruction.\n"
            "Attach images/documents/voice for context."
        )
        await update.message.reply_text(text)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        tasks = task_engine.list_tasks()
        running = [t for t in tasks if t["status"] == "RUNNING"]
        queued = [t for t in tasks if t["status"] == "QUEUED"]
        lines = [
            f"Agent: online",
            f"Tasks: {len(tasks)} total, {len(running)} running, {len(queued)} queued",
            f"Desktop lock holder: {task_engine.desktop_lock_holder() or 'none'}",
            f"Streaming: {'on' if capture_engine.is_recording() else 'idle'}",
        ]
        if running:
            lines.append("\nRunning:")
            for t in running[:5]:
                lines.append(f"  - {t['id'][:8]}: {t['instruction'][:60]}")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        tasks = task_engine.list_tasks()
        if not tasks:
            await update.message.reply_text("No tasks.")
            return
        lines = ["Recent tasks:"]
        for t in tasks[:15]:
            lines.append(f"[{t['status']}] {t['id'][:8]}: {t['instruction'][:70]}")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        await update.message.chat.send_action(action="upload_photo")
        path, _ = self._desktop.screenshot()
        if path:
            await update.message.reply_photo(photo=InputFile(path), caption="Current desktop")
        else:
            await update.message.reply_text("Failed to capture screenshot.")

    async def _cmd_live(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        chat_id = update.effective_chat.id
        interval = 5
        if context.args:
            try:
                interval = max(2, int(context.args[0]))
            except ValueError:
                pass
        # stop any existing live job for this chat
        self._stop_live(chat_id)
        job = {"chat_id": chat_id, "interval": interval, "running": True}
        self._live_jobs[chat_id] = job
        await update.message.reply_text(f"Live updates started: screenshot every {interval}s. Use /stoplive to stop.")
        threading.Thread(target=self._live_loop, args=(job,), daemon=True).start()

    def _stop_live(self, chat_id: int) -> None:
        job = self._live_jobs.pop(chat_id, None)
        if job:
            job["running"] = False

    async def _cmd_stoplive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        self._stop_live(update.effective_chat.id)
        await update.message.reply_text("Live updates stopped.")

    async def _cmd_record(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        duration = 30
        if context.args:
            try:
                duration = max(5, min(int(context.args[0]), 300))
            except ValueError:
                pass
        await update.message.reply_text(f"Recording screen for {duration}s...")
        path = capture_engine.start_recording(duration_seconds=duration)
        if path and os.path.exists(path):
            await update.message.reply_video(video=InputFile(path), caption=f"Screen recording ({duration}s)")
        else:
            await update.message.reply_text("Recording failed.")

    async def _cmd_stoprecord(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        capture_engine.stop_recording()
        await update.message.reply_text("Recording stop requested.")

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        if not context.args:
            await update.message.reply_text("Usage: /stop <task_id_prefix>")
            return
        prefix = context.args[0]
        stopped = False
        for t in task_engine.list_tasks():
            if t["id"].startswith(prefix) and t["status"] in ("QUEUED", "PLANNING", "RUNNING"):
                task_engine.stop(t["id"])
                stopped = True
        await update.message.reply_text(f"Stopped tasks matching '{prefix}': {stopped}")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        if not context.args:
            await update.message.reply_text("Usage: /pause <task_id_prefix>")
            return
        prefix = context.args[0]
        count = 0
        for t in task_engine.list_tasks():
            if t["id"].startswith(prefix) and t["status"] == "RUNNING":
                task_engine.pause(t["id"])
                count += 1
        await update.message.reply_text(f"Paused {count} task(s).")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        if not context.args:
            await update.message.reply_text("Usage: /resume <task_id_prefix>")
            return
        prefix = context.args[0]
        count = 0
        for t in task_engine.list_tasks():
            if t["id"].startswith(prefix) and t["status"] == "WAITING":
                task_engine.resume(t["id"])
                count += 1
        await update.message.reply_text(f"Resumed {count} task(s).")

    async def _cmd_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            return
        if not llm.available:
            await update.message.reply_text(SETUP_HINT)
            return
        # /schedule <seconds_from_now> <instruction>
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /schedule <seconds> <instruction>")
            return
        try:
            delay = float(context.args[0])
        except ValueError:
            await update.message.reply_text("Seconds must be a number.")
            return
        instruction = " ".join(context.args[1:])
        scheduled_at = time.time() + delay
        task_id = task_engine.create_task(instruction, scheduled_at=scheduled_at,
                                          metadata={"source": "telegram", "user": user.id})
        await update.message.reply_text(f"Task scheduled in {delay}s: {task_id[:8]}")

    # ---------------------------------------------------------------- message handler
    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not _is_authorized(user.id):
            await update.message.reply_text("Unauthorized.")
            return
        text = update.message.text or ""
        if not text.strip():
            await update.message.reply_text("Please send a text instruction, or attach an image/document.")
            return
        if not llm.available:
            await update.message.reply_text(SETUP_HINT)
            return
        # Treat as a task instruction
        task_id = task_engine.create_task(text, metadata={"source": "telegram", "user": user.id})
        await update.message.reply_text(f"Task received: {task_id[:8]}\nI'll work on it and report back.")
        # Start execution in background
        threading.Thread(target=self._run_task_async, args=(task_id, text), daemon=True).start()

    def _run_task_async(self, task_id: str, text: str) -> None:
        task = None
        for t in task_engine.list_tasks():
            if t["id"] == task_id:
                task = t
                break
        # Re-fetch the Task object is not stored; run_task needs a Task dataclass.
        # Simplest: mark queued -> the queue worker picks it up.
        # Here we just ensure it's enqueued (create_task already enqueues).
        return

    # ---------------------------------------------------------------- live loop
    def _live_loop(self, job: dict) -> None:
        chat_id = job["chat_id"]
        interval = job["interval"]
        # We need the bot application to send messages; use a fresh async runner
        while job.get("running") and job["chat_id"] in self._live_jobs:
            try:
                path, _ = self._desktop.screenshot()
                if path:
                    self._send_photo_sync(chat_id, path, "Live PC")
            except Exception:
                pass
            time.sleep(interval)

    def _send_photo_sync(self, chat_id: int, path: str, caption: str) -> None:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        try:
            asyncio.run(bot.send_photo(chat_id=chat_id, photo=InputFile(path), caption=caption))
        except Exception:
            pass

    # ---------------------------------------------------------------- run modes
    def run_polling(self) -> None:
        """Standalone long-polling mode."""
        if not TELEGRAM_BOT_TOKEN:
            return
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("tasks", self._cmd_tasks))
        app.add_handler(CommandHandler("screen", self._cmd_screen))
        app.add_handler(CommandHandler("live", self._cmd_live))
        app.add_handler(CommandHandler("stoplive", self._cmd_stoplive))
        app.add_handler(CommandHandler("record", self._cmd_record))
        app.add_handler(CommandHandler("stoprecord", self._cmd_stoprecord))
        app.add_handler(CommandHandler("stop", self._cmd_stop))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))
        app.add_handler(CommandHandler("schedule", self._cmd_schedule))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self.application = app
        logger = __import__("logging").getLogger("telegram")
        logger.info("Telegram long-polling started")
        # Running in a worker thread: needs its own event loop and no signal handlers.
        asyncio.set_event_loop(asyncio.new_event_loop())
        app.run_polling(drop_pending_updates=True, stop_signals=None)

    def run_webhook(self) -> None:
        """Webhook mode (server-based)."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_WEBHOOK_URL:
            return
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("tasks", self._cmd_tasks))
        app.add_handler(CommandHandler("screen", self._cmd_screen))
        app.add_handler(CommandHandler("live", self._cmd_live))
        app.add_handler(CommandHandler("stoplive", self._cmd_stoplive))
        app.add_handler(CommandHandler("record", self._cmd_record))
        app.add_handler(CommandHandler("stoprecord", self._cmd_stoprecord))
        app.add_handler(CommandHandler("stop", self._cmd_stop))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))
        app.add_handler(CommandHandler("schedule", self._cmd_schedule))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self.application = app
        asyncio.set_event_loop(asyncio.new_event_loop())
        app.run_webhook(listen="0.0.0.0", port=TELEGRAM_WEBHOOK_PORT,
                        url_path=TELEGRAM_BOT_TOKEN, webhook_url=TELEGRAM_WEBHOOK_URL,
                        stop_signals=None)


# Singleton
telegram_bot = TelegramBot()
