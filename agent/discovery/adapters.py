"""Application adapters.

An adapter encapsulates how to operate a specific application using the best
available method (native API > accessibility > UI Automation > OCR > mouse/keyboard > CLI).
Adapters register their capabilities into the DynamicToolRegistry.
"""

import time
from typing import Any, Dict, List, Optional

from control.desktop import DesktopControl
from control.windows import windows, discover_installed_apps
from discovery.registry import registry, PERMISSION_LEVELS


class ApplicationAdapter:
    """Base class for application-specific automation adapters."""

    def __init__(self, app_name: str):
        self.app_name = app_name
        self.desktop = DesktopControl()
        self.registered_tools: List[str] = []

    # -- lifecycle -------------------------------------------------------
    def is_running(self) -> bool:
        return any(self.app_name.lower() in (w.title or "").lower()
                   for w in self.desktop.get_all_windows())

    def launch(self) -> bool:
        pid = windows.launch_application(self.app_name)
        if pid is None:
            return False
        time.sleep(1.0)
        return self.is_running()

    def focus(self) -> bool:
        return self.desktop.switch_to_window(self.app_name)

    def close(self) -> bool:
        return windows.close_application(self.app_name)

    # -- discovery -------------------------------------------------------
    def discover_capabilities(self) -> List[Dict[str, Any]]:
        """Return a list of capability dicts this adapter can offer."""
        return []

    def register(self) -> List[str]:
        """Discover capabilities and register them as tools. Returns tool names."""
        self.registered_tools = []
        for cap in self.discover_capabilities():
            name = cap.pop("tool_name")
            fn = cap.pop("callable")
            schema = cap.pop("input_schema", {"type": "object", "properties": {}})
            permission = cap.pop("permission", "owner")
            category = cap.pop("category", self.app_name.lower())
            description = cap.pop("description", "")
            dry_run = cap.pop("dry_run", None)
            registry.register(name, fn, description=description,
                              input_schema=schema, permission=permission,
                              category=category, dry_run=dry_run)
            self.registered_tools.append(name)
        return list(self.registered_tools)


class NotepadAdapter(ApplicationAdapter):
    """Adapter for Notepad using pure desktop control (universal, works on any Notepad)."""

    def __init__(self):
        super().__init__("Notepad")

    def discover_capabilities(self) -> List[Dict[str, Any]]:
        caps = []

        def type_text(text: str) -> Dict[str, Any]:
            self.focus()
            self.desktop.type_text(text)
            return {"ok": True, "chars": len(text)}

        def save_file(path: str) -> Dict[str, Any]:
            self.focus()
            self.desktop.hotkey("ctrl", "s")
            time.sleep(0.3)
            self.desktop.type_text(path, interval=0.01)
            time.sleep(0.2)
            self.desktop.press("enter")
            time.sleep(0.3)
            return {"ok": True, "path": path}

        caps.append({
            "tool_name": "notepad.type_text",
            "callable": type_text,
            "description": "Type text into the focused Notepad window",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "permission": "owner",
            "category": "notepad",
        })
        caps.append({
            "tool_name": "notepad.save_file",
            "callable": save_file,
            "description": "Save Notepad content to a file (Ctrl+S, type path, Enter)",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "permission": "owner",
            "category": "notepad",
        })
        return caps


class TerminalAdapter(ApplicationAdapter):
    """Adapter for executing terminal commands through the terminal control module."""

    def __init__(self):
        super().__init__("Terminal")
        from control.terminal import terminal
        self.terminal = terminal

    def discover_capabilities(self) -> List[Dict[str, Any]]:
        def run_command(command: str, shell: str = "powershell") -> Dict[str, Any]:
            result = self.terminal.run(command, shell=shell)
            return {
                "ok": result.exit_code == 0,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.duration_seconds,
            }

        return [{
            "tool_name": "terminal.run_command",
            "callable": run_command,
            "description": "Run a command in PowerShell/CMD/Python/Node/etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "shell": {"type": "string", "enum": ["powershell", "cmd", "python", "node", "git"]},
                },
                "required": ["command"],
            },
            "permission": "owner",
            "category": "terminal",
        }]


