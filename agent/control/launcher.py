"""Find and launch things the user can see: Desktop icons, Start Menu apps, installed apps.

Why this exists
---------------
"Open X" used to look only at the Uninstall registry and the Start Menu, so anything that
lives *on the Desktop* (shortcuts, portable .exe files, folders, documents, items on the
Public/OneDrive desktop) was never found. On top of that, .lnk/.url shortcuts and folders
were started with ``subprocess.Popen`` which cannot run them, so even discovered shortcuts
failed to open, and only the first match was ever tried.

This module is import-safe on any OS (Windows-only pieces are imported lazily) so it can be
unit-tested anywhere.
"""

import os
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Optional

# Lower number = preferred. What the user sees on screen wins over registry guesses.
SOURCE_PRIORITY = {"desktop": 0, "start_menu": 1, "registry": 2}

_IGNORED_DESKTOP_FILES = {"desktop.ini", "thumbs.db"}
_SHORTCUT_EXT = (".lnk", ".url")


# ----------------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    """Lowercase and drop everything but letters/digits: 'Google Chrome!' -> 'googlechrome'."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


_NUMERIC_RE = re.compile(r"^[+-]?\d+$")


def _raw(text: str) -> str:
    """Case-insensitive, whitespace-trimmed name that KEEPS punctuation ('-1' stays '-1')."""
    return (text or "").strip().lower()


def _raw_names(entry: Dict) -> List[str]:
    stem = os.path.splitext(entry.get("filename", "") or "")[0]
    return [n for n in (_raw(entry.get("name", "")), _raw(stem)) if n]


