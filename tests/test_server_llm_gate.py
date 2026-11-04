"""Without an LLM provider the server must not create tasks, and chat must say so.

Uses FastAPI's TestClient with an isolated SQLite file; skipped when the server
dependencies (fastapi, httpx) are not installed.
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "server"))
_TMP = tempfile.mkdtemp(prefix="pcserver_test_")
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOGS_DIR", os.path.join(_TMP, "logs"))

try:
    from fastapi.testclient import TestClient
    import database
    database.SQLITE_PATH = os.path.join(_TMP, "server.db")  # never touch the real DB
    database.init_db()
    _spec = importlib.util.spec_from_file_location(
        "pc_server_main", os.path.join(ROOT, "server", "main.py"))
    server = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(server)
    from llm.llm import llm, SETUP_HINT
    _IMPORT_ERROR = None
except Exception as e:  # missing optional deps on this machine
    _IMPORT_ERROR = e


@unittest.skipIf(_IMPORT_ERROR is not None, f"server deps unavailable: {_IMPORT_ERROR}")
class TestServerLLMGate(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def no_llm(self):
        return mock.patch.multiple(llm, provider="openai", api_key="")

    def with_llm(self):
        return mock.patch.multiple(llm, provider="openai", api_key="sk-test")

    def task_count(self):
        return len(self.client.get("/tasks").json()["tasks"])

    def test_task_is_not_created_without_a_provider(self):
        before = self.task_count()
        with self.no_llm():
            r = self.client.post("/tasks", json={"instruction": "open notepad"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], SETUP_HINT)
        self.assertEqual(self.task_count(), before)

    def test_chat_tells_the_user_to_add_a_provider(self):
        before = self.task_count()
        with self.no_llm():
            r = self.client.post("/chat", json={"message": "open notepad", "history": []})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["reply"], SETUP_HINT)
        self.assertIsNone(body["task_id"])
        self.assertEqual(self.task_count(), before)

    def test_mock_provider_counts_as_not_configured(self):
        with mock.patch.multiple(llm, provider="mock", api_key="sk-test"):
            r = self.client.post("/tasks", json={"instruction": "open notepad"})
        self.assertEqual(r.status_code, 409)

    def test_task_is_created_when_a_provider_is_configured(self):
        with self.with_llm():
            r = self.client.post("/tasks", json={"instruction": "open notepad"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "QUEUED")

    def test_chat_still_creates_a_task_when_the_llm_asks_for_one(self):
        reply = types.SimpleNamespace(
            text='{"action":"task","instruction":"Open Notepad","reply":"On it."}')
        with self.with_llm(), mock.patch.object(llm, "chat", return_value=reply):
            r = self.client.post("/chat", json={"message": "open notepad", "history": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["reply"], "On it.")
        self.assertTrue(r.json()["task_id"])

    def test_provider_failure_is_reported_not_guessed(self):
        with self.with_llm(), mock.patch.object(llm, "chat", side_effect=ConnectionError("down")):
            r = self.client.post("/chat", json={"message": "hi", "history": []})
        self.assertEqual(r.status_code, 503)
        self.assertIn("down", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
