"""Executor: carries out a plan through registered tools.

Selects appropriate tools, executes steps, verifies outcomes, and handles
ordinary errors via the recovery engine. GUI operations hold the exclusive
desktop input lock.
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


class Executor:
    def __init__(self):
        self.desktop = DesktopControl()

    # --------------------------------------------------------------- entry point
    def execute_plan(self, task, engine) -> str:
        plan = task.plan or {}
        steps = plan.get("execution_steps", []) or []
        if not steps:
            # No steps: do a minimal observe action
            self.desktop.screenshot()
            return "No execution steps in plan; observed desktop."

        results: List[str] = []
        for idx, step in enumerate(steps):
            if engine._stop_events.get(task.id, threading.Event()).is_set():
                return "Task stopped by user."
            # pause handling
            pause_ev = engine._paused.get(task.id)
            while pause_ev and not pause_ev.is_set():
                time.sleep(0.5)
                if engine._stop_events.get(task.id, threading.Event()).is_set():
                    return "Task stopped by user."

            engine.step_started(task.id, idx, step.get("tool", ""), step.get("action", ""))
            try:
                outcome = self._execute_step(step, task, engine)
                engine.step_finished(0, "COMPLETED", result=str(outcome))
                results.append(f"Step {idx+1} ({step.get('action')}): {outcome}")
            except Exception as e:
                engine.step_finished(0, "FAILED", error=str(e))
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
        action = step.get("action", "")
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
                return registry.execute(tool, arguments, caller_level="owner")
            finally:
                engine.release_desktop_lock(task.id)
        else:
            return registry.execute(tool, arguments, caller_level="owner")

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
        tool = step.get("tool")
        tool = self._map_tool(tool)
        resolved = {}

        # Argument name mapping: LLM-invented names -> schema names
        arg_map = {
            "command": "app_name_or_path",
            "application_name": "app_name_or_path",
            "app": "app_name_or_path",
            "program": "app_name_or_path",
            "message": "text",
            "content_text": "text",
            "input_text": "text",
            "keys": "keys",
            "key_combo": "keys",
            "file_path": "path",
            "target_path": "path",
            "source_path": "path",
            "dest_path": "dst",
            "destination": "dst",
        }

        # Get the tool's input schema to know expected argument names
        schema = registry.get_tool(tool) if tool else None
        expected_props = set()
        if schema:
            expected_props = set(schema.input_schema.get("properties", {}).keys())

        # Map provided arguments to expected names
        for k, v in args.items():
            mapped_k = arg_map.get(k, k)
            # Only keep if it's an expected property or we don't have a schema
            if not expected_props or mapped_k in expected_props:
                resolved[mapped_k] = v
            else:
                # Still keep it; the schema validation may be lenient
                resolved[mapped_k] = v

        # Fill in missing required arguments from context
        required = []
        if schema:
            required = schema.input_schema.get("required", [])
        for req in required:
            if req not in resolved or not resolved[req]:
                inferred = self._infer_argument(req, tool, step, task, engine)
                if inferred is not None:
                    resolved[req] = inferred

        # Resolve string values (placeholders, paths)
        final = {}
        for k, v in resolved.items():
            if isinstance(v, str):
                final[k] = self._resolve_string(v, task, engine)
            else:
                final[k] = v
        return final

    def _infer_argument(self, arg_name: str, tool: Optional[str],
                        step: Dict, task, engine) -> Optional[str]:
        """Infer a missing required argument from instruction/action context."""
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

    def _resolve_string(self, s: str, task, engine) -> str:
        # Resolve {instruction}, {task_id}, etc.
        s = s.replace("{instruction}", task.instruction)
        s = s.replace("{task_id}", task.id)
        # Resolve file paths via filesystem finder (tolerates incorrect paths)
        from control.filesystem import fs
        # If it looks like a path, try to find it
        if s and (s.startswith(("C:", "D:", "~", "%")) or "\\" in s or "/" in s or s.endswith((".txt", ".py", ".docx", ".pdf"))):
            found = fs.find_file(s)
            if found:
                s = found
        return s


executor = Executor()
