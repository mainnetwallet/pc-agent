"""Dynamic Tool Registry.

Tools are registered with a JSON Schema describing inputs/outputs, a permission
level, and an executable callable. Before execution a tool passes:
  1. schema validation of inputs
  2. permission check against the current context
  3. safe testing (dry-run when supported)

Failed/exhausted tools are tracked so the recovery engine can avoid repeating them.
"""

import inspect
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

try:
    import jsonschema  # type: ignore
    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover
    jsonschema = None
    _HAS_JSONSCHEMA = False

from memory.sqlite_store import memory
from config import MAX_RETRIES


PERMISSION_LEVELS = ("public", "owner", "admin")


@dataclass
class ToolSchema:
    name: str
    description: str
    input_schema: Dict[str, Any]  # JSON Schema (draft-07 subset)
    output_schema: Optional[Dict[str, Any]] = None
    permission: str = "owner"  # public | owner | admin
    category: str = "general"
    dry_run: Optional[Callable] = None  # optional safe test callable
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["dry_run"] = self.dry_run is not None
        return d


@dataclass
class ToolExecutionRecord:
    tool_name: str
    success: bool
    duration_seconds: float
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class DynamicToolRegistry:
    """Registry of validated, permission-checked tools."""

    def __init__(self):
        self._tools: Dict[str, ToolSchema] = {}
        self._callables: Dict[str, Callable] = {}
        self._lock = threading.RLock()
        self._execution_history: List[ToolExecutionRecord] = []
        self._failures: Dict[str, int] = {}  # tool_name -> consecutive failures

    # --------------------------------------------------------------- registration
    def register(self, name: str, callable_fn: Callable,
                 description: str = "",
                 input_schema: Optional[Dict[str, Any]] = None,
                 output_schema: Optional[Dict[str, Any]] = None,
                 permission: str = "owner",
                 category: str = "general",
                 dry_run: Optional[Callable] = None) -> bool:
        """Register a tool. Returns True on success."""
        if input_schema is None:
            input_schema = {"type": "object", "properties": {}, "additionalProperties": True}
        schema = ToolSchema(
            name=name, description=description, input_schema=input_schema,
            output_schema=output_schema, permission=permission, category=category,
            dry_run=dry_run,
        )
        # Validate the schema itself is valid JSON Schema (if jsonschema available)
        if _HAS_JSONSCHEMA:
            try:
                jsonschema.Draft7Validator.check_schema(input_schema)
            except Exception as e:
                raise ValueError(f"Invalid input schema for tool {name}: {e}")

        with self._lock:
            self._tools[name] = schema
            self._callables[name] = callable_fn
            self._failures[name] = 0
        memory.audit(None, "tool.registered", {"tool": name, "permission": permission})
        return True

    def unregister(self, name: str) -> bool:
        with self._lock:
            if name in self._tools:
                del self._tools[name]
                del self._callables[name]
                self._failures.pop(name, None)
                return True
        return False

    def list_tools(self, permission: Optional[str] = None,
                   category: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for t in self._tools.values():
                if permission and t.permission != permission:
                    continue
                if category and t.category != category:
                    continue
                out.append(t.to_dict())
            return out

    def get_tool(self, name: str) -> Optional[ToolSchema]:
        with self._lock:
            return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        with self._lock:
            return name in self._tools

    # --------------------------------------------------------------- validation
    def validate_inputs(self, name: str, arguments: Dict[str, Any]) -> List[str]:
        """Validate arguments against the tool's input schema. Returns error messages."""
        schema = self.get_tool(name)
        if schema is None:
            return [f"Tool '{name}' not registered"]
        errors: List[str] = []
        if not _HAS_JSONSCHEMA:
            # Minimal built-in validation: required fields present
            required = schema.input_schema.get("required", [])
            for req in required:
                if req not in arguments:
                    errors.append(f"Missing required argument: {req}")
            return errors
        try:
            jsonschema.Draft7Validator(schema.input_schema).validate(arguments)
        except jsonschema.ValidationError as e:
            errors.append(str(e.message))
        return errors

    def check_permission(self, name: str, caller_level: str = "owner") -> List[str]:
        """Return error list if the caller lacks permission."""
        level_rank = {l: i for i, l in enumerate(PERMISSION_LEVELS)}
        schema = self.get_tool(name)
        if schema is None:
            return [f"Tool '{name}' not registered"]
        if level_rank.get(caller_level, 0) < level_rank.get(schema.permission, 0):
            return [f"Insufficient permission for tool '{name}' (requires {schema.permission})"]
        return []

    # --------------------------------------------------------------- execution
    def execute(self, name: str, arguments: Dict[str, Any],
                caller_level: str = "owner", dry_run_first: bool = True) -> Dict[str, Any]:
        """Execute a registered tool with validation, permission, and safety checks."""
        start = time.time()
        result: Dict[str, Any] = {"ok": False}

        with self._lock:
            schema = self._tools.get(name)
            callable_fn = self._callables.get(name)
        if schema is None or callable_fn is None:
            result["error"] = f"Tool '{name}' not registered"
            self._record(name, False, 0.0, result["error"])
            return result

        # 1. Schema validation
        input_errors = self.validate_inputs(name, arguments)
        if input_errors:
            result["error"] = "; ".join(input_errors)
            self._record(name, False, time.time() - start, result["error"])
            return result

        # 2. Permission check
        perm_errors = self.check_permission(name, caller_level)
        if perm_errors:
            result["error"] = "; ".join(perm_errors)
            self._record(name, False, time.time() - start, result["error"])
            return result

        # 3. Dry-run / safe test
        if dry_run_first and schema.dry_run is not None:
            try:
                dry_ok = schema.dry_run(arguments)
                if not dry_ok:
                    result["error"] = f"Dry-run failed for tool '{name}'"
                    self._record(name, False, time.time() - start, result["error"])
                    return result
            except Exception as e:
                result["error"] = f"Dry-run error for '{name}': {e}"
                self._record(name, False, time.time() - start, result["error"])
                return result

        # 4. Execute
        try:
            output = callable_fn(**arguments) if isinstance(arguments, dict) else callable_fn(arguments)
            duration = time.time() - start
            result["ok"] = True
            result["result"] = output
            result["duration_seconds"] = duration
            self._record(name, True, duration)
            # Reset consecutive failure count on success
            with self._lock:
                self._failures[name] = 0
            return result
        except Exception as e:
            duration = time.time() - start
            result["error"] = str(e)
            result["duration_seconds"] = duration
            self._record(name, False, duration, str(e))
            return result

    def _record(self, name: str, success: bool, duration: float,
                error: Optional[str] = None) -> None:
        rec = ToolExecutionRecord(tool_name=name, success=success,
                                  duration_seconds=duration, error=error)
        with self._lock:
            self._execution_history.append(rec)
            if len(self._execution_history) > 10000:
                self._execution_history = self._execution_history[-5000:]
            if not success:
                self._failures[name] = self._failures.get(name, 0) + 1

    def consecutive_failures(self, name: str) -> int:
        with self._lock:
            return self._failures.get(name, 0)

    def is_circuit_open(self, name: str, threshold: int = 5) -> bool:
        """Circuit breaker: too many consecutive failures => skip this tool."""
        return self.consecutive_failures(name) >= threshold

    # --------------------------------------------------------------- persistence
    def get_execution_history(self, tool_name: Optional[str] = None,
                              limit: int = 100) -> List[dict]:
        with self._lock:
            hist = self._execution_history
            if tool_name:
                hist = [r for r in hist if r.tool_name == tool_name]
            return [asdict(r) for r in hist[-limit:]]


# Singleton instance
registry = DynamicToolRegistry()
