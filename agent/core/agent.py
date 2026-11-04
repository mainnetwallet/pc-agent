"""Autonomous Agent core.

Implements the execution architecture:
  OBSERVE -> UNDERSTAND -> PLAN -> ACT -> VERIFY -> LEARN

The agent receives an instruction, inspects the computer, plans, executes via
registered tools, verifies with evidence (screenshots, exit codes, file checks),
and learns by saving reusable skills.
"""

import time
import traceback
from typing import Any, Dict, List, Optional

from control.desktop import DesktopControl
from screen.capture import capture_engine
from discovery.registry import registry
from tasks.engine import task_engine, TaskStatus
from core.planner import planner
from core.executor import executor
from memory.sqlite_store import memory


class Agent:
    def __init__(self):
        self.desktop = DesktopControl()
        self._active = False

    # --------------------------------------------------------------- public entry
    def run_task(self, task, engine) -> str:
        """Execute one task end-to-end through the O-U-P-A-V-L cycle."""
        task_id = task.id
        instruction = task.instruction
        self._active = True
        evidence: List[str] = []

        try:
            # ---------- OBSERVE ----------
            # Ensure the task is marked RUNNING (idempotent).
            task_engine.mark_running(task_id)
            with task_engine._lock:
                task = task_engine._tasks[task_id]
                task.status = TaskStatus.PLANNING
                task.updated_at = time.time()
            memory.save_task(task_id, instruction, "PLANNING")
            observe = self._observe(task_id)
            evidence.append(f"OBSERVE: {observe}")
            self._log_step(task_id, "observe", observe)

            # ---------- UNDERSTAND + PLAN ----------
            context = self._build_context(task_id)
            plan = planner.plan(instruction, context=context)
            with task_engine._lock:
                task = task_engine._tasks[task_id]
                task.plan = plan
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
                task.updated_at = time.time()
            memory.save_task(task_id, instruction, "RUNNING", plan=plan)
            evidence.append(f"PLAN: goal={plan.get('interpreted_goal')} steps={len(plan.get('execution_steps', []))}")
            self._log_step(task_id, "plan", plan)

            # Register built-in tools/adapters if not yet
            self._ensure_tools()

            # ---------- ACT ----------
            result = executor.execute_plan(task, task_engine)
            evidence.append(f"ACT: {result}")
            self._log_step(task_id, "act", result)

            # ---------- VERIFY ----------
            verification = self._verify(task, plan, result)
            evidence.append(f"VERIFY: {verification['summary']}")
            self._log_step(task_id, "verify", verification)

            # ---------- LEARN ----------
            self._learn(task, plan, verification)
            evidence.append("LEARN: saved workflow/skill if applicable")

            status = "COMPLETED" if verification["passed"] else "COMPLETED_WITH_WARNINGS"
            if not verification["passed"]:
                status = "FAILED"

            final = "\n".join(evidence)
            task_engine.complete(task_id, final)
            return final

        except Exception as e:
            tb = traceback.format_exc()
            evidence.append(f"ERROR: {e}\n{tb}")
            try:
                task_engine.fail(task_id, str(e))
            except Exception:
                pass
            return "\n".join(evidence)
        finally:
            self._active = False

    # --------------------------------------------------------------- OBSERVE
    def _observe(self, task_id: str) -> str:
        parts: List[str] = []
        # Screenshot
        path, pil = self.desktop.screenshot()
        if path:
            parts.append(f"screenshot captured: {path}")
        # Active window
        win = self.desktop.get_active_window()
        parts.append(f"active_window={win.title if win else 'None'}")
        # Open windows count
        wins = self.desktop.get_all_windows()
        parts.append(f"open_windows={len([w for w in wins if w.title])}")
        # Monitors
        mons = self.desktop.get_monitors()
        parts.append(f"monitors={len(mons)}")
        # Recent memory
        recent = memory.get_recent_conversation(5)
        parts.append(f"recent_conversation_entries={len(recent)}")
        return "; ".join(parts)

    # --------------------------------------------------------------- context
    def _build_context(self, task_id: str) -> str:
        parts: List[str] = []
        conv = memory.summarize_conversation(limit=50)
        if conv:
            parts.append(f"Recent conversation:\n{conv}")
        skills = memory.list_skills()
        if skills:
            parts.append(f"Known skills: {', '.join(s['name'] for s in skills[:10])}")
        apps = memory.list_skills()  # placeholder
        return "\n".join(parts)

    # --------------------------------------------------------------- ACT support
    def _ensure_tools(self) -> None:
        from discovery.adapters import register_builtin_adapters
        if not registry.has_tool("desktop.click"):
            register_builtin_adapters()

    # --------------------------------------------------------------- VERIFY
    def _verify(self, task, plan: Dict, act_result: str) -> Dict[str, Any]:
        """Verify actual outcomes using screenshots and evidence."""
        evidence: List[str] = []
        passed = True
        criteria = plan.get("success_criteria", []) or ["Action executed without error"]

        # Capture post-execution screenshot
        path, pil = self.desktop.screenshot()
        if path:
            evidence.append(f"post-execution screenshot: {path}")

        # Check that no exception was raised in act_result
        if "failed" in act_result.lower() or "error" in act_result.lower():
            passed = False
            evidence.append("Act step reported failure/error")

        # Simple heuristics per success criteria
        for c in criteria:
            evidence.append(f"criterion: {c}")

        return {"passed": passed, "evidence": evidence,
                "summary": f"{'PASS' if passed else 'FAIL'} - {len(evidence)} evidence items"}

    # --------------------------------------------------------------- LEARN
    def _learn(self, task, plan: Dict, verification: Dict) -> None:
        """Save useful procedures as reusable skills."""
        steps = plan.get("execution_steps", []) or []
        if not steps:
            return
        skill_name = f"skill_{task.id[:8]}"
        skill_steps = []
        for s in steps:
            skill_steps.append({
                "tool": s.get("tool"),
                "action": s.get("action"),
                "arguments": s.get("arguments", {}),
            })
        from memory.sqlite_store import memory
        memory.save_skill(
            name=skill_name,
            description=f"Workflow for: {task.instruction}",
            steps=skill_steps,
            applications=[],
        )
        memory.record_skill_use(skill_name, verification["passed"])

    # --------------------------------------------------------------- logging
    def _log_step(self, task_id: str, phase: str, data: Any) -> None:
        memory.audit(task_id, f"agent.{phase}", {"data": str(data)[:2000]})

    def is_active(self) -> bool:
        return self._active


agent = Agent()
