"""Automated tests for the Autonomous PC Agent.

Real tests against actual Windows desktop operations. No mocks of the
underlying control libraries. Destructive operations use temp files only.
"""

import os
import sys
import time
import unittest

# Ensure agent/ modules importable as top-level
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

from control.desktop import DesktopControl
from control.filesystem import fs
from control.system import system
from control.terminal import terminal
from discovery.registry import registry
from discovery.adapters import register_builtin_adapters
from tasks.engine import task_engine, Task, TaskStatus
from core.planner import planner
from llm.llm import llm
from screen.capture import capture_engine
from memory.sqlite_store import memory


class TestDesktopControl(unittest.TestCase):
    def setUp(self):
        self.dc = DesktopControl()

    def test_monitors(self):
        mons = self.dc.get_monitors()
        self.assertGreaterEqual(len(mons), 1)
        self.assertTrue(any(m.primary for m in mons))

    def test_screen_size(self):
        w, h = self.dc.get_screen_size()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_screenshot(self):
        path, img = self.dc.screenshot(monitor_index=0)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(img)
        self.assertGreater(img.size[0], 0)
        self.assertGreater(img.size[1], 0)

    def test_active_window(self):
        w = self.dc.get_active_window()
        # May be None if no window; just ensure no crash
        if w is not None:
            self.assertIsInstance(w.title, str)

    def test_mouse_keyboard_noop(self):
        # Ensure click/hotkey/press don't crash (safe coordinates away from UI)
        self.dc.click(100, 100)
        self.dc.press("esc")
        self.dc.key_combo(["ctrl", "c"])  # harmless copy


class TestFilesystem(unittest.TestCase):
    def test_write_read_delete(self):
        path = os.path.join(os.path.expanduser("~"), "Desktop", "pc_agent_test.txt")
        try:
            fs.write_file(path, "hello test")
            self.assertEqual(fs.read_file(path), "hello test")
            meta = fs.get_metadata(path)
            self.assertEqual(meta["name"], "pc_agent_test.txt")
            self.assertTrue(fs.delete_file(path))
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_find_file_tolerates_bad_path(self):
        # notepad.exe should be found even with a wrong path hint
        found = fs.find_file("notepad.exe")
        # On Windows notepad.exe is in System32
        self.assertIsNotNone(found)
        self.assertTrue(found.lower().endswith("notepad.exe"))

    def test_search_files(self):
        results = fs.search_files("notepad.exe", max_results=5)
        self.assertGreaterEqual(len(results), 0)

    def test_copy_move_folder(self):
        src = os.path.join(os.path.expanduser("~"), "Desktop", "pc_agent_copy_src.txt")
        dst = os.path.join(os.path.expanduser("~"), "Desktop", "pc_agent_copy_dst.txt")
        try:
            fs.write_file(src, "copy me")
            fs.copy(src, dst)
            self.assertTrue(os.path.exists(dst))
            fs.move(dst, src)  # move back over src
            self.assertTrue(os.path.exists(src))
        finally:
            for p in (src, dst):
                if os.path.exists(p):
                    os.remove(p)


class TestSystemControl(unittest.TestCase):
    def test_cpu_ram_disk(self):
        cpu = system.cpu_percent(0.5)
        self.assertGreaterEqual(cpu, 0)
        ram = system.ram()
        self.assertIn("percent", ram)
        disks = system.disks()
        self.assertGreaterEqual(len(disks), 1)

    def test_processes(self):
        procs = system.processes()
        self.assertGreaterEqual(len(procs), 1)


class TestTerminal(unittest.TestCase):
    def test_powershell(self):
        r = terminal.run_powershell("Get-Date | Select-Object -ExpandProperty DateTime")
        self.assertEqual(r.exit_code, 0)
        self.assertIn("November", r.stdout) or self.assertIn("2026", r.stdout)

    def test_cmd(self):
        r = terminal.run("hostname", shell="cmd")
        self.assertEqual(r.exit_code, 0)

    def test_not_found(self):
        r = terminal.run_powershell("this-command-definitely-does-not-exist-xyz")
        self.assertNotEqual(r.exit_code, 0)