def _shell_folder(csidl: int) -> Optional[str]:
    """Real path of a Windows special folder (follows OneDrive/redirected Desktop)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
            return buf.value or None
    except Exception:
        pass
    return None


def desktop_dirs() -> List[str]:
    """Every folder whose contents show up as icons on the user's Desktop."""
    home = os.path.expanduser("~")
    candidates: List[Optional[str]] = [
        _shell_folder(0x10),   # CSIDL_DESKTOPDIRECTORY: this user's real Desktop
        _shell_folder(0x19),   # CSIDL_COMMON_DESKTOPDIRECTORY: Public Desktop (all users)
        os.path.join(home, "Desktop"),
    ]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root:
            candidates.append(os.path.join(root, "Desktop"))
    candidates.append(os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"))

    out: List[str] = []
    seen = set()
    for c in candidates:
        if not c or not os.path.isdir(c):
            continue
        key = os.path.normcase(os.path.realpath(c))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# --------------------------------------------------------------------- discovery
def list_desktop_items(dirs: Optional[Iterable[str]] = None) -> List[Dict]:
    """Everything visible on the Desktop: shortcuts, programs, folders, documents."""
    items: List[Dict] = []
    seen = set()
    for d in (dirs if dirs is not None else desktop_dirs()):
        try:
            names = sorted(os.listdir(d), key=str.lower)
        except OSError:
            continue
        for filename in names:
            if filename.lower() in _IGNORED_DESKTOP_FILES:
                continue
            full = os.path.join(d, filename)
            if full in seen:
                continue
            seen.add(full)
            is_dir = os.path.isdir(full)
            stem = filename if is_dir else os.path.splitext(filename)[0]
            ext = "" if is_dir else os.path.splitext(filename)[1].lower()
            kind = "folder" if is_dir else ("shortcut" if ext in _SHORTCUT_EXT else (ext.lstrip(".") or "file"))
            items.append({"name": stem, "filename": filename, "path": full,
                          "type": kind, "source": "desktop"})
    return items


def list_start_menu_items() -> List[Dict]:
    items: List[Dict] = []
    for sm in (os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
               os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")):
        if not os.path.isdir(sm):
            continue
        for root, _, files in os.walk(sm):
            for f in files:
                if f.lower().endswith(_SHORTCUT_EXT):
                    items.append({"name": os.path.splitext(f)[0], "filename": f,
                                  "path": os.path.join(root, f),
                                  "type": "shortcut", "source": "start_menu"})
    return items


def _clean_icon_path(value: Optional[str]) -> Optional[str]:
    """Registry DisplayIcon looks like '"C:\\x\\app.exe",0' or 'C:\\x\\app.exe,-101'."""
    if not value:
        return None
    value = value.strip()
    value = re.sub(r",\s*-?\d+$", "", value).strip().strip('"')
    value = os.path.expandvars(value)
    low = os.path.basename(value).lower()
    if not low.endswith(".exe") or "unins" in low or "uninstall" in low:
        return None
    return value if os.path.isfile(value) else None


def list_registry_apps() -> List[Dict]:
    if sys.platform != "win32":
        return []
    import winreg  # Windows-only

    apps: List[Dict] = []
    seen = set()
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in roots:
        # 0x0100 = KEY_WOW64_32KEY, 0x0200 = KEY_WOW64_64KEY
        for flags in (winreg.KEY_READ, winreg.KEY_READ | 0x0100, winreg.KEY_READ | 0x0200):
            try:
                with winreg.OpenKey(hive, subkey, 0, flags) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as skey:
                                name = winreg.QueryValueEx(skey, "DisplayName")[0]
                                try:
                                    icon = winreg.QueryValueEx(skey, "DisplayIcon")[0]
                                except Exception:
                                    icon = None
                        except Exception:
                            continue
                        exe = _clean_icon_path(icon)
                        if not name or not exe or (name, exe) in seen:
                            continue
                        seen.add((name, exe))
                        apps.append({"name": name, "filename": os.path.basename(exe),
                                     "path": exe, "type": "exe", "source": "registry"})
            except Exception:
                continue
    return apps


def discover_installed_apps() -> List[dict]:
    """Desktop icons, Start Menu shortcuts and installed apps (best first).

    Returns a list of {name, executable, source, type}. ``executable`` is the path that
    :func:`launch_path` can open (a .lnk, .url, .exe, folder or document).
    """
    entries = list_desktop_items() + list_start_menu_items() + list_registry_apps()
    return [{"name": e["name"], "executable": e["path"], "source": e["source"],
             "type": e.get("type", "")} for e in entries]


# ----------------------------------------------------------------------- matching
def rank_matches(query: str, entries: List[Dict]) -> List[Dict]:
    """Entries matching ``query``, best first.

    Order: exact name > prefix > substring > entry name contained in the query
    ("open google chrome please"); ties go to Desktop, then Start Menu, then registry.
    """
    q = _norm(query)
    if not q:
        return []
    raw_q = _raw(query)
    if _NUMERIC_RE.match(raw_q):
        # Numbered icons ("1", "-1", "24", "-24" ...) must match EXACTLY: normalising would
        # turn "-1" into "1" and prefix matching would turn "1" into "10", opening the wrong one.
        exact = [e for e in entries if raw_q in _raw_names(e)]
        exact.sort(key=lambda e: SOURCE_PRIORITY.get(e.get("source", ""), 9))
        return exact
    scored = []
    for e in entries:
        names = [n for n in (_norm(e.get("name", "")), _norm(e.get("filename", ""))) if n]
        if not names:
            continue
        if raw_q in _raw_names(e):
            score = -1
        elif q in names:
            score = 0
        elif any(n.startswith(q) for n in names):
            score = 1
        elif any(q in n for n in names):
            score = 2
        elif any(len(n) >= 4 and n in q for n in names):
            score = 3
        else:
            continue
        scored.append((score, SOURCE_PRIORITY.get(e.get("source", ""), 9),
                       len(e.get("name", "")), e))
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored]


# ------------------------------------------------------------------------ launching
def launch_path(path: str) -> Optional[int]:
    """Open one concrete path the way double-clicking it would.

    Returns the PID for a directly started .exe, 0 when the shell opened it (shortcut,
    folder, document: no PID available), or None when it could not be opened.
    """
    if not path:
        return None
    path = os.path.expandvars(path.strip().strip('"'))
    if not os.path.exists(path):
        return None
    if os.path.isfile(path) and path.lower().endswith(".exe"):
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return subprocess.Popen([path], cwd=os.path.dirname(path) or None,
                                    creationflags=flags).pid
        except OSError:
            pass  # e.g. needs elevation: let the shell handle it below
    try:
        os.startfile(path)  # type: ignore[attr-defined]  # .lnk/.url/folder/document/exe
        return 0
    except (OSError, AttributeError):
        return None


def launch_by_name(query: str, max_tries: int = 5) -> Optional[int]:
    """Open an app/shortcut/folder by (partial) name, trying candidates until one opens."""
    if not query or not query.strip():
        return None
    # Cheap, file-based sources first; the (slower) registry scan only as a fallback.
    sources = (lambda: list_desktop_items() + list_start_menu_items(), list_registry_apps)
    tried = 0
    for load in sources:
        for entry in rank_matches(query, load()):
            pid = launch_path(entry["path"])
            if pid is not None:
                return pid
            tried += 1
            if tried >= max_tries:
                return None
    return None


def exact_desktop_match(query: str) -> Optional[Dict]:
    """A Desktop icon whose name is exactly what the user asked for, if any."""
    q = _norm(query)
    if not q:
        return None
    raw_q = _raw(query)
    items = list_desktop_items()
    for e in items:  # punctuation-exact first ("-1" is not "1")
        if raw_q in _raw_names(e):
            return e
    if _NUMERIC_RE.match(raw_q):
        return None
    for e in items:
        if q in (_norm(e["name"]), _norm(e["filename"])):
            return e
    return None
