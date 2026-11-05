"""Task Management Engine.

Persistent task queue with statuses, priorities, scheduling, pause/resume/cancel,
checkpoints, and an exclusive desktop input lock so multiple tasks never interfere
with each other's desktop control.

Statuses: QUEUED, PLANNING, RUNNING, WAITING, RECOVERING, NEEDS_USER_ACTION,
COMPLETED, FAILED, CANCELLED

The engine owns task state and the queue; the Agent (core.agent) drives actual
execution and calls complete/fail on the engine.
"""

import heapq
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from memory.sqlite_store import memory
from recovery.recovery import recovery
from config import STUCK_STATE_THRESHOLD


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RECOVERING = "RECOVERING"
    NEEDS_USER_ACTION = "NEEDS_USER_ACTION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Task:
    id: str
    instruction: str
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = 0
    plan: Optional[Dict] = None
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    scheduled_at: Optional[float] = None
    current_step: int = 0
    retry_count: int = 0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "instruction": self.instruction,
            "status": self.status.value, "priority": self.priority,
            "plan": self.plan, "result": self.result, "error": self.error,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "scheduled_at": self.scheduled_at, "current_step": self.current_step,
            "retry_count": self.retry_count, "metadata": self.metadata,
        }


class TaskEngine:
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._queue: List = []  # heap of (-priority, created_at, task_id)
        self._lock = threading.RLock()
        self._desktop_lock = threading.Lock()  # exclusive desktop input
        self._desktop_lock_holder: Optional[str] = None
        self._stop_events: Dict[str, threading.Event] = {}
        self._paused: Dict[str, threading.Event] = {}  # task_id -> event (set = running)

    # --------------------------------------------------------------- lifecycle
    def create_task(self, instruction: str, priority: int = 0,
                    scheduled_at: Optional[float] = None,
                    metadata: Optional[Dict] = None,
                    task_id: Optional[str] = None) -> str:
        task_id = task_id or str(uuid.uuid4())
        task = Task(
            id=task_id, instruction=instruction, status=TaskStatus.QUEUED,
            priority=priority, scheduled_at=scheduled_at,
            metadata=metadata or {},
        )
        with self._lock:
            self._tasks[task_id] = task
            if not (scheduled_at and scheduled_at > time.time()):
                heapq.heappush(self._queue, (-priority, task.created_at, task_id))
        memory.save_task(task_id, instruction, task.status.value)
        memory.audit(task_id, "task.created", {"instruction": instruction})
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            t = self._tasks.get(task_id)
        return t.to_dict() if t else None

    def list_tasks(self, status: Optional[str] = None) -> List[Dict]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return [t.to_dict() for t in tasks]

    # --------------------------------------------------------------- queue
    def next_queued(self) -> Optional[Task]:
        with self._lock:
            now = time.time()
            while self._queue:
                neg_prio, created, tid = heapq.heappop(self._queue)
                task = self._tasks.get(tid)
                if task is None:
                    continue
                if task.status == TaskStatus.CANCELLED:
                    continue
                if task.scheduled_at and task.scheduled_at > now:
                    heapq.heappush(self._queue, (neg_prio, created, tid))
                    return None
                if task.status == TaskStatus.QUEUED:
                    return task
        return None

    def enqueue(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.QUEUED:
                heapq.heappush(self._queue, (-task.priority, task.created_at, task_id))

    def due_tasks(self) -> List[str]:
        now = time.time()
        due = []
        with self._lock:
            for t in self._tasks.values():
                if t.status == TaskStatus.QUEUED and t.scheduled_at and t.scheduled_at <= now:
                    due.append(t.id)
        return due

    # --------------------------------------------------------------- desktop lock
    def acquire_desktop_lock(self, task_id: str, timeout: float = 5.0) -> bool:
        if self._desktop_lock.acquire(timeout=timeout):
            with self._lock:
                self._desktop_lock_holder = task_id
            return True
        return False

    def release_desktop_lock(self, task_id: str) -> None:
        with self._lock:
            if self._desktop_lock_holder != task_id:
                # Already released (stop()/cleanup frees the lock of a stopped task), or held
                # by another task now. Releasing here would raise or free someone else's lock.
                return
            self._desktop_lock_holder = None
        self._desktop_lock.release()

    def desktop_lock_holder(self) -> Optional[str]:
        with self._lock:
            return self._desktop_lock_holder

    # --------------------------------------------------------------- state transitions
    def mark_running(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in (TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.WAITING):
                return False
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            task.updated_at = time.time()
        memory.save_task(task_id, task.instruction, "RUNNING")
        self._stop_events[task_id] = threading.Event()
        self._paused[task_id] = threading.Event()
        self._paused[task_id].set()
        return True

    def complete(self, task_id: str, result: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()
            task.updated_at = time.time()
        recovery.clear_stuck(task_id)
        memory.save_task(task_id, task.instruction, "COMPLETED", result=result)
        memory.audit(task_id, "task.done", {"result": result})
        self._cleanup_task(task_id)

    def fail(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.error = error
            task.completed_at = time.time()
            task.updated_at = time.time()
        memory.save_task(task_id, task.instruction, "FAILED", error=error)
        memory.audit(task_id, "task.failed", {"error": error})
        self._cleanup_task(task_id)

    def stop(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
            task.status = TaskStatus.CANCELLED
            task.updated_at = time.time()
        ev = self._stop_events.get(task_id)
        if ev:
            ev.set()
        memory.save_task(task_id, task.instruction, "CANCELLED")
        memory.audit(task_id, "task.stopped", {})
        self._cleanup_task(task_id)
        return True

    def pause(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.RUNNING:
                return False
            task.status = TaskStatus.WAITING
            task.updated_at = time.time()
        ev = self._paused.get(task_id)
        if ev:
            ev.clear()
        memory.save_task(task_id, task.instruction, "WAITING")
        return True

    def resume(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.WAITING:
                return False
            task.status = TaskStatus.RUNNING
            task.updated_at = time.time()
        ev = self._paused.get(task_id)
        if ev:
            ev.set()
        memory.save_task(task_id, task.instruction, "RUNNING")
        return True

    @staticmethod
    def question_of(result: Optional[str]) -> str:
        """The question the agent asked: the first paragraph of a needs_user result
        (the rest of that result is internal evidence lines)."""
        return (result or "").strip().split("\n\n")[0].strip()

    def inject_message(self, task_id: str, message: str) -> None:
        """Give a running task a correction, or ANSWER a task that is waiting for the user.

        A task in NEEDS_USER_ACTION has already finished its run. The reply is stored as a
        high-priority correction (together with the question it answers, so the LLM knows
        what "yes" or "profile 3" refers to) and the task is put back in the queue. The
        reasoning loop then restores its saved checklist/notes and carries on.
        """
        message = (message or "").strip()
        if not message:
            return
        memory.audit(task_id, "task.message", {"message": message})
        requeued = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            text = message
            if task.status == TaskStatus.NEEDS_USER_ACTION:
                question = self.question_of(task.result)
                if question:
                    text = f'Answer to your question "{question[:300]}": {message}'
                task.status = TaskStatus.QUEUED
                task.result = None
                task.error = None
                task.completed_at = None
                heapq.heappush(self._queue, (-task.priority, task.created_at, task_id))
                requeued = True
            msgs = task.metadata.get("corrections", [])
            msgs.append(text)
            task.metadata["corrections"] = msgs
            task.updated_at = time.time()
            instruction = task.instruction
        if requeued:
            memory.save_task(task_id, instruction, "QUEUED")
            memory.audit(task_id, "task.resumed_by_user_reply", {})

    # --------------------------------------------------------------- run-time control helpers
    def stop_requested(self, task_id: str) -> bool:
        """True once the task was stopped. stop() pops the stop event during cleanup,
        so the CANCELLED status is checked as well (the event alone is not reliable)."""
        ev = self._stop_events.get(task_id)
        if ev is not None and ev.is_set():
            return True
        with self._lock:
            task = self._tasks.get(task_id)
            return task is not None and task.status == TaskStatus.CANCELLED

    def wait_if_paused(self, task_id: str, poll: float = 0.5) -> bool:
        """Block while the task is paused. Returns True if it was stopped meanwhile."""
        while True:
            if self.stop_requested(task_id):
                return True
            ev = self._paused.get(task_id)
            if ev is None or ev.is_set():
                return False
            time.sleep(poll)

    def get_corrections(self, task_id: str) -> List[str]:
        """Messages the user injected while the task was running (oldest first)."""
        with self._lock:
            task = self._tasks.get(task_id)
            return list(task.metadata.get("corrections", [])) if task else []

    def update_progress(self, task_id: str, plan: Optional[Dict] = None,
                        state: Optional[Dict] = None) -> None:
        """Publish live progress: the checklist (as task.plan) and the loop state."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if plan is not None:
                task.plan = plan
            if state is not None:
                task.metadata["state"] = state
            task.updated_at = time.time()
            instruction, status = task.instruction, task.status.value
        recovery.mark_progress(task_id)
        if state is not None:
            memory.save_checkpoint(task_id, "loop_state", state)
        if plan is not None:
            memory.save_task(task_id, instruction, status, plan=plan)

    def needs_user(self, task_id: str, message: str) -> None:
        """The agent cannot continue without the user (login, missing info, decision)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.NEEDS_USER_ACTION
            task.result = message
            task.updated_at = time.time()
        memory.save_task(task_id, task.instruction, "NEEDS_USER_ACTION", result=message)
        memory.audit(task_id, "task.needs_user", {"message": message})
        self._cleanup_task(task_id)

    def _cleanup_task(self, task_id: str) -> None:
        with self._lock:
            if self._desktop_lock_holder == task_id:
                self._desktop_lock_holder = None
                try:
                    self._desktop_lock.release()
                except Exception:
                    pass
            self._stop_events.pop(task_id, None)
            self._paused.pop(task_id, None)

    # --------------------------------------------------------------- checkpoints / steps
    def save_checkpoint(self, task_id: str, name: str, state: Dict) -> int:
        return memory.save_checkpoint(task_id, name, state)

    def get_checkpoints(self, task_id: str) -> List[Dict]:
        return memory.get_checkpoints(task_id)

    def step_started(self, task_id: str, step_index: int, tool: str, action: str) -> int:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.current_step = step_index
                task.updated_at = time.time()
        recovery.mark_progress(task_id)
        return memory.add_task_step(task_id, step_index, tool, action, "RUNNING")

    def step_finished(self, step_id: int, status: str,
                      result: Optional[str] = None, error: Optional[str] = None) -> None:
        memory.finish_task_step(step_id, status, result, error)


# Singleton instance
task_engine = TaskEngine()