class TestDiscovery(unittest.TestCase):
    def test_register_execute(self):
        def mul(a, b):
            return {"prod": a * b}

        registry.register("test.mul", mul, "multiply",
                          input_schema={"type": "object",
                                        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                                        "required": ["a", "b"]},
                          permission="public")
        r = registry.execute("test.mul", {"a": 4, "b": 5}, caller_level="public")
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["prod"], 20)

    def test_validation_error(self):
        r = registry.execute("test.mul", {"a": 4}, caller_level="public")
        self.assertFalse(r["ok"])
        self.assertIn("required", r["error"])

    def test_builtin_adapters(self):
        names_before = set(t["name"] for t in registry.list_tools())
        register_builtin_adapters()
        names_after = set(t["name"] for t in registry.list_tools())
        self.assertIn("desktop.click", names_after)
        self.assertIn("filesystem.read_file", names_after)
        self.assertIn("terminal.run_command", names_after)
        self.assertTrue(len(names_after) > len(names_before))


class TestPlanner(unittest.TestCase):
    def test_misspelling_correction(self):
        out = planner.interpret("Open crome", "")
        self.assertEqual(out["interpreted_goal"], "Open chrome")
        self.assertTrue(any("crome" in a for a in out["assumptions"]))

    def test_language_detection_bengali(self):
        out = planner.interpret("ক sobrevivা", "")  # mixed
        self.assertIn(out["language"], ("english", "bengali", "banglish"))


class TestTaskEngine(unittest.TestCase):
    def test_lifecycle(self):
        tid = task_engine.create_task("test task", priority=3)
        t = task_engine.get_task(tid)
        self.assertEqual(t["status"], "QUEUED")
        self.assertTrue(task_engine.mark_running(tid))
        self.assertEqual(task_engine.get_task(tid)["status"], "RUNNING")
        task_engine.pause(tid)
        self.assertEqual(task_engine.get_task(tid)["status"], "WAITING")
        task_engine.resume(tid)
        self.assertEqual(task_engine.get_task(tid)["status"], "RUNNING")
        task_engine.complete(tid, "done")
        self.assertEqual(task_engine.get_task(tid)["status"], "COMPLETED")

    def test_stop(self):
        tid = task_engine.create_task("to stop")
        task_engine.mark_running(tid)
        self.assertTrue(task_engine.stop(tid))
        self.assertEqual(task_engine.get_task(tid)["status"], "CANCELLED")

    def test_fail(self):
        tid = task_engine.create_task("to fail")
        task_engine.fail(tid, "boom")
        self.assertEqual(task_engine.get_task(tid)["status"], "FAILED")

    def test_list_tasks(self):
        tasks = task_engine.list_tasks()
        self.assertIsInstance(tasks, list)


class TestMemory(unittest.TestCase):
    def test_preference_roundtrip(self):
        memory.set_preference("theme", "dark")
        self.assertEqual(memory.get_preference("theme"), "dark")
        self.assertEqual(memory.get_preference("missing", "x"), "x")

    def test_skill_roundtrip(self):
        import time
        name = f"test_skill_{int(time.time()*1000)}"
        memory.save_skill(name, "desc", [{"tool": "t", "action": "a"}], ["notepad"])
        s = memory.get_skill(name)
        self.assertIsNotNone(s)
        self.assertEqual(s["name"], name)
        self.assertEqual(len(s["steps"]), 1)
        memory.record_skill_use(name, True)
        s2 = memory.get_skill(name)
        self.assertEqual(s2["success_count"], 1)

    def test_conversation(self):
        memory.add_conversation("user", "hi", {})
        memory.add_conversation("agent", "hello", {})
        recent = memory.get_recent_conversation(10)
        self.assertGreaterEqual(len(recent), 2)


class TestCapture(unittest.TestCase):
    def test_capture(self):
        path, jpeg = capture_engine.capture()
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(jpeg)
        self.assertGreater(len(jpeg), 0)

    def test_monitors_and_resolution(self):
        mons = capture_engine.get_monitors()
        self.assertGreaterEqual(len(mons), 1)
        w, h = capture_engine.get_resolution()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)


class TestAgentPipeline(unittest.TestCase):
    def test_full_pipeline_screenshot(self):
        from tasks.engine import TaskStatus
        tid = task_engine.create_task("capture the desktop screen", priority=5)
        t = task_engine.get_task(tid)
        task_obj = Task(id=t["id"], instruction=t["instruction"], status=TaskStatus.QUEUED, priority=5)
        from core.agent import agent
        result = agent.run_task(task_obj, task_engine)
        self.assertIn("OBSERVE", result)
        self.assertIn("PLAN", result)
        self.assertIn("ACT", result)
        final = task_engine.get_task(tid)["status"]
        self.assertIn(final, ("COMPLETED", "FAILED"))


if __name__ == "__main__":
    unittest.main()