class FileSystemAdapter(ApplicationAdapter):
    """Adapter exposing safe filesystem operations as tools."""

    def __init__(self):
        super().__init__("FileSystem")
        from control.filesystem import fs
        self.fs = fs

    def discover_capabilities(self) -> List[Dict[str, Any]]:
        def read_file(path: str) -> Dict[str, Any]:
            try:
                return {"ok": True, "content": self.fs.read_file(path)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def write_file(path: str, content: str) -> Dict[str, Any]:
            try:
                self.fs.write_file(path, content)
                return {"ok": True, "path": path}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def list_folder(path: str) -> Dict[str, Any]:
            try:
                return {"ok": True, "entries": self.fs.list_folder(path)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def search_files(query: str) -> Dict[str, Any]:
            try:
                return {"ok": True, "results": self.fs.search_files(query)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def delete_file(path: str) -> Dict[str, Any]:
            try:
                self.fs.delete_file(path)
                return {"ok": True, "path": path}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def copy(src: str, dst: str) -> Dict[str, Any]:
            try:
                self.fs.copy(src, dst)
                return {"ok": True, "src": src, "dst": dst}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def move(src: str, dst: str) -> Dict[str, Any]:
            try:
                self.fs.move(src, dst)
                return {"ok": True, "src": src, "dst": dst}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def create_folder(path: str) -> Dict[str, Any]:
            try:
                self.fs.create_folder(path)
                return {"ok": True, "path": path}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        schemas = {
            "read_file": ({"path": {"type": "string"}}, ["path"]),
            "write_file": ({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            "list_folder": ({"path": {"type": "string"}}, ["path"]),
            "search_files": ({"query": {"type": "string"}}, ["query"]),
            "delete_file": ({"path": {"type": "string"}}, ["path"]),
            "copy": ({"src": {"type": "string"}, "dst": {"type": "string"}}, ["src", "dst"]),
            "move": ({"src": {"type": "string"}, "dst": {"type": "string"}}, ["src", "dst"]),
            "create_folder": ({"path": {"type": "string"}}, ["path"]),
        }

        caps = []
        for fname, (props, required) in schemas.items():
            caps.append({
                "tool_name": f"filesystem.{fname}",
                "callable": locals()[fname],
                "description": f"Filesystem: {fname}",
                "input_schema": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
                "permission": "owner",
                "category": "filesystem",
            })
        return caps


class DesktopAdapter(ApplicationAdapter):
    """Adapter exposing desktop control (mouse/keyboard/screenshot/OCR) as tools."""

    def __init__(self):
        super().__init__("Desktop")

    # ------------------------------------------------------------------ tools
    def _launch_application(self, app_name_or_path: str) -> Dict[str, Any]:
        pid = windows.launch_application(app_name_or_path)
        if pid is None:
            return {"ok": False, "error": f"Could not launch {app_name_or_path}"}
        time.sleep(1.5)
        return {"ok": True, "pid": pid, "launched": app_name_or_path}

    def _click(self, x: int, y: int, button: str = "left") -> Dict[str, Any]:
        self.desktop.click(x, y, button=button)
        return {"ok": True, "x": x, "y": y, "button": button}

    def _type_text(self, text: str) -> Dict[str, Any]:
        self.desktop.type_text(text)
        return {"ok": True, "chars": len(text)}

    def _hotkey(self, keys: List[str]) -> Dict[str, Any]:
        self.desktop.key_combo(keys)
        return {"ok": True, "keys": list(keys)}

    def _press(self, key: str) -> Dict[str, Any]:
        self.desktop.press(key)
        return {"ok": True, "key": key}

    def _ocr_screen(self, monitor_index: int = 0) -> Dict[str, Any]:
        text = self.desktop.ocr_screen(monitor_index=monitor_index)
        return {"ok": True, "text": text}

    def _get_monitors(self) -> Dict[str, Any]:
        return {"ok": True, "monitors": [m.to_dict() for m in self.desktop.get_monitors()]}

    def _screenshot(self, monitor_index: int = 0) -> Dict[str, Any]:
        path, _ = self.desktop.screenshot(monitor_index=monitor_index)
        return {"ok": path is not None, "path": path}

    def discover_capabilities(self) -> List[Dict[str, Any]]:
        caps = [
            {
                "tool_name": "desktop.launch_application",
                "callable": self._launch_application,
                "description": "Launch an application by name or path",
                "input_schema": {"type": "object", "properties": {"app_name_or_path": {"type": "string"}}, "required": ["app_name_or_path"]},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.screenshot",
                "callable": self._screenshot,
                "description": "Capture a screenshot of a monitor",
                "input_schema": {"type": "object", "properties": {"monitor_index": {"type": "integer"}}, "required": []},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.click",
                "callable": self._click,
                "description": "Click at screen coordinates",
                "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string"}}, "required": ["x", "y"]},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.type_text",
                "callable": self._type_text,
                "description": "Type text at the current cursor/focus",
                "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.hotkey",
                "callable": self._hotkey,
                "description": "Press a key combination (keys as a list)",
                "input_schema": {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}}}, "required": ["keys"]},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.press",
                "callable": self._press,
                "description": "Press a single key",
                "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.ocr_screen",
                "callable": self._ocr_screen,
                "description": "Run OCR on the current screen",
                "input_schema": {"type": "object", "properties": {"monitor_index": {"type": "integer"}}, "required": []},
                "permission": "owner",
                "category": "desktop",
            },
            {
                "tool_name": "desktop.get_monitors",
                "callable": self._get_monitors,
                "description": "List available monitors",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "permission": "public",
                "category": "desktop",
            },
        ]
        return caps


# Built-in adapters to auto-register
BUILTIN_ADAPTERS = [
    DesktopAdapter,
    FileSystemAdapter,
    TerminalAdapter,
    NotepadAdapter,
]


def register_builtin_adapters() -> List[str]:
    """Register all built-in adapters and return their tool names."""
    names: List[str] = []
    for adapter_cls in BUILTIN_ADAPTERS:
        try:
            adapter = adapter_cls()
            names.extend(adapter.register())
        except Exception as e:
            # Do not crash startup if one adapter fails
            import traceback
            traceback.print_exc()
    return names
