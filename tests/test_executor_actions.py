"""Tests for the executor / task-engine fixes that the reasoning loop relies on.

Uses the real tool registry, task engine and SQLite memory (in a temp dir). Only the
OS-level control libraries are stubbed when they cannot be imported (non-Windows), so
these also run in CI/Linux. On a real Windows install the real modules are used.
"""

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

# Isolated data dir: never touch the real agent.db. Must be set before config is imported.
_TMP = tempfile.mkdtemp(prefix="pcagent_test_")
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOGS_DIR", os.path.join(_TMP, "logs"))


def _install_control_stubs():
    try:
        import control.desktop  # noqa: F401
        import screen.capture  # noqa: F401
        return  # real libraries are available (Windows)
    except Exception:
        pass
    for name in list(sys.modules):
        if name.split(".")[0] in ("control", "screen"):
            del sys.modules[name]

    agent_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent")

    def pkg(name):
        m = types.ModuleType(name)
        # "screen" keeps its real folder on the path so dependency-free submodules such as
        # screen.settle (Pillow only) import for real; screen.capture is mocked below.
        m.__path__ = [os.path.join(agent_dir, name)] if name == "screen" else []
        sys.modules[name] = m

    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m

    for p in ("control", "screen"):
        pkg(p)
    mod("control.desktop", DesktopControl=mock.MagicMock)
    mod("control.windows", windows=mock.MagicMock(), discover_installed_apps=mock.MagicMock())
    mod("control.filesystem", fs=mock.MagicMock())
    mod("control.system", system=mock.MagicMock())
    mod("control.terminal", terminal=mock.MagicMock())
    mod("screen.capture", capture_engine=mock.MagicMock())


_install_control_stubs()

from core.executor import Executor, executor  # noqa: E402
from discovery.registry import registry  # noqa: E402
from tasks.engine import task_engine  # noqa: E402
from memory.sqlite_store import memory  # noqa: E402


def _schema(props, required):
    return {"type": "object", "properties": props, "required": required}


