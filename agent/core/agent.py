"""Autonomous Agent core.

Implements the execution architecture:
  OBSERVE -> UNDERSTAND -> PLAN -> ACT -> VERIFY -> LEARN

With an LLM configured (LLM_API_KEY) the PLAN/ACT part is an iterative reasoning loop
(core.loop): the LLM is called repeatedly, each time seeing the results of its previous
actions, a fresh observation of the PC and its own checklist/notes, until the task is
finished. Without an LLM the agent falls back to the rule-based planner and runs the
resulting plan open-loop.
"""

import time
import traceback
from typing import Any, Dict, List, Optional

from config import (
    AGENT_HISTORY_WINDOW, AGENT_MAX_ACTIONS_PER_TURN, AGENT_MAX_ITERATIONS,
    AGENT_OBSERVE_OCR, LLM_ENABLE_VISION,
)
from control.desktop import DesktopControl
from screen.capture import capture_engine
from discovery.registry import registry
from tasks.engine import task_engine, TaskStatus
from core.loop import LoopResult, Observation, ReasoningLoop
from core.planner import planner, detect_language
from core.executor import executor
from llm.llm import llm
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

            # Earlier tasks + conversation give the LLM continuity across tasks.
            context = self._build_context(task_id)
            memory.add_conversation("user", instruction, {"task_id": task_id})

            self._ensure_tools()
            if llm.available:
                return self._run_with_loop(task, instruction, context, evidence)
            return self._run_with_plan(task, instruction, context, evidence)

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

    # --------------------------------------------------------------- loop mode (LLM)
    def _run_with_loop(self, task, instruction: str, context: str,
                       evidence: List[str]) -> str:
        task_id = task.id
        loop = ReasoningLoop(
            llm=llm, executor=executor, engine=task_engine, registry=registry,
            observe=self._loop_observation,
            max_iterations=AGENT_MAX_ITERATIONS,
            max_actions_per_turn=AGENT_MAX_ACTIONS_PER_TURN,
            history_window=AGENT_HISTORY_WINDOW,
            use_vision=LLM_ENABLE_VISION,
        )
        with task_engine._lock:
            t = task_engine._tasks[task_id]
            t.status = TaskStatus.RUNNING
            t.started_at = time.time()
            t.updated_at = time.time()

        result = loop.run(task, goal=instruction, language=detect_language(instruction),
                          context=context)
        state = result.state
        failed_actions = sum(1 for h in state.history if not h["ok"])
        evidence.append(f"PLAN: goal={instruction} steps={len(state.checklist)}")
        evidence.append(f"ACT: {result.iterations} reasoning turns, {len(state.history)} actions "
                        f"({failed_actions} failed)")
        self._log_step(task_id, "act", {"outcome": result.outcome, "history": state.history[-10:]})

        if result.outcome == "stopped":
            # engine.stop() already marked the task CANCELLED; do not overwrite that.
            evidence.append("STOPPED: task cancelled by user")
            memory.add_conversation("agent", "[stopped] " + state.goal[:200], {"task_id": task_id})
            return "\n".join(evidence)

        verification = self._verify_loop(result)
        evidence.append(f"VERIFY: {verification['summary']}")
        self._log_step(task_id, "verify", verification)

        if verification["passed"]:
            self._learn(task, {"execution_steps": [
                {"tool": h["tool"], "action": h["tool"], "arguments": h["arguments"]}
                for h in state.successful_actions()]}, verification)
            evidence.append("LEARN: saved workflow/skill if applicable")

        final = f"{result.summary}\n\n" + "\n".join(evidence)
        memory.add_conversation("agent", f"[{result.outcome}] {result.summary}"[:500],
                                {"task_id": task_id})
        if result.outcome == "needs_user":
            task_engine.needs_user(task_id, final)
        elif verification["passed"]:
            task_engine.complete(task_id, final)
        else:
            task_engine.fail(task_id, final)
        return final

    def _loop_observation(self) -> Observation:
        """Fresh view of the PC for the LLM: windows, OCR text and (optionally) a screenshot."""
        parts: List[str] = []
        image_path: Optional[str] = None
        try:
            win = self.desktop.get_active_window()
            parts.append(f"active_window: {win.title if win and win.title else 'none'}")
        except Exception as e:
            parts.append(f"active_window: unavailable ({e})")
        try:
            titles = [w.title for w in self.desktop.get_all_windows() if w.title]
            parts.append("open_windows: " + (" | ".join(titles[:15]) or "none"))
        except Exception:
            pass
        try:
            path, pil = self.desktop.screenshot(save=LLM_ENABLE_VISION)
            if LLM_ENABLE_VISION:
                image_path = path
            if AGENT_OBSERVE_OCR and pil is not None:
                text = self.desktop.ocr(image=pil).strip()
                if text:
                    parts.append("screen_text (OCR, may be inaccurate):\n" + text[:1500])
        except Exception as e:
            parts.append(f"screenshot: unavailable ({e})")
        return Observation(text="\n".join(parts), image_path=image_path)

    def _verify_loop(self, result: LoopResult) -> Dict[str, Any]:
        state = result.state
        evidence: List[str] = []
        failed_items = state.failed_items()
        pending_items = state.pending()
        path, _ = self.desktop.screenshot()
        if path:
            evidence.append(f"post-execution screenshot: {path}")
        evidence.append(f"outcome={result.outcome}")
        evidence.append(f"checklist: {len(state.checklist) - len(pending_items) - len(failed_items)}"
                        f"/{len(state.checklist)} done, {len(failed_items)} failed, "
                        f"{len(pending_items)} pending")
        passed = result.outcome == "done" and not failed_items
        return {"passed": passed, "evidence": evidence,
                "summary": f"{'PASS' if passed else 'FAIL'} - {'; '.join(evidence)}"}

    # --------------------------------------------------------------- legacy mode (no LLM)
    def _run_with_plan(self, task, instruction: str, context: str,
                       evidence: List[str]) -> str:
        task_id = task.id

        # ---------- UNDERSTAND + PLAN ----------
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

        # ---------- ACT ----------
        result = executor.execute_plan(task, task_engine)
        evidence.append(f"ACT: {result}")
        self._log_step(task_id, "act", result)

        if task_engine.stop_requested(task_id):
            evidence.append("STOPPED: task cancelled by user")
            return "\n".join(evidence)

        # ---------- VERIFY ----------
        verification = self._verify(task, plan, result)
        evidence.append(f"VERIFY: {verification['summary']}")
        self._log_step(task_id, "verify", verification)

        # ---------- LEARN ----------
        self._learn(task, plan, verification)
        evidence.append("LEARN: saved workflow/skill if applicable")

        final = "\n".join(evidence)
        memory.add_conversation("agent", f"[{'done' if verification['passed'] else 'failed'}] "
                                         f"{instruction}"[:500], {"task_id": task_id})
        if verification["passed"]:
            task_engine.complete(task_id, final)
        else:
            task_engine.fail(task_id, final)
        return final

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
