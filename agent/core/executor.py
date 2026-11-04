"""Executor: carries out the LLM's tool calls through the registry.

  * execute_action(...)  - ONE tool call for the reasoning loop (core.loop). It never
                           guesses missing arguments and always returns a normalized
                           {"ok", "tool", "result"/"error"} dict so failures reach the LLM,
                           which decides what to do next.

GUI operations hold the exclusive desktop input lock.
"""

import time
import threading
from typing import Any, Dict, Optional

from control.desktop import DesktopControl
from control.windows import windows
from control.filesystem import fs
from control.system import system
from control.terminal import terminal
from screen.capture import capture_engine
from discovery.registry import registry
from tasks.engine import task_engine
from memory.sqlite_store import memory


# LLM-invented argument names -> candidate real names (first one the tool expects wins).
_ARG_ALIASES = {
    "command": ["app_name_or_path"],
    "application_name": ["app_name_or_path"],
    "app": ["app_name_or_path"],
    "program": ["app_name_or_path"],
    "message": ["text"],
    "content_text": ["text"],
    "input_text": ["text"],
    "key_combo": ["keys"],
    "file_path": ["path"],
    "target_path": ["path"],
    "source_path": ["src", "path"],
    "source": ["src"],
    "from": ["src"],
    "dest_path": ["dst", "path"],
    "destination": ["dst"],
    "dest": ["dst"],
    "to": ["dst"],
}

# Only these arguments are file paths that may be "found" when slightly wrong.
# Free text (type_text, file content, shell commands) must never be rewritten.
_PATH_ARGS = {"path", "src", "dst", "app_name_or_path"}


class Executor:
    def __init__(self):
        self.desktop = DesktopControl()

    # ------------------------------------------------------- single action (reasoning loop)
    def execute_action(self, tool: str, arguments: Dict[str, Any], task, engine) -> Dict[str, Any]:
        """Run one tool call. Returns {"ok": bool, "tool": str, "result"|"error": ...}."""
        mapped = self._map_tool(tool)
        if not mapped or not registry.has_tool(mapped):
            return {"ok": False, "tool": tool,
                    "error": f"Unknown tool '{tool}'. Use one of the listed tools."}
        try:
            args = self._resolve_args(arguments or {}, {"tool": mapped}, task, engine)
            if mapped.startswith("desktop."):
                if not engine.acquire_desktop_lock(task.id, timeout=10.0):
                    return {"ok": False, "tool": mapped,
                            "error": "Could not acquire desktop input lock"}
                try:
                    raw = registry.execute(mapped, args, caller_level="owner")
                finally:
                    engine.release_desktop_lock(task.id)
            else:
                raw = registry.execute(mapped, args, caller_level="owner")
        except Exception as e:
            return {"ok": False, "tool": mapped, "error": f"{type(e).__name__}: {e}"}
        return self._normalize(mapped, raw)

    @staticmethod
    def _normalize(tool: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """registry.execute reports failures as {"ok": False}; adapters may also return
        {"ok": False} inside a successful call. Treat both as failures."""
        if not raw.get("ok"):
            return {"ok": False, "tool": tool, "error": raw.get("error") or "tool failed"}
        out = raw.get("result")
        if isinstance(out, dict) and out.get("ok") is False:
            return {"ok": False, "tool": tool, "result": out,
                    "error": str(out.get("error") or "tool reported failure")}
        return {"ok": True, "tool": tool, "result": out}

    @staticmethod
    def _map_tool(tool: Optional[str]) -> Optional[str]:
        """Map LLM-invented tool names to registered tools."""
        if not tool:
            return tool
        if registry.has_tool(tool):
            return tool
        # Common LLM inventions -> registered tools
        mapping = {
            "system_launcher": "desktop.launch_application",
            "gui_automation": "desktop.click",
            "keyboard_input": "desktop.type_text",
            "hotkey_press": "desktop.hotkey",
            "press_key": "desktop.press",
            "screen_capture": "desktop.screenshot",
            "file_write": "filesystem.write_file",
            "file_read": "filesystem.read_file",
            "file_delete": "filesystem.delete_file",
            "file_copy": "filesystem.copy",
            "file_move": "filesystem.move",
            "folder_create": "filesystem.create_folder",
            "terminal_run": "terminal.run_command",
        }
        mapped = mapping.get(tool)
        if mapped and registry.has_tool(mapped):
            return mapped
        # Last resort: if it mentions launch/open, use desktop.launch_application
        if tool and any(w in tool.lower() for w in ("launch", "open", "start", "run_app")):
            if registry.has_tool("desktop.launch_application"):
                return "desktop.launch_application"
        return tool

    # --------------------------------------------------------------- tool invocation dispatch
    def _call_tool(self, tool: str, arguments: Dict, task, engine) -> Any:
        return registry.execute(tool, arguments, caller_level="owner")

    # --------------------------------------------------------------- argument resolution
    def _resolve_args(self, args: Dict, step: Dict, task, engine) -> Dict:
        """Map LLM argument names onto the tool's schema and resolve placeholders.

        Missing required arguments are never guessed: the registry reports the missing
        argument and the LLM fixes it on the next turn.
        """
        tool = self._map_tool(step.get("tool"))
        resolved: Dict[str, Any] = {}

        # Get the tool's input schema to know expected argument names
        schema = registry.get_tool(tool) if tool else None
        expected_props = set()
        if schema:
            expected_props = set(schema.input_schema.get("properties", {}).keys())

        for k, v in args.items():
            if expected_props and k in expected_props:
                mapped_k = k  # already a real argument name: never rename it
            else:
                candidates = _ARG_ALIASES.get(k, [])
                if expected_props:
                    mapped_k = next((c for c in candidates if c in expected_props), k)
                else:
                    mapped_k = candidates[0] if candidates else k
            resolved[mapped_k] = v

        final = {}
        for k, v in resolved.items():
            if isinstance(v, str):
                final[k] = self._resolve_string(v, task, engine, resolve_path=(k in _PATH_ARGS))
            else:
                final[k] = v
        return final

    def _resolve_string(self, s: str, task, engine, resolve_path: bool = True) -> str:
        # Resolve {instruction}, {task_id}, etc.
        s = s.replace("{instruction}", task.instruction)
        s = s.replace("{task_id}", task.id)
        if not resolve_path:
            return s
        # Resolve file paths via filesystem finder (tolerates incorrect paths)
        from control.filesystem import fs
        # If it looks like a path, try to find it
        if s and (s.startswith(("C:", "D:", "~", "%")) or "\\" in s or "/" in s or s.endswith((".txt", ".py", ".docx", ".pdf"))):
            found = fs.find_file(s)
            if found:
                s = found
        return s


executor = Executor()
