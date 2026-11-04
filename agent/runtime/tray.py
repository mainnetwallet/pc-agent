"""Windows system tray for the agent.

Provides a tray icon with Pause / Stop / Emergency Stop / Exit. Runs in the
logged-in user's session (not Session 0).
"""

import os
import sys
import threading
import logging

logger = logging.getLogger("tray")

try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_TRAY = True
except Exception:
    _HAS_TRAY = False


def _create_image():
    # Simple icon: a blue PC monitor shape
    image = Image.new("RGB", (64, 64), color=(0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([16, 16, 48, 44], fill=(0, 120, 215))
    draw.rectangle([24, 44, 40, 60], fill=(0, 120, 215))
    draw.ellipse([29, 12, 35, 18], fill=(255, 255, 255))
    return image


class Tray:
    def __init__(self, on_stop, on_pause, on_resume, on_exit):
        self.on_stop = on_stop
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_exit = on_exit
        self._icon = None
        self._thread = None

    def start(self):
        if not _HAS_TRAY:
            logger.warning("pystray not available; tray disabled")
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        from tasks.engine import task_engine
        def toggle_pause(icon, item):
            # Pause/resume all running tasks
            from tasks.engine import task_engine as te
            any_paused = False
            for t in te.list_tasks():
                if t["status"] == "RUNNING":
                    te.pause(t["id"])
                    any_paused = True
                elif t["status"] == "WAITING":
                    te.resume(t["id"])
            self._update_menu()

        def stop_all(icon, item):
            for t in task_engine.list_tasks():
                if t["status"] in ("QUEUED", "PLANNING", "RUNNING"):
                    task_engine.stop(t["id"])
            self.on_stop()

        def emergency_stop(icon, item):
            # Hard stop everything
            import pyautogui
            pyautogui.keyUp("ctrl")
            pyautogui.keyUp("alt")
            pyautogui.keyUp("shift")
            for t in task_engine.list_tasks():
                if t["status"] not in ("COMPLETED", "FAILED", "CANCELLED"):
                    task_engine.stop(t["id"])
            self.on_stop()

        def exit_app(icon, item):
            icon.stop()
            self.on_exit()

        def status_text(icon, item):
            return f"PC Agent - {task_engine.desktop_lock_holder() or 'idle'}"

        self._icon = pystray.Icon("pc_agent", _create_image(), "PC Agent")
        self._update_menu = lambda: None
        self._icon.menu = pystray.Menu(
            pystray.MenuItem(status_text, lambda *a: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause/Resume All", toggle_pause),
            pystray.MenuItem("Stop All Tasks", stop_all),
            pystray.MenuItem("Emergency Stop", emergency_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", exit_app),
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            self._icon.stop()


tray = Tray(lambda: None, lambda: None, lambda: None, lambda: None)
