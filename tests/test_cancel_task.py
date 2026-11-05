"""Cancelling a task that waits for the user must work, even after the agent restarted.

The agent keeps tasks in memory only. A task that asked a question stays NEEDS_USER_ACTION in the
server database after a restart, but the agent no longer knows it: stop and replies used to vanish.
Server tests use FastAPI's TestClient with an isolated SQLite file (skipped without fastapi/httpx).
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "server"))
_TMP = tempfile.mkdtemp(prefix="pccancel_test_")
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOGS_DIR", os.path.join(_TMP, "logs"))

try:
    from fastapi.testclient import TestClient
    import database
    database.SQLITE_PATH = os.path.join(_TMP, "server.db")  # never touch the real DB
    database.init_db()
    _spec = importlib.util.spec_from_file_location("pc_server_main_cancel", os.path.join(ROOT, "server", "main.py"))
    server = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(server)
    _IMPORT_ERROR = None
except Exception as e:  # missing optional deps on this machine
    _IMPORT_ERROR = e

QUESTION = "Which Chrome profile? Reply 1 or -1."


def insert_task(task_id, status="NEEDS_USER_ACTION", result=QUESTION):
    conn = database.get_conn()
    try:
        conn.cursor().execute(
            "INSERT INTO tasks(id, instruction, status, plan, result, error, created_at, updated_at, source, metadata) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task_id, "open chrome 1 and chat", status, None, result, None, time.time(), time.time(), "web", "{}"))
        conn.commit()
    finally:
        conn.close()


@unittest.skipIf(_IMPORT_ERROR is not None, f"server deps unavailable: {_IMPORT_ERROR}")
class TestCancelCommand(unittest.TestCase):
    def test_pure_cancel_commands(self):
        for text in ["task cancel koro", "cancel", "Stop", "stop it", "please cancel this task", "bondho koro",
                     "task batil koro.", "cancel kore den", "বাতিল"]:
            self.assertTrue(server.is_cancel_command(text), text)

    def test_normal_replies_are_not_cancel_commands(self):
        for text in ["use -1", "stop the music", "cancel koro na", "do not cancel", "yes",
                     "open chrome and cancel the download", "", "   ", "profile 1 then stop"]:
            self.assertFalse(server.is_cancel_command(text), text)


@unittest.skipIf(_IMPORT_ERROR is not None, f"server deps unavailable: {_IMPORT_ERROR}")
class TestServerCancel(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.sent = []

        async def fake_send(msg):
            self.sent.append(msg)
            return True
        # TestClient does not start the server loop, so run what the server would schedule right here.
        for p in (mock.patch.object(server, "_send_agent", fake_send),
                  mock.patch.object(server, "_run_async", lambda coro: asyncio.run(coro))):
            p.start()
            self.addCleanup(p.stop)
        self.tid = f"t-{time.time_ns()}"
        insert_task(self.tid)

    def status(self):
        return self.client.get(f"/tasks/{self.tid}").json()["status"]

    def test_stop_cancels_a_task_the_agent_no_longer_knows(self):
        r = self.client.post(f"/tasks/{self.tid}/stop")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.status(), "CANCELLED")
        self.assertTrue(any(m["type"] == "stop" and m["task_id"] == self.tid for m in self.sent))

    def test_typed_cancel_cancels_instead_of_answering(self):
        r = self.client.post(f"/tasks/{self.tid}/message", json={"message": "task cancel koro"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["cancelled"])
        self.assertEqual(self.status(), "CANCELLED")
        self.assertFalse(any(m["type"] == "message" for m in self.sent))

    def test_normal_reply_carries_what_the_agent_needs_to_restore_the_task(self):
        r = self.client.post(f"/tasks/{self.tid}/message", json={"message": "use -1"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json().get("cancelled", False))
        self.assertEqual(self.status(), "NEEDS_USER_ACTION")   # the agent reports the move on
        msg = next(m for m in self.sent if m["type"] == "message")
        self.assertEqual(msg["message"], "use -1")
        self.assertEqual(msg["instruction"], "open chrome 1 and chat")
        self.assertEqual(msg["question"], QUESTION)

    def test_late_agent_update_cannot_undo_a_cancel(self):
        self.client.post(f"/tasks/{self.tid}/stop")
        server._update_task(self.tid, "RUNNING")
        self.assertEqual(self.status(), "CANCELLED")

    def test_delete_removes_the_task_and_stops_it_on_the_agent(self):
        r = self.client.delete(f"/tasks/{self.tid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"/tasks/{self.tid}").status_code, 404)
        ids = [t["id"] for t in self.client.get("/tasks").json()["tasks"]]
        self.assertNotIn(self.tid, ids)
        self.assertTrue(any(m["type"] == "stop" and m["task_id"] == self.tid for m in self.sent))

    def test_delete_a_finished_task(self):
        done = f"done-{time.time_ns()}"
        insert_task(done, status="COMPLETED", result="ok")
        self.assertEqual(self.client.delete(f"/tasks/{done}").status_code, 200)
        self.assertEqual(self.client.get(f"/tasks/{done}").status_code, 404)

    def test_delete_unknown_task_is_404(self):
        self.assertEqual(self.client.delete("/tasks/nope").status_code, 404)

    def test_unknown_task_message_is_404(self):
        self.assertEqual(self.client.post("/tasks/nope/message", json={"message": "x"}).status_code, 404)


@unittest.skipIf(_IMPORT_ERROR is not None, f"server deps unavailable: {_IMPORT_ERROR}")
class TestTaskChatInDatabase(unittest.TestCase):
    """The whole task chat is stored in the server database, not in the browser."""

    def setUp(self):
        self.client = TestClient(server.app)

        async def fake_send(msg):
            return True
        for p in (mock.patch.object(server, "_send_agent", fake_send),
                  mock.patch.object(server, "_run_async", lambda coro: asyncio.run(coro))):
            p.start()
            self.addCleanup(p.stop)
        self.tid = f"chat-{time.time_ns()}"
        insert_task(self.tid)

    def chat(self):
        return [(m["role"], m["text"]) for m in self.client.get(f"/tasks/{self.tid}/messages").json()["messages"]]

    def test_chat_of_an_older_task_starts_with_its_question(self):
        self.assertEqual(self.chat(), [("agent", QUESTION)])

    def test_reply_is_stored_and_the_question_is_not_added_again(self):
        self.client.post(f"/tasks/{self.tid}/message", json={"message": "use -1"})
        self.assertEqual(self.chat(), [("agent", QUESTION), ("user", "use -1")])
        self.assertEqual(self.chat(), [("agent", QUESTION), ("user", "use -1")])   # reading again adds nothing

    def test_chat_survives_a_new_client(self):
        self.client.post(f"/tasks/{self.tid}/message", json={"message": "use -1"})
        again = TestClient(server.app)   # another browser / device
        msgs = again.get(f"/tasks/{self.tid}/messages").json()["messages"]
        self.assertEqual([m["text"] for m in msgs], [QUESTION, "use -1"])
        self.assertTrue(all(m["created_at"] for m in msgs))

    def test_typed_cancel_is_part_of_the_chat(self):
        self.client.post(f"/tasks/{self.tid}/message", json={"message": "task cancel koro"})
        self.assertEqual(self.chat(), [("agent", QUESTION), ("user", "task cancel koro"), ("agent", "Task cancelled.")])

    def test_cancel_button_is_part_of_the_chat(self):
        self.client.post(f"/tasks/{self.tid}/stop")
        self.assertEqual(self.chat(), [("agent", QUESTION), ("agent", "Task cancelled.")])

    def test_task_list_counts_the_users_messages(self):
        def count():
            return next(t for t in self.client.get("/tasks").json()["tasks"] if t["id"] == self.tid)["user_messages"]
        self.assertEqual(count(), 0)
        self.client.post(f"/tasks/{self.tid}/message", json={"message": "use -1"})
        self.assertEqual(count(), 1)

    def test_deleting_the_task_deletes_its_chat(self):
        self.client.post(f"/tasks/{self.tid}/message", json={"message": "use -1"})
        self.client.delete(f"/tasks/{self.tid}")
        self.assertEqual(server._messages(self.tid), [])
        self.assertEqual(self.client.get(f"/tasks/{self.tid}/messages").status_code, 404)

    def test_agent_question_and_result_are_stored_once(self):
        tid = f"agent-{time.time_ns()}"
        insert_task(tid, status="RUNNING", result=None)
        with self.client.websocket_connect("/agent/connect") as ws:
            for _ in range(2):   # a reconnect reports the same status again
                ws.send_json({"type": "task.update", "task_id": tid, "status": "NEEDS_USER_ACTION",
                              "result": QUESTION + "\n\nOBSERVE: x"})
            deadline = time.time() + 3
            while time.time() < deadline and not server._messages(tid):
                time.sleep(0.05)
            time.sleep(0.2)
            self.assertEqual([(m["role"], m["text"]) for m in server._messages(tid)], [("agent", QUESTION)])
            ws.send_json({"type": "task.update", "task_id": tid, "status": "COMPLETED", "result": "Done: chatted.\n\nOBSERVE: y"})
            deadline = time.time() + 3
            while time.time() < deadline and len(server._messages(tid)) < 2:
                time.sleep(0.05)
        self.assertEqual([m["text"] for m in server._messages(tid)], [QUESTION, "Done: chatted."])


class TestRestoreWaiting(unittest.TestCase):
    def setUp(self):
        from tasks import engine as engine_mod
        for name in ("memory", "recovery"):
            p = mock.patch.object(engine_mod, name)
            p.start()
            self.addCleanup(p.stop)
        self.eng = engine_mod.TaskEngine()

    def test_unknown_task_is_rebuilt_with_the_answer_and_queued(self):
        self.assertTrue(self.eng.restore_waiting("abc", "open chrome 1 and chat", QUESTION + "\n\nOBSERVE: x", " use -1 "))
        t = self.eng.get_task("abc")
        self.assertEqual(t["status"], "QUEUED")
        corr = self.eng.get_corrections("abc")
        self.assertEqual(len(corr), 1)
        self.assertIn(QUESTION, corr[0])
        self.assertIn("use -1", corr[0])
        self.assertNotIn("OBSERVE", corr[0])
        self.assertEqual(self.eng.next_queued().id, "abc")

    def test_known_task_is_left_alone(self):
        tid = self.eng.create_task("x")
        self.assertFalse(self.eng.restore_waiting(tid, "x", "", "hello"))

    def test_nothing_to_restore_without_instruction_or_message(self):
        self.assertFalse(self.eng.restore_waiting("abc", "", QUESTION, "yes"))
        self.assertFalse(self.eng.restore_waiting("abc", "x", QUESTION, "  "))
        self.assertIsNone(self.eng.get_task("abc"))


if __name__ == "__main__":
    unittest.main()
