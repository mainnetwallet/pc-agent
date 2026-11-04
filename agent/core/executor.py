"""Executor: carries out tool calls through the registry.

Two entry points:
  * execute_action(...)  - ONE tool call for the reasoning loop (core.loop). It never
                           guesses missing arguments and always returns a normalized
                           {"ok", "tool", "result"/"error"} dict so failures reach the LLM.
  * execute_plan(...)    - legacy open-loop runner for a pre-built plan (used only by the
                           rule-based fallback when no LLM is configured).

GUI operations hold the exclusive desktop input lock.
"""

import os
import time
import threading
from typing import Any, Dict, List, Optional

from control.desktop import DesktopControl
from control.windows import windows
from control.filesystem import fs
from control.system import system
from control.terminal import terminal
from browser.chrome import browser as browser_control
from screen.capture import capture_engine
from discovery.registry import registry
from tasks.engine import task_engine
from recovery.recovery import recovery
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
            args = self._resolve_args(arguments or {}, {"tool": mapped}, task, engine, infer=False)
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

    # --------------------------------------------------------------- legacy plan runner
    def execute_plan(self, task, engine) -> str:
        plan = task.plan or {}
        steps = plan.get("execution_steps", []) or []
        if not steps:
            # No steps: do a minimal observe action
            self.desktop.screenshot()
            return "No execution steps in plan; observed desktop."

        results: List[str] = []
        for idx, step in enumerate(steps):
            # Blocks while paused; True means the task was stopped.
            if engine.wait_if_paused(task.id):
                return "Task stopped by user."

            step_id = engine.step_started(task.id, idx, step.get("tool", ""), step.get("action", ""))
            try:
                outcome = self._execute_step(step, task, engine)
                engine.step_finished(step_id, "COMPLETED", result=str(outcome))
                results.append(f"Step {idx+1} ({step.get('action')}): {outcome}")
            except Exception as e:
                engine.step_finished(step_id, "FAILED", error=str(e))
                # Attempt recovery
                strategy = recovery.recover(task.id, e, {"step": step})
                # Try bounded retry of the step
                try:
                    outcome = self._execute_step(step, task, engine)
                    results.append(f"Step {idx+1} recovered ({step.get('action')}): {outcome}")
                except Exception as e2:
                    return f"Step {idx+1} failed: {e2}. Recovery: {strategy['details']}"
        return "\n".join(results) if results else "Plan executed."

    # --------------------------------------------------------------- step execution
    def _execute_step(self, step: Dict, task, engine) -> Any:
        tool = step.get("tool")
        arguments = step.get("arguments", {}) or {}

        # Resolve arguments: replace placeholder references
        arguments = self._resolve_args(arguments, step, task, engine)

        # Map unknown/invented tool names to registered tools
        tool = self._map_tool(tool)

        # Desktop/GUI operations require exclusive lock
        if tool and tool.startswith("desktop."):
            if not engine.acquire_desktop_lock(task.id, timeout=10.0):
                raise RuntimeError("Could not acquire desktop input lock")
            try:
                raw = registry.execute(tool, arguments, caller_level="owner")
            finally:
                engine.release_desktop_lock(task.id)
        else:
            raw = registry.execute(tool, arguments, caller_level="owner")

        # A tool that reports failure must not count as a completed step.
        norm = self._normalize(tool or "", raw)
        if not norm["ok"]:
            raise RuntimeError(norm["error"])
        return raw

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
    def _resolve_args(self, args: Dict, step: Dict, task, engine, infer: bool = True) -> Dict:
        """Map LLM argument names onto the tool's schema and resolve placeholders.

        infer=True  (legacy plans): missing required arguments are guessed from the task.
        infer=False (reasoning loop): never guess; the registry reports the missing
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

        if infer:
            required = schema.input_schema.get("required", []) if schema else []
            for req in required:
                if req not in resolved or not resolved[req]:
                    inferred = self._infer_argument(req, tool, step, task, engine)
                    if inferred is not None:
                        resolved[req] = inferred

        final = {}
        for k, v in resolved.items():
            if isinstance(v, str):
                final[k] = self._resolve_string(v, task, engine, resolve_path=(k in _PATH_ARGS))
            else:
                final[k] = v
        return final

    def _infer_argument(self, arg_name: str, tool: Optional[str],
                        step: Dict, task, engine) -> Optional[str]:
        """Infer a missing required argument from instruction/action context (legacy only)."""
        instruction = task.instruction.lower()
        action = step.get("action", "").lower()

        # desktop.launch_application -> app_name_or_path
        if arg_name == "app_name_or_path" and tool == "desktop.launch_application":
            # Extract app name from instruction
            import re
            # Try to find app name mentions
            for keyword in ["notepad", "chrome", "edge", "firefox", "calculator", "calc",
                            "word", "excel", "powerpoint", "cmd", "powershell", "paint"]:
                if keyword in instruction:
                    return keyword.capitalize() if keyword != "calc" else "Calculator"
            # Fallback: look at action text
            for keyword in ["notepad", "chrome", "edge", "firefox"]:
                if keyword in action:
                    return keyword.capitalize()
            return "Notepad"  # safe default

        # desktop.type_text -> text
        if arg_name == "text" and tool == "desktop.type_text":
            # Extract quoted text from instruction
            import re
            match = re.search(r'["\']([^"\']+)["\']', task.instruction)
            if match:
                return match.group(1)
            # Fallback: use a generic message
            return "Hello from PC Agent"

        # filesystem.write_file -> path and content
        if arg_name == "path" and tool == "filesystem.write_file":
            from control.filesystem import fs
            found = fs.find_file("Desktop")
            if found:
                return os.path.join(found, "agent_output.txt")
            return os.path.join(os.path.expanduser("~"), "Desktop", "agent_output.txt")

        if arg_name == "content" and tool == "filesystem.write_file":
            return "Generated by PC Agent"

        # desktop.click / desktop.press -> x,y or key
        if arg_name == "key" and tool == "desktop.press":
            if "save" in action or "ctrl+s" in action:
                return "s"
            return "enter"

        return None

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