class TestExecuteAction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.register(
            "desktop.fake_type", lambda text: {"ok": True, "echo": text}, "type text",
            input_schema=_schema({"text": {"type": "string"}}, ["text"]))
        registry.register(
            "terminal.fake_run",
            lambda command, shell="powershell": {"ok": True, "command": command}, "run",
            input_schema=_schema({"command": {"type": "string"}, "shell": {"type": "string"}},
                                 ["command"]))
        registry.register(
            "filesystem.fake_copy", lambda src, dst: {"ok": True, "src": src, "dst": dst}, "copy",
            input_schema=_schema({"src": {"type": "string"}, "dst": {"type": "string"}},
                                 ["src", "dst"]))
        registry.register(
            "filesystem.fake_fail", lambda path: {"ok": False, "error": "disk full"}, "fails",
            input_schema=_schema({"path": {"type": "string"}}, ["path"]))
        registry.register(
            "desktop.fake_launch", lambda app_name_or_path: {"ok": True, "app": app_name_or_path},
            "launch", input_schema=_schema({"app_name_or_path": {"type": "string"}},
                                           ["app_name_or_path"]))

        def boom():
            raise RuntimeError("kaboom")
        registry.register("test.fake_raise", boom, "raises", input_schema=_schema({}, []))

    def setUp(self):
        self.tid = task_engine.create_task("test instruction")
        self.task = types.SimpleNamespace(id=self.tid, instruction="test instruction")

    def run_action(self, tool, args):
        return executor.execute_action(tool, args, self.task, task_engine)

    def test_free_text_is_never_rewritten_as_a_path(self):
        text = "C:/notes/a.txt and b/c.pdf"
        res = self.run_action("desktop.fake_type", {"text": text})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"]["echo"], text)

    def test_real_argument_names_are_not_renamed(self):
        # Used to be renamed command -> app_name_or_path, so terminal commands never ran.
        res = self.run_action("terminal.fake_run", {"command": "dir"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"]["command"], "dir")

    def test_aliases_map_to_the_names_the_tool_expects(self):
        res = self.run_action("filesystem.fake_copy", {"source_path": "src1", "destination": "dst1"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"], {"ok": True, "src": "src1", "dst": "dst1"})

    def test_unknown_tool_is_reported(self):
        res = self.run_action("does.not_exist", {})
        self.assertFalse(res["ok"])
        self.assertIn("Unknown tool", res["error"])

    def test_missing_required_argument_is_not_guessed(self):
        # A missing argument must go back to the LLM, never be defaulted (e.g. to "Notepad").
        res = self.run_action("desktop.fake_launch", {})
        self.assertFalse(res["ok"])
        self.assertIn("app_name_or_path", res["error"])

    def test_tool_reported_failure_is_a_failure(self):
        res = self.run_action("filesystem.fake_fail", {"path": "p1"})
        self.assertFalse(res["ok"])
        self.assertIn("disk full", res["error"])

    def test_exception_inside_a_tool_is_a_failure(self):
        res = self.run_action("test.fake_raise", {})
        self.assertFalse(res["ok"])
        self.assertIn("kaboom", res["error"])

    def test_desktop_lock_is_released_after_each_action(self):
        for _ in range(2):  # a leaked lock would block the second call for 10 s and fail it
            res = self.run_action("desktop.fake_type", {"text": "hi"})
            self.assertTrue(res["ok"], res)
        self.assertIsNone(task_engine.desktop_lock_holder())


class TestEngineControl(unittest.TestCase):
    def test_stop_is_visible_after_cleanup_popped_the_event(self):
        tid = task_engine.create_task("to stop")
        task_engine.mark_running(tid)
        self.assertFalse(task_engine.stop_requested(tid))
        self.assertTrue(task_engine.stop(tid))  # stop() pops the stop event in cleanup
        self.assertTrue(task_engine.stop_requested(tid))

    def test_wait_if_paused_returns_true_when_stopped_while_paused(self):
        tid = task_engine.create_task("pause then stop")
        task_engine.mark_running(tid)
        task_engine.pause(tid)
        threading.Timer(0.2, task_engine.stop, args=(tid,)).start()
        self.assertTrue(task_engine.wait_if_paused(tid, poll=0.05))

    def test_wait_if_paused_returns_false_when_running(self):
        tid = task_engine.create_task("running")
        task_engine.mark_running(tid)
        self.assertFalse(task_engine.wait_if_paused(tid, poll=0.05))
        task_engine.complete(tid, "done")

    def test_corrections_roundtrip(self):
        tid = task_engine.create_task("corrections")
        self.assertEqual(task_engine.get_corrections(tid), [])
        task_engine.inject_message(tid, "use report.txt")
        self.assertEqual(task_engine.get_corrections(tid), ["use report.txt"])

    def test_update_progress_publishes_plan_and_state(self):
        tid = task_engine.create_task("progress")
        plan = {"interpreted_goal": "g", "execution_steps": [{"step": 1, "action": "a",
                                                               "status": "done"}]}
        task_engine.update_progress(tid, plan=plan, state={"goal": "g", "iteration": 2})
        t = task_engine.get_task(tid)
        self.assertEqual(t["plan"], plan)
        self.assertEqual(t["metadata"]["state"]["iteration"], 2)
        self.assertEqual(task_engine.get_checkpoints(tid)[-1]["state"]["iteration"], 2)

    def test_needs_user_status(self):
        tid = task_engine.create_task("needs input")
        task_engine.mark_running(tid)
        task_engine.needs_user(tid, "Password dao")
        t = task_engine.get_task(tid)
        self.assertEqual(t["status"], "NEEDS_USER_ACTION")
        self.assertEqual(t["result"], "Password dao")

    def test_stale_desktop_lock_release_is_harmless(self):
        a = task_engine.create_task("lock a")
        b = task_engine.create_task("lock b")
        task_engine.mark_running(a)
        task_engine.mark_running(b)
        self.assertTrue(task_engine.acquire_desktop_lock(a))
        task_engine.stop(a)  # cleanup frees a's lock
        self.assertTrue(task_engine.acquire_desktop_lock(b, timeout=1.0))
        task_engine.release_desktop_lock(a)  # a's executor releasing late: must be a no-op
        self.assertEqual(task_engine.desktop_lock_holder(), b)
        self.assertFalse(task_engine.acquire_desktop_lock("someone-else", timeout=0.1))
        task_engine.release_desktop_lock(b)
        task_engine.complete(b, "done")


class _ScriptedLLM:
    available = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat_strict(self, messages, images=None, **kw):
        self.calls += 1
        return types.SimpleNamespace(text=json.dumps(self.replies.pop(0)))


def _fake_desktop():
    d = mock.MagicMock()
    d.screenshot.return_value = ("shot.png", None)
    d.get_active_window.return_value = types.SimpleNamespace(title="Notepad")
    d.get_all_windows.return_value = [types.SimpleNamespace(title="Notepad")]
    d.get_monitors.return_value = [object()]
    d.ocr.return_value = ""
    return d


class TestAgentRunTask(unittest.TestCase):
    """Agent.run_task end to end with a scripted LLM and fake tools."""

    @classmethod
    def setUpClass(cls):
        import core.agent as agent_module
        cls.agent_module = agent_module
        cls.state = {"tid": None, "launch": lambda app: {"ok": True, "launched": app}}
        # desktop.click present => Agent._ensure_tools does not register the real adapters
        registry.register("desktop.click", lambda x, y: {"ok": True}, "click",
                          input_schema=_schema({"x": {"type": "integer"},
                                                "y": {"type": "integer"}}, ["x", "y"]))
        registry.register("desktop.fake_launch", lambda app_name_or_path: {"ok": True,
                                                                           "app": app_name_or_path},
                          "launch", input_schema=_schema({"app_name_or_path": {"type": "string"}},
                                                         ["app_name_or_path"]))
        registry.register("desktop.fake_stop",
                          lambda: (task_engine.stop(cls.state["tid"]), {"ok": True})[1],
                          "stops its own task", input_schema=_schema({}, []))

    def setUp(self):
        self.agent = self.agent_module.agent
        self.agent.desktop = _fake_desktop()

    def run_task(self, instruction, llm_obj):
        tid = task_engine.create_task(instruction)
        self.state["tid"] = tid
        task = task_engine._tasks[tid]
        task_engine.mark_running(tid)
        with mock.patch.object(self.agent_module, "llm", llm_obj):
            text = self.agent.run_task(task, task_engine)
        return tid, text

    def test_loop_mode_completes_and_records_progress(self):
        llm = _ScriptedLLM([
            {"plan": ["open notepad"],
             "actions": [{"tool": "desktop.fake_launch",
                          "arguments": {"app_name_or_path": "Notepad"}}],
             "status": "continue"},
            {"plan_update": [{"id": 1, "status": "done"}], "status": "done",
             "summary": "Notepad khola hoyeche"},
        ])
        tid, text = self.run_task("open notepad", llm)
        t = task_engine.get_task(tid)

        self.assertEqual(t["status"], "COMPLETED")
        self.assertTrue(t["result"].startswith("Notepad khola hoyeche"))
        self.assertIn("ACT: 2 reasoning turns, 1 actions (0 failed)", text)
        self.assertIn("VERIFY: PASS", text)
        self.assertEqual(llm.calls, 2)
        self.assertEqual(t["plan"]["execution_steps"][0]["status"], "done")
        steps = memory.get_task_steps(tid)
        self.assertEqual([s["status"] for s in steps], ["COMPLETED"])  # step row really updated
        # Cross-task memory: the next task's context can see this one.
        convo = [c["content"] for c in memory.get_recent_conversation(10)]
        self.assertIn("open notepad", convo)
        self.assertTrue(any(c.startswith("[done] Notepad khola hoyeche") for c in convo))

    def test_stopped_task_stays_cancelled(self):
        llm = _ScriptedLLM([{"plan": ["a"], "actions": [{"tool": "desktop.fake_stop",
                                                          "arguments": {}}],
                             "status": "continue"}])
        tid, text = self.run_task("do something", llm)
        self.assertEqual(task_engine.get_task(tid)["status"], "CANCELLED")
        self.assertIn("STOPPED", text)
        self.assertEqual(llm.calls, 1)

    def test_failed_status_marks_task_failed(self):
        llm = _ScriptedLLM([{"plan": ["a"], "status": "failed", "summary": "App nai"}])
        tid, _ = self.run_task("open nothing", llm)
        t = task_engine.get_task(tid)
        self.assertEqual(t["status"], "FAILED")
        self.assertIn("App nai", t["error"])

    def test_needs_user_sets_status(self):
        llm = _ScriptedLLM([{"plan": ["login"], "status": "needs_user",
                             "summary": "Password dao"}])
        tid, _ = self.run_task("login somewhere", llm)
        t = task_engine.get_task(tid)
        self.assertEqual(t["status"], "NEEDS_USER_ACTION")
        self.assertTrue(t["result"].startswith("Password dao"))

    def test_without_llm_the_task_fails_with_a_clear_error_and_runs_nothing(self):
        # No rule-based planner: with no LLM nothing may be guessed or executed.
        tid, text = self.run_task("open notepad", types.SimpleNamespace(available=False))
        t = task_engine.get_task(tid)
        self.assertEqual(t["status"], "FAILED")
        self.assertIn("LLM not configured", t["error"])
        self.assertIn("LLM not configured", text)
        self.assertEqual(memory.get_task_steps(tid), [])


class TestWaitTools(unittest.TestCase):
    """wait.seconds / wait.until_screen_stable: registered, lock-free, tolerant of LLM input."""

    @classmethod
    def setUpClass(cls):
        from discovery.adapters import WaitAdapter
        cls.adapter = WaitAdapter()
        cls.names = cls.adapter.register()

    def test_registered_and_not_desktop_tools(self):
        # "desktop.*" tools hold the exclusive input lock while they run; a long wait must not.
        self.assertEqual(sorted(self.names), ["wait.seconds", "wait.until_screen_stable"])
        self.assertTrue(all(not n.startswith("desktop.") for n in self.names))
        for n in self.names:
            self.assertTrue(registry.has_tool(n))

    def test_wait_seconds_is_clamped_and_tolerates_bad_input(self):
        with mock.patch("discovery.adapters.time.sleep") as sleep:
            r = registry.execute("wait.seconds", {"seconds": 9999})
            self.assertTrue(r["ok"])
            self.assertEqual(r["result"]["waited"], 60.0)
            sleep.assert_called_with(60.0)
            r = registry.execute("wait.seconds", {"seconds": "five"})
            self.assertEqual(r["result"]["waited"], 3.0)
        self.assertFalse(registry.execute("wait.seconds", {})["ok"])  # required argument

    def test_until_screen_stable_reports_stable_and_still_changing(self):
        with mock.patch("discovery.adapters.wait_until_stable",
                        return_value={"stable": True, "waited": 4.0, "changes": 3}) as w:
            r = registry.execute("wait.until_screen_stable",
                                 {"timeout_seconds": "9999", "quiet_seconds": None})
            self.assertTrue(r["ok"])
            self.assertTrue(r["result"]["stable"])
            self.assertIn("finished", r["result"]["message"])
            self.assertEqual(w.call_args.kwargs["timeout"], 120.0)   # clamped
            self.assertEqual(w.call_args.kwargs["quiet_for"], 3.0)   # default for junk
        with mock.patch("discovery.adapters.wait_until_stable",
                        return_value={"stable": False, "waited": 30.0, "changes": 40}):
            r = registry.execute("wait.until_screen_stable", {})
            # Still-changing is information for the model, not a tool failure.
            self.assertTrue(r["ok"])
            self.assertFalse(r["result"]["stable"])
            self.assertIn("STILL changing", r["result"]["message"])


if __name__ == "__main__":
    unittest.main()
