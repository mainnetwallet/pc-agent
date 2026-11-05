"""After a restart, tasks that were running must come back PAUSED and never run on their own.

Covers both halves: the agent's task engine (memory-only state, restored from SQLite) and the
relay server (task rows left RUNNING in its database).
"""
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT =os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))

from tasks import engine as engine_mod  # noqa: E402
from tasks.engine import TaskEngine, TaskStatus  # noqa: E402


def _row(tid, status, instruction="do the thing", plan=None):
    return {"id": tid, "instruction": instruction, "status": status, "plan": plan,
            "result": None, "error": None,
            "created_at": "2026-11-05T06:00:00+00:00", "updated_at": "2026-11-05T06:01:00+00:00",
            "completed_at": None}


class TestEngineRestoresPaused(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.memory = mock.MagicMock()
        self.memory.list_tasks.side_effect = lambda status=None, limit=100: [
            r for r in self.rows if status is None or r["status"] == status]
        for target, repl in (("memory", self.memory), ("recovery", mock.MagicMock())):
            p = mock.patch.object(engine_mod, target, repl)
            p.start()
            self.addCleanup(p.stop)
        self.eng = TaskEngine()

    def test_in_flight_tasks_come_back_paused(self):
        self.rows = [_row("a", "RUNNING"), _row("b", "PLANNING"), _row("c", "RECOVERING"),
                     _row("d", "WAITING")]
        restored = self.eng.restore_interrupted()
        self.assertCountEqual(restored, ["a", "b", "c", "d"])
        for tid in restored:
            self.assertEqual(self.eng.get_task(tid)["status"], "WAITING")
        # Paused is saved back, so SQLite no longer says RUNNING.
        saved = {c.args[0]: c.args[2] for c in self.memory.save_task.call_args_list}
        self.assertEqual(saved, {"a": "WAITING", "b": "WAITING", "c": "WAITING", "d": "WAITING"})

    def test_finished_and_queued_tasks_are_left_alone(self):
        self.rows = [_row("x", "COMPLETED"), _row("y", "FAILED"), _row("z", "CANCELLED"),
                     _row("q", "QUEUED"), _row("n", "NEEDS_USER_ACTION")]
        self.assertEqual(self.eng.restore_interrupted(), [])
        self.assertEqual(self.eng.list_tasks(), [])

    def test_restored_tasks_do_not_run_on_their_own(self):
        self.rows = [_row("a", "RUNNING")]
        self.eng.restore_interrupted()
        self.assertIsNone(self.eng.next_queued())          # nothing for the worker to pick up
        self.assertEqual(self.eng.list_tasks("QUEUED"), [])
        self.assertEqual(self.eng.get_task("a")["status"], "WAITING")

    def test_plan_and_creation_time_survive(self):
        self.rows = [_row("a", "RUNNING", plan='{"steps": ["one", "two"]}')]
        self.eng.restore_interrupted()
        t = self.eng.get_task("a")
        self.assertEqual(t["plan"], {"steps": ["one", "two"]})
        expected = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(t["created_at"], expected, delta=1)

    def test_restoring_twice_does_not_duplicate(self):
        self.rows = [_row("a", "RUNNING")]
        self.assertEqual(self.eng.restore_interrupted(), ["a"])
        self.assertEqual(self.eng.restore_interrupted(), [])
        self.assertEqual(len(self.eng.list_tasks()), 1)

    def test_resume_queues_restored_task_and_worker_can_start_it(self):
        self.rows = [_row("a", "RUNNING")]
        self.eng.restore_interrupted()
        self.assertTrue(self.eng.resume("a"))
        self.assertEqual(self.eng.get_task("a")["status"], "QUEUED")
        task = self.eng.next_queued()
        self.assertIsNotNone(task)
        self.assertEqual(task.id, "a")
        self.assertTrue(self.eng.mark_running("a"))
        self.assertEqual(self.eng.get_task("a")["status"], "RUNNING")

    def test_resume_of_a_live_paused_task_still_just_unpauses(self):
        tid = self.eng.create_task("live task")
        self.eng.next_queued()
        self.assertTrue(self.eng.mark_running(tid))
        self.assertTrue(self.eng.pause(tid))
        self.assertEqual(self.eng.get_task(tid)["status"], "WAITING")
        self.assertTrue(self.eng.resume(tid))
        self.assertEqual(self.eng.get_task(tid)["status"], "RUNNING")   # not re-queued
        self.assertIsNone(self.eng.next_queued())

    def test_restored_task_can_be_stopped(self):
        self.rows = [_row("a", "RUNNING")]
        self.eng.restore_interrupted()
        self.assertTrue(self.eng.stop("a"))
        self.assertEqual(self.eng.get_task("a")["status"], "CANCELLED")
        self.assertFalse(self.eng.resume("a"))


# ---------------------------------------------------------------------------- relay server
_TMP = tempfile.mkdtemp(prefix="pcserver_restart_")
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOGS_DIR", os.path.join(_TMP, "logs"))
sys.path.insert(0, os.path.join(ROOT, "server"))
try:
    import database
    database.SQLITE_PATH = os.path.join(_TMP, "server.db")  # never touch the real DB
    database.init_db()
    _spec = importlib.util.spec_from_file_location(
        "pc_server_main_restart", os.path.join(ROOT, "server", "main.py"))
    server = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(server)
    _IMPORT_ERROR = None
except Exception as e:  # server dependencies (fastapi...) not installed here
    _IMPORT_ERROR = e


@unittest.skipIf(_IMPORT_ERROR is not None, f"server deps unavailable: {_IMPORT_ERROR}")
class TestServerPausesOnStartup(unittest.TestCase):
    def setUp(self):
        conn = server.get_conn()
        conn.execute("DELETE FROM tasks")
        now = time.time()
        for tid, status in [("run", "RUNNING"), ("plan", "PLANNING"), ("rec", "RECOVERING"),
                            ("disp", "DISPATCHED"), ("wait", "WAITING"), ("queued", "QUEUED"),
                            ("done", "COMPLETED"), ("failed", "FAILED"), ("cancel", "CANCELLED"),
                            ("ask", "NEEDS_USER_ACTION")]:
            conn.execute("INSERT INTO tasks(id, instruction, status, created_at, updated_at) "
                         "VALUES(?,?,?,?,?)", (tid, f"task {tid}", status, now, now))
        conn.commit()
        conn.close()

    def statuses(self):
        conn = server.get_conn()
        try:
            return dict(conn.execute("SELECT id, status FROM tasks").fetchall())
        finally:
            conn.close()

    def test_in_flight_tasks_are_paused_everything_else_untouched(self):
        self.assertEqual(server._pause_interrupted_tasks(), 4)
        s = self.statuses()
        for tid in ("run", "plan", "rec", "disp", "wait"):
            self.assertEqual(s[tid], "WAITING", tid)
        self.assertEqual(s["queued"], "QUEUED")
        self.assertEqual(s["done"], "COMPLETED")
        self.assertEqual(s["failed"], "FAILED")
        self.assertEqual(s["cancel"], "CANCELLED")
        self.assertEqual(s["ask"], "NEEDS_USER_ACTION")

    def test_second_start_changes_nothing(self):
        server._pause_interrupted_tasks()
        self.assertEqual(server._pause_interrupted_tasks(), 0)

    def test_paused_tasks_are_never_redispatched(self):
        server._pause_interrupted_tasks()
        queued = [tid for tid, _ in server._queued_tasks()]
        self.assertEqual(queued, ["queued"])

    def test_waiting_ids_are_listed_for_the_agent_pause(self):
        server._pause_interrupted_tasks()
        self.assertCountEqual(server._task_ids_with_status("WAITING"),
                              ["run", "plan", "rec", "disp", "wait"])


if __name__ == "__main__":
    unittest.main()
