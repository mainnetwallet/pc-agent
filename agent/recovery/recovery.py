"""Self-Recovery Engine.

Handles ordinary failures independently: bounded retries, exponential backoff,
stuck-state detection, execution deadlines, checkpoints, and recovery logs.
Never enters infinite retry loops.
"""

import time
import traceback
from typing import Any, Callable, Dict, Optional

from memory.sqlite_store import memory
from config import MAX_RETRIES, RETRY_BASE_BACKOFF, STUCK_STATE_THRESHOLD, TASK_EXECUTION_TIMEOUT


class RecoveryEngine:
    def __init__(self):
        self._stuck_tracker: Dict[str, float] = {}  # task_id -> last progress time
        self._deadlines: Dict[str, float] = {}      # task_id -> deadline timestamp

    # --------------------------------------------------------------- retry with backoff
    def retry(self, func: Callable, *args, max_retries: Optional[int] = None,
              base_backoff: Optional[float] = None, task_id: Optional[str] = None,
              **kwargs) -> Any:
        """Retry a function with exponential backoff. Bounded by max_retries."""
        max_retries = MAX_RETRIES if max_retries is None else max_retries
        base_backoff = RETRY_BASE_BACKOFF if base_backoff is None else base_backoff
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                tb = traceback.format_exc()
                if task_id:
                    memory.audit(task_id, "retry.attempt", {
                        "attempt": attempt, "max_retries": max_retries,
                        "error": str(e),
                    })
                if attempt < max_retries:
                    delay = base_backoff * (2 ** (attempt - 1))
                    time.sleep(delay)
        raise last_error

    # --------------------------------------------------------------- stuck-state detection
    def mark_progress(self, task_id: str) -> None:
        self._stuck_tracker[task_id] = time.time()

    def is_stuck(self, task_id: str, threshold: Optional[float] = None) -> bool:
        threshold = STUCK_STATE_THRESHOLD if threshold is None else threshold
        last = self._stuck_tracker.get(task_id)
        if last is None:
            return False
        return (time.time() - last) > threshold

    def clear_stuck(self, task_id: str) -> None:
        self._stuck_tracker.pop(task_id, None)

    # --------------------------------------------------------------- deadlines
    def set_deadline(self, task_id: str, timeout_seconds: Optional[int] = None) -> None:
        timeout_seconds = TASK_EXECUTION_TIMEOUT if timeout_seconds is None else timeout_seconds
        self._deadlines[task_id] = time.time() + timeout_seconds

    def is_deadline_exceeded(self, task_id: str) -> bool:
        deadline = self._deadlines.get(task_id)
        if deadline is None:
            return False
        return time.time() > deadline

    # --------------------------------------------------------------- recovery strategy
    def recover(self, task_id: str, error: Exception, context: Dict) -> Dict[str, Any]:
        """Analyze a failure and propose a recovery strategy."""
        strategy = {
            "task_id": task_id,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "context": context,
            "recommended": "retry",
            "details": "",
        }
        msg = str(error).lower()
        if "timed out" in msg or "timeout" in msg:
            strategy["recommended"] = "retry_longer"
            strategy["details"] = "Operation timed out; retry with a longer timeout."
        elif "not found" in msg or "no such" in msg or "missing" in msg:
            strategy["recommended"] = "alternative_method"
            strategy["details"] = "Resource not found; try an alternative path/method."
        elif "access denied" in msg or "permission" in msg:
            strategy["recommended"] = "request_elevation"
            strategy["details"] = "Permission denied; may require elevation or is blocked."
        elif "connection" in msg or "network" in msg:
            strategy["recommended"] = "retry_network"
            strategy["details"] = "Network issue; retry after backoff."
        else:
            strategy["recommended"] = "retry"
            strategy["details"] = "Generic error; attempt bounded retry."

        if task_id:
            memory.audit(task_id, "recovery.strategy", strategy)
            memory.record_failed_pattern(strategy["error"], context)
        return strategy


recovery = RecoveryEngine()
