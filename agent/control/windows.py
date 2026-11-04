"""Window and application management.

Discovers running windows, launches applications, activates/switches/closes windows.
"""

import os
import subprocess
from typing import List, Optional

import pygetwindow as gw  # type: ignore

from control.desktop import DesktopControl


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
        """Launch an application by name or path. Returns the PID if started."""
        # If it's an existing path, launch directly
        if app_name_or_path and os.path.exists(app_name_or_path):
            try:
                proc = subprocess.Popen([app_name_or_path], creationflags=subprocess.CREATE_NO_WINDOW)
                return proc.pid
            except Exception:
                pass

        # Direct system executable fallback (notepad.exe, calc.exe, etc.)
        for sysdir in (os.environ.get("WINDIR", r"C:\Windows"),
                       os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")):
            candidate = os.path.join(sysdir, f"{app_name_or_path}.exe")
            if os.path.exists(candidate):
                try:
                    proc = subprocess.Popen([candidate], creationflags=subprocess.CREATE_NO_WINDOW)
                    return proc.pid
                except Exception:
                    pass

        # Try shell association (os.startfile)
        try:
            os.startfile(app_name_or_path)
            time.sleep(1.0)
            # best effort; return None since we can't get PID
            return 0
        except Exception:
            pass

        # Otherwise try common locations / registry uninstall + start menu shortcuts
        discovered = discover_installed_apps()
        matches = [a for a in discovered if app_name_or_path.lower() in a["name"].lower()]
        if not matches:
            matches = [a for a in discovered if a["name"].lower().startswith(app_name_or_path.lower())]
        if matches:
            exe = matches[0].get("executable")
            if exe and os.path.exists(exe):
                try:
                    proc = subprocess.Popen([exe], creationflags=subprocess.CREATE_NO_WINDOW)
                    return proc.pid
                except Exception:
                    pass
        return None

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


def discover_installed_apps() -> List[dict]:
    """Discover installed applications from Windows uninstall registry and start menu shortcuts.

    Returns list of {name, executable, source}.
    """
    import winreg  # Windows-only

    apps: List[dict] = []
    seen = set()

    def _add(name: str, exe: Optional[str], source: str) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        apps.append({"name": name, "executable": exe, "source": source})

    # Uninstall registry (HKLM and HKCU, 32 and 64 bit)
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in roots:
        for flags in (winreg.KEY_READ, winreg.KEY_READ | 0x0100, winreg.KEY_READ | 0x0200):
            # 0x0100=KEY_WOW64_32KEY, 0x0200=KEY_WOW64_64KEY
            try:
                with winreg.OpenKey(hive, subkey, 0, flags) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub) as skey:
                                try:
                                    name = winreg.QueryValueEx(skey, "DisplayName")[0]
                                except Exception:
                                    continue
                                exe = None
                                try:
                                    exe = winreg.QueryValueEx(skey, "DisplayIcon")[0]
                                    if exe and exe.endswith(",0"):
                                        exe = exe[:-2]
                                except Exception:
                                    try:
                                        exe = winreg.QueryValueEx(skey, "InstallLocation")[0]
                                    except Exception:
                                        exe = None
                                _add(name, exe, "registry")
                        except Exception:
                            continue
            except Exception:
                continue

    # Start menu shortcuts
    start_menu_dirs = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for sm in start_menu_dirs:
        if not os.path.isdir(sm):
            continue
        for root, _, files in os.walk(sm):
            for f in files:
                if f.lower().endswith((".lnk", ".url")):
                    full = os.path.join(root, f)
                    name = os.path.splitext(f)[0]
                    _add(name, full, "start_menu")

    return apps

