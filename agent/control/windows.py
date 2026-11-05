"""Window and application management.

Discovers running windows, launches applications, activates/switches/closes windows.
"""

import os
import time
from typing import List, Optional

import pygetwindow as gw  # type: ignore

from control.desktop import DesktopControl
from control.launcher import (  # noqa: F401  (discover_installed_apps is re-exported)
    discover_installed_apps, exact_desktop_match, launch_by_name, launch_path,
    list_desktop_items,
)


class WindowControl:
    def __init__(self):
        self.desktop = DesktopControl()

    def get_all_windows(self) -> List[dict]:
        out = []
        for w in gw.getAllWindows():
            try:
                out.append({
                    "title": w.title,
                    "handle": w._hWnd if hasattr(w, "_hWnd") else None,
                    "left": w.left,
                    "top": w.top,
                    "width": w.width,
                    "height": w.height,
                    "visible": w.visible,
                    "minimized": w.isMinimized,
                    "maximized": w.isMaximized,
                    "active": False,  # filled below
                })
            except Exception:
                continue
        return out

    def get_active_window(self) -> Optional[dict]:
        w = self.desktop.get_active_window()
        if w is None:
            return None
        return {
            "title": w.title,
            "left": w.left, "top": w.top,
            "width": w.width, "height": w.height,
        }

    def switch_to_window(self, title_contains: str) -> bool:
        return self.desktop.switch_to_window(title_contains)

    def activate_window(self, title_contains: str) -> bool:
        return self.switch_to_window(title_contains)

    def close_window(self, title_contains: str) -> bool:
        return self.desktop.close_window(title_contains)

    def minimize_window(self, title_contains: str) -> bool:
        for w in gw.getAllWindows():
            if title_contains.lower() in (w.title or "").lower():
                try:
                    w.minimize()
                    return True
                except Exception:
                    continue
        return False

    def maximize_window(self, title_contains: str) -> bool:
        for w in gw.getAllWindows():
            if title_contains.lower() in (w.title or "").lower():
                try:
                    w.maximize()
                    return True
                except Exception:
                    continue
        return False

    # --------------------------------------------------------------- applications
    def launch_application(self, app_name_or_path: str) -> Optional[int]:
        """Launch an application, shortcut, folder or file by name or path.

        Returns the PID if started, 0 if the shell opened it (no PID available), or None
        if nothing could be opened. Things on the Desktop are found by name, so
        "open Chrome" works with a Chrome icon on the desktop.
        """
        target = (app_name_or_path or "").strip().strip('"')
        if not target:
            return None

        # 1. An explicit path (file, folder, shortcut)
        if os.path.exists(target):
            pid = launch_path(target)
            if pid is not None:
                return pid

        # 2. An icon on the Desktop with exactly this name wins over anything else
        icon = exact_desktop_match(target)
        if icon:
            pid = launch_path(icon["path"])
            if pid is not None:
                return pid

        # 3. System executables (notepad.exe, calc.exe, ...)
        windir = os.environ.get("WINDIR", r"C:\Windows")
        for sysdir in (windir, os.path.join(windir, "System32")):
            candidate = os.path.join(sysdir, f"{target}.exe")
            if os.path.exists(candidate):
                pid = launch_path(candidate)
                if pid is not None:
                    return pid

        # 4. Shell association / App Paths ("chrome", "excel", "https://...")
        try:
            os.startfile(target)
            time.sleep(1.0)
            return 0
        except Exception:
            pass

        # 5. Fuzzy match on Desktop icons, Start Menu shortcuts, then installed apps
        return launch_by_name(target)

    def list_desktop_items(self) -> List[dict]:
        """Icons currently on the Desktop (name, path, type)."""
        return list_desktop_items()

    def close_application(self, app_name: str) -> bool:
        """Close windows belonging to an application by title match."""
        closed = False
        for w in gw.getAllWindows():
            if app_name.lower() in (w.title or "").lower():
                try:
                    w.close()
                    closed = True
                except Exception:
                    continue
        return closed


# Module-level instance
windows = WindowControl()
