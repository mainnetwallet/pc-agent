"""Headless tests for the reasoning loop (core.loop).

No desktop, no network, no real LLM: the LLM, executor and task engine are fakes, so
these run on any OS. They check the behaviour the loop exists for: the LLM is called
repeatedly, sees the real result of every earlier action, and keeps track of what is
done and what is left.
"""

import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

from core.loop import (  # noqa: E402
    Observation, ReasoningLoop, TaskState, extract_json_object, format_tools,
)

LAUNCH = {"tool": "desktop.launch_application", "arguments": {"app_name_or_path": "Notepad"}}


class FakeLLM:
    """Returns scripted replies and records every prompt it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.calls = 0

    def chat_strict(self, messages, images=None, **kw):
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        if not self.replies:
            raise AssertionError("LLM called more often than scripted")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return types.SimpleNamespace(text=r if isinstance(r, str) else json.dumps(r))


class RejectsImagesLLM(FakeLLM):
    def __init__(self, replies):
        super().__init__(replies)
        self.image_calls = 0

    def chat_strict(self, messages, images=None, **kw):
        if images:
            self.image_calls += 1
            raise RuntimeError("images unsupported")
        return super().chat_strict(messages, images=None)


class FakeRegistry:
    def list_tools(self):
        return [
            {"name": "desktop.launch_application", "description": "Launch an app",
             "input_schema": {"type": "object",
                              "properties": {"app_name_or_path": {"type": "string"}},
                              "required": ["app_name_or_path"]}},
            {"name": "desktop.screenshot", "description": "Capture the screen",
             "input_schema": {"type": "object",
                              "properties": {"monitor_index": {"type": "integer"}},
                              "required": []}},
        ]


class FakeExecutor:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda tool, args: {"ok": True, "tool": tool,
                                                       "result": {"ok": True}})

    def execute_action(self, tool, args, task, engine):
        self.calls.append((tool, args))
        return self.handler(tool, args)


class FakeEngine:
    def __init__(self):
        self.corrections = []
        self.stop_flag = False
        self.progress = []
        self.steps = []
        self._id = 0

    def wait_if_paused(self, task_id, poll=0.5):
        return self.stop_flag

    def stop_requested(self, task_id):
        return self.stop_flag

    def get_corrections(self, task_id):
        return list(self.corrections)

    def step_started(self, task_id, idx, tool, action):
        self._id += 1
        self.steps.append(("start", tool))
        return self._id

    def step_finished(self, step_id, status, result=None, error=None):
        self.steps.append(("end", status))

    def update_progress(self, task_id, plan=None, state=None):
        self.progress.append({"plan": plan, "state": state})


def make_task():
    return types.SimpleNamespace(id="t1", instruction="goal", metadata={})


def make_loop(llm, executor=None, engine=None, **kw):
    engine = engine or FakeEngine()
    executor = executor or FakeExecutor()
    kw.setdefault("observe", lambda: Observation("active_window: Notepad"))
    kw.setdefault("sleep", lambda s: None)
    return ReasoningLoop(llm=llm, executor=executor, engine=engine,
                         registry=FakeRegistry(), **kw), engine, executor


class TestContextCarriesAcrossCalls(unittest.TestCase):
    def test_each_call_sees_earlier_results_and_checklist_progress(self):
        llm = FakeLLM([
            {"plan": ["open notepad", "type hello", "save file"],
             "actions": [LAUNCH], "status": "continue"},
            {"plan_update": [{"id": 1, "status": "done"}], "notes": "notepad is open",
             "actions": [{"tool": "desktop.type_text", "arguments": {"text": "hello"}}],
             "status": "continue"},
            {"plan_update": [{"id": 2, "status": "done"}],
             "actions": [{"tool": "desktop.hotkey", "arguments": {"keys": ["ctrl", "s"]}}],
             "status": "continue"},
            {"plan_update": [{"id": 3, "status": "done"}], "actions": [],
             "status": "done", "summary": "File save hoyeche"},
        ])
        loop, engine, executor = make_loop(llm)
        res = loop.run(make_task(), goal="open notepad, type hello, save")

        self.assertEqual(res.outcome, "done")
        self.assertEqual(res.summary, "File save hoyeche")
        self.assertEqual(llm.calls, 4)
        self.assertEqual([c[0] for c in executor.calls],
                         ["desktop.launch_application", "desktop.type_text", "desktop.hotkey"])

        self.assertIn("not created yet", llm.prompts[0])
        # Turn 2: sees action #1 and its result, item 1 not yet marked.
        self.assertIn("#1 desktop.launch_application", llm.prompts[1])
        self.assertIn("-> OK", llm.prompts[1])
        self.assertIn("1. [ ] open notepad", llm.prompts[1])
        # Turn 3: item 1 is done, item 2 pending, notes and both actions are carried.
        self.assertIn("1. [x] open notepad", llm.prompts[2])
        self.assertIn("2. [ ] type hello", llm.prompts[2])
        self.assertIn("notepad is open", llm.prompts[2])
        self.assertIn("#2 desktop.type_text", llm.prompts[2])
        self.assertIn("3. [ ] save file", llm.prompts[3])

        final = engine.progress[-1]
        self.assertTrue(all(c["status"] == "done" for c in final["state"]["checklist"]))
        self.assertEqual(final["plan"]["execution_steps"][0]["status"], "done")
        self.assertEqual(len(final["state"]["history"]), 3)

    def test_old_actions_are_compressed_so_the_prompt_stays_small(self):
        replies = [{"plan": ["many steps"],
                    "actions": [{"tool": "desktop.type_text",
                                 "arguments": {"text": f"unique-{i}"}}],
                    "status": "continue"} for i in range(6)]
        replies.append({"plan_update": [{"id": 1, "status": "done"}], "status": "done",
                        "summary": "ok"})
        llm = FakeLLM(replies)
        loop, _, _ = make_loop(llm, history_window=3)
        res = loop.run(make_task(), goal="g")

        self.assertEqual(res.outcome, "done")
        last = llm.prompts[6]
        self.assertIn("Earlier (3 actions, 3 ok)", last)
        self.assertIn("unique-5", last)
        self.assertNotIn("unique-0", last)

    def test_saved_state_is_resumed(self):
        task = make_task()
        task.metadata["state"] = TaskState(
            goal="g", checklist=[{"id": 1, "text": "already done step", "status": "done"}]
        ).to_dict()
        llm = FakeLLM([{"status": "done", "summary": "ok"}])
        loop, _, _ = make_loop(llm)
        loop.run(task, goal="g")
        self.assertIn("1. [x] already done step", llm.prompts[0])


class TestFeedbackAndGuards(unittest.TestCase):
    def test_failed_action_error_is_fed_back(self):
        results = iter([{"ok": False, "tool": "desktop.launch_application",
                         "error": "Could not launch Notepadd"},
                        {"ok": True, "tool": "desktop.launch_application", "result": {"ok": True}}])
        llm = FakeLLM([
            {"plan": ["open"], "actions": [
                {"tool": "desktop.launch_application",
                 "arguments": {"app_name_or_path": "Notepadd"}}], "status": "continue"},
            {"actions": [LAUNCH], "status": "continue"},
            {"plan_update": [{"id": 1, "status": "done"}], "status": "done", "summary": "ok"},
        ])
        loop, _, executor = make_loop(llm, executor=FakeExecutor(lambda t, a: next(results)))
        res = loop.run(make_task(), goal="g")

        self.assertEqual(res.outcome, "done")
        self.assertIn("FAILED", llm.prompts[1])
        self.assertIn("Could not launch Notepadd", llm.prompts[1])
        self.assertIn("SYSTEM FEEDBACK", llm.prompts[1])
        self.assertEqual(len(executor.calls), 2)

    def test_stop_between_turns(self):
        engine = FakeEngine()

        def handler(tool, args):
            engine.stop_flag = True
            return {"ok": True, "tool": tool, "result": {}}

        llm = FakeLLM([{"plan": ["a"], "actions": [LAUNCH], "status": "continue"}])
        loop, _, _ = make_loop(llm, executor=FakeExecutor(handler), engine=engine)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "stopped")
        self.assertEqual(llm.calls, 1)

    def test_stop_between_actions_of_one_turn(self):
        engine = FakeEngine()

        def handler(tool, args):
            engine.stop_flag = True
            return {"ok": True, "tool": tool, "result": {}}

        llm = FakeLLM([{"plan": ["a"], "actions": [LAUNCH, LAUNCH, LAUNCH],
                        "status": "continue"}])
        loop, _, executor = make_loop(llm, executor=FakeExecutor(handler), engine=engine)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "stopped")
        self.assertEqual(len(executor.calls), 1)

    def test_user_corrections_reach_the_next_prompt(self):
        engine = FakeEngine()

        def handler(tool, args):
            engine.corrections.append("save it as report.txt, not notes.txt")
            return {"ok": True, "tool": tool, "result": {}}

        llm = FakeLLM([
            {"plan": ["a"], "actions": [LAUNCH], "status": "continue"},
            {"plan_update": [{"id": 1, "status": "done"}], "status": "done", "summary": "ok"},
        ])
        loop, _, _ = make_loop(llm, executor=FakeExecutor(handler), engine=engine)
        loop.run(make_task(), goal="g")
        self.assertNotIn("CORRECTION", llm.prompts[0])
        self.assertIn("NEW USER CORRECTION: save it as report.txt", llm.prompts[1])
        self.assertIn("USER CORRECTIONS (highest priority)", llm.prompts[1])

    def test_invalid_json_is_retried(self):
        llm = FakeLLM(["sorry, here you go: not json",
                       {"plan": ["x"], "plan_update": [{"id": 1, "status": "done"}],
                        "status": "done", "summary": "ok"}])
        loop, _, _ = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "done")
        self.assertIn("not a valid JSON", llm.prompts[1])

    def test_three_invalid_replies_fail_the_task(self):
        llm = FakeLLM(["nope", "still nope", "no again"])
        loop, _, _ = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "failed")

    def test_repeating_the_same_action_is_detected(self):
        same = {"plan": ["a"], "actions": [LAUNCH], "status": "continue"}
        llm = FakeLLM([same] * 5)
        loop, _, executor = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "failed")
        self.assertIn("Stuck", res.summary)
        self.assertEqual(len(executor.calls), 5)
        self.assertIn("repeated the same action 3 times", llm.prompts[3])

    def test_five_failures_in_a_row_fail_the_task(self):
        def handler(tool, args):
            return {"ok": False, "tool": tool, "error": "nope"}

        replies = [{"plan": ["a"], "actions": [
            {"tool": "desktop.type_text", "arguments": {"text": f"t{i}"}}],
            "status": "continue"} for i in range(5)]
        loop, _, _ = make_loop(FakeLLM(replies), executor=FakeExecutor(handler))
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "failed")
        self.assertIn("in a row", res.summary)

    def test_max_iterations(self):
        replies = [{"plan": ["a"], "actions": [
            {"tool": "desktop.type_text", "arguments": {"text": f"t{i}"}}],
            "status": "continue"} for i in range(3)]
        loop, _, _ = make_loop(FakeLLM(replies), max_iterations=3)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "max_iterations")
        self.assertIn("1. [ ] a", res.summary)

    def test_no_limit_on_llm_calls_by_default(self):
        # A long task: 60 working turns, then done. Nothing may cut it off.
        replies = [{"plan": ["long job"], "actions": [
            {"tool": "desktop.type_text", "arguments": {"text": f"t{i}"}}],
            "status": "continue"} for i in range(60)]
        replies.append({"plan_update": [{"id": 1, "status": "done"}], "status": "done",
                        "summary": "finished"})
        llm = FakeLLM(replies)
        loop, _, _ = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "done")
        self.assertEqual(llm.calls, 61)
        self.assertEqual(res.iterations, 61)
        self.assertIn("Turn 61.", llm.prompts[-1])      # no "of N" when unlimited
        self.assertNotIn(" of ", llm.prompts[-1].split("Turn 61")[1][:8])

    def test_zero_or_none_means_unlimited_and_a_number_still_caps(self):
        self.assertEqual(make_loop(FakeLLM([]), max_iterations=0)[0].max_iterations, 0)
        self.assertEqual(make_loop(FakeLLM([]), max_iterations=None)[0].max_iterations, 0)
        loop = make_loop(FakeLLM([]), max_iterations=7)[0]
        self.assertEqual(loop.max_iterations, 7)

    def test_same_action_on_a_changing_screen_is_progress_not_stuck(self):
        # e.g. clicking "Next" or scrolling 8 times: same arguments, screen changes each time.
        shots = iter(range(1000))
        replies = [{"plan": ["page through"], "actions": [
            {"tool": "desktop.press", "arguments": {"key": "pagedown"}}],
            "status": "continue"} for _ in range(8)]
        replies.append({"status": "done", "summary": "reached the end",
                        "plan_update": [{"id": 1, "status": "done"}]})
        loop, _, executor = make_loop(
            FakeLLM(replies), observe=lambda: Observation(f"page {next(shots)}"))
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "done", res.summary)
        self.assertEqual(len(executor.calls), 8)

    def test_done_with_pending_items_is_pushed_back_once(self):
        llm = FakeLLM([
            {"plan": ["step a", "step b"], "status": "done", "summary": "too early"},
            {"status": "done", "summary": "really done"},
        ])
        loop, _, _ = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "done")
        self.assertEqual(res.summary, "really done")
        self.assertIn("still pending", llm.prompts[1])
        self.assertIn("step a", llm.prompts[1])

    def test_done_together_with_actions_is_not_final(self):
        llm = FakeLLM([
            {"plan": ["a"], "actions": [LAUNCH], "status": "done", "summary": "premature"},
            {"plan_update": [{"id": 1, "status": "done"}], "status": "done", "summary": "ok"},
        ])
        loop, _, executor = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.summary, "ok")
        self.assertEqual(llm.calls, 2)
        self.assertEqual(len(executor.calls), 1)
        self.assertIn("confirm the result", llm.prompts[1])

    def test_needs_user(self):
        llm = FakeLLM([{"plan": ["login"], "status": "needs_user",
                        "summary": "Password dao"}])
        loop, _, executor = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "needs_user")
        self.assertEqual(res.summary, "Password dao")
        self.assertEqual(executor.calls, [])

    def test_failed_status_runs_no_actions(self):
        llm = FakeLLM([{"actions": [LAUNCH], "status": "failed", "summary": "cannot"}])
        loop, _, executor = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "failed")
        self.assertEqual(executor.calls, [])

    def test_llm_error_fails_cleanly(self):
        llm = FakeLLM([RuntimeError("boom")] * 3)
        loop, _, _ = make_loop(llm)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "failed")
        self.assertIn("boom", res.summary)

    def test_screenshots_are_dropped_when_provider_rejects_images(self):
        llm = RejectsImagesLLM([
            {"plan": ["a"], "actions": [LAUNCH], "status": "continue"},
            {"actions": [{"tool": "desktop.screenshot", "arguments": {}}],
             "status": "continue"},
            {"plan_update": [{"id": 1, "status": "done"}], "status": "done", "summary": "ok"},
        ])
        loop, _, _ = make_loop(llm, use_vision=True,
                               observe=lambda: Observation("w", image_path="shot.png"))
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "done")
        self.assertFalse(loop.use_vision)
        self.assertEqual(llm.image_calls, 2)  # third turn no longer tries images

    def test_observation_failure_does_not_kill_the_loop(self):
        def broken():
            raise RuntimeError("no display")

        llm = FakeLLM([{"status": "done", "summary": "ok"}])
        loop, _, _ = make_loop(llm, observe=broken)
        res = loop.run(make_task(), goal="g")
        self.assertEqual(res.outcome, "done")
        self.assertIn("observation failed", llm.prompts[0])


class TestHelpers(unittest.TestCase):
    def test_extract_json_object(self):
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json_object('Sure! {"a": {"b": 2}} hope it helps'),
                         {"a": {"b": 2}})
        self.assertEqual(extract_json_object('{"a": 1} {"b": 2}'), {"a": 1})
        self.assertIsNone(extract_json_object("no json here"))
        self.assertIsNone(extract_json_object(""))

    def test_format_tools(self):
        out = format_tools(FakeRegistry().list_tools())
        self.assertIn("desktop.launch_application(app_name_or_path: string) - Launch an app", out)
        self.assertIn("monitor_index?: integer", out)

    def test_set_plan_keeps_status_of_unchanged_items(self):
        s = TaskState(goal="g")
        s.set_plan(["a", "b"])
        s.update_statuses([{"id": 1, "status": "done"}])
        s.set_plan(["a", "b", "c"])
        self.assertEqual([c["status"] for c in s.checklist], ["done", "pending", "pending"])


if __name__ == "__main__":
    unittest.main()
