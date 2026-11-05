"""Answering a task that is waiting for the user (NEEDS_USER_ACTION) must resume it."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

from tasks import engine as engine_mod  # noqa: E402
from tasks.engine import TaskEngine, TaskStatus  # noqa: E402


class TestNeedsUserReply(unittest.TestCase):
    def setUp(self):
        # Keep the tests away from the real SQLite file.
        p = mock.patch.object(engine_mod, "memory")
        p.start()
        self.addCleanup(p.stop)
        r = mock.patch.object(engine_mod, "recovery")
        r.start()
        self.addCleanup(r.stop)
        self.eng = TaskEngine()
        self.tid = self.eng.create_task("open chrome 1 and chat")
        self.eng.next_queued()                       # worker takes it
        self.assertTrue(self.eng.mark_running(self.tid))
        self.eng.needs_user(self.tid, "Which Chrome profile? Reply 1 or -1.\n\nOBSERVE: x\nACT: y")

    def test_question_is_first_paragraph(self):
        self.assertEqual(TaskEngine.question_of("Q?\n\nOBSERVE: x"), "Q?")
        self.assertEqual(TaskEngine.question_of(None), "")

    def test_reply_requeues_task_with_question_context(self):
        self.assertEqual(self.eng.get_task(self.tid)["status"], "NEEDS_USER_ACTION")
        self.eng.inject_message(self.tid, "  use -1  ")
        t = self.eng.get_task(self.tid)
        self.assertEqual(t["status"], "QUEUED")
        corr = self.eng.get_corrections(self.tid)
        self.assertEqual(len(corr), 1)
        self.assertIn("Which Chrome profile? Reply 1 or -1.", corr[0])
        self.assertIn("use -1", corr[0])
        self.assertNotIn("OBSERVE", corr[0])
        # the queue worker will pick it up again and can mark it running
        self.assertEqual(self.eng.next_queued().id, self.tid)
        self.assertTrue(self.eng.mark_running(self.tid))

    def test_second_reply_while_running_is_plain_correction(self):
        self.eng.inject_message(self.tid, "use -1")
        self.eng.next_queued(); self.eng.mark_running(self.tid)
        self.eng.inject_message(self.tid, "also close it after")
        self.assertEqual(self.eng.get_task(self.tid)["status"], "RUNNING")
        self.assertEqual(self.eng.get_corrections(self.tid)[-1], "also close it after")

    def test_empty_reply_ignored(self):
        self.eng.inject_message(self.tid, "   ")
        self.assertEqual(self.eng.get_task(self.tid)["status"], "NEEDS_USER_ACTION")
        self.assertEqual(self.eng.get_corrections(self.tid), [])

    def test_unknown_task_is_ignored(self):
        self.eng.inject_message("nope", "hello")  # must not raise

    def test_stop_still_works_on_waiting_task(self):
        self.assertTrue(self.eng.stop(self.tid))
        self.assertEqual(self.eng.get_task(self.tid)["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()


class TestEndToEndResume(unittest.TestCase):
    """Real TaskEngine + real ReasoningLoop with a scripted LLM: ask -> reply -> continue."""

    def test_agent_asks_user_answers_agent_finishes(self):
        import json
        import types
        from core.loop import Observation, ReasoningLoop

        class LLM:
            def __init__(self, replies):
                self.replies, self.prompts = list(replies), []

            def chat_strict(self, messages, images=None, **kw):
                self.prompts.append(messages[-1]["content"])
                return types.SimpleNamespace(text=json.dumps(self.replies.pop(0)))

        class Registry:
            def list_tools(self):
                return []

        class Executor:
            def __init__(self):
                self.calls = []

            def execute_action(self, tool, args, task, engine):
                self.calls.append((tool, args))
                return {"ok": True, "tool": tool, "result": {"ok": True}}

        with mock.patch.object(engine_mod, "memory"), mock.patch.object(engine_mod, "recovery"):
            eng = TaskEngine()
            tid = eng.create_task("open a chrome profile and chat")
            task = eng.next_queued()
            eng.mark_running(tid)
            ex = Executor()
            obs = lambda: Observation(text="desktop with icons 1 and -1")

            # Run 1: the agent does not know which profile -> asks.
            llm1 = LLM([{"thought": "ambiguous", "plan": ["pick profile", "chat"],
                         "actions": [], "status": "needs_user",
                         "summary": "Which Chrome profile should I use, 1 or -1?"}])
            r1 = ReasoningLoop(llm1, ex, eng, Registry(), observe=obs, sleep=lambda s: None).run(
                task, goal=task.instruction)
            self.assertEqual(r1.outcome, "needs_user")
            eng.needs_user(tid, r1.summary + "\n\nOBSERVE: evidence")
            self.assertEqual(eng.get_task(tid)["status"], "NEEDS_USER_ACTION")

            # User answers in the dashboard -> task is queued again.
            eng.inject_message(tid, "use -1")
            task2 = eng.next_queued()
            self.assertEqual(task2.id, tid)
            self.assertTrue(eng.mark_running(tid))

            # Run 2: same checklist restored, the answer is in the prompt, the agent finishes.
            llm2 = LLM([{"thought": "got the answer",
                         "actions": [{"tool": "desktop.launch_application",
                                      "arguments": {"app_name_or_path": "-1"}}],
                         "status": "continue"},
                        {"thought": "opened", "plan_update": [{"id": 1, "status": "done"},
                                                              {"id": 2, "status": "done"}],
                         "actions": [], "status": "done", "summary": "Opened profile -1."}])
            r2 = ReasoningLoop(llm2, ex, eng, Registry(), observe=obs, sleep=lambda s: None).run(
                task2, goal=task2.instruction)
            first_prompt = llm2.prompts[0]
            self.assertIn("Which Chrome profile should I use, 1 or -1?", first_prompt)
            self.assertIn("use -1", first_prompt)
            self.assertIn("pick profile", first_prompt)          # old checklist survived
            self.assertEqual(r2.outcome, "done")
            self.assertEqual(ex.calls, [("desktop.launch_application", {"app_name_or_path": "-1"})])
