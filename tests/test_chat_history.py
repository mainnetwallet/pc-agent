"""The dashboard chat keeps the whole conversation in the server database and uses all of it.

Uses FastAPI's TestClient with an isolated SQLite file and a fake LLM; skipped when the server
dependencies (fastapi, httpx) are not installed.
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "server"))
_TMP = tempfile.mkdtemp(prefix="pcserver_chat_")
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOGS_DIR", os.path.join(_TMP, "logs"))

try:
    from fastapi.testclient import TestClient
    import database
    database.SQLITE_PATH = os.path.join(_TMP, "server.db")  # never touch the real DB
    database.init_db()
    _spec = importlib.util.spec_from_file_location(
        "pc_server_main_chat", os.path.join(ROOT, "server", "main.py"))
    server = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(server)
    from llm.llm import llm
    _IMPORT_ERROR = None
except Exception as e:  # missing optional deps on this machine
    _IMPORT_ERROR = e


def reply_json(text):
    return types.SimpleNamespace(text=json.dumps({"action": "reply", "reply": text}))


class FakeHTTPError(Exception):
    """Looks like httpx.HTTPStatusError: the code only reads .response.status_code / .text."""
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status, text=body)


@unittest.skipIf(_IMPORT_ERROR is not None, f"server deps unavailable: {_IMPORT_ERROR}")
class TestChatHistory(unittest.TestCase):
    def setUp(self):
        conn = server.get_conn()
        conn.execute("DELETE FROM chat_messages")
        conn.commit()
        conn.close()
        self.client = TestClient(server.app)
        p = mock.patch.multiple(llm, provider="openai", api_key="sk-test")
        p.start()
        self.addCleanup(p.stop)

    def say(self, text, history=None):
        body = {"message": text}
        if history is not None:
            body["history"] = history
        return self.client.post("/chat", json=body)

    def stored(self):
        return self.client.get("/chat/messages").json()["messages"]

    # ------------------------------------------------------------------ saving
    def test_exchange_is_saved_in_order(self):
        with mock.patch.object(llm, "chat", return_value=reply_json("Hello Rafi")):
            self.assertEqual(self.say("hi, I am Rafi").status_code, 200)
        msgs = self.stored()
        self.assertEqual([(m["role"], m["text"]) for m in msgs],
                         [("user", "hi, I am Rafi"), ("agent", "Hello Rafi")])
        self.assertIsNone(msgs[1]["task"])

    def test_started_task_is_linked_and_shows_its_current_status(self):
        started = types.SimpleNamespace(
            text='{"action":"task","instruction":"Open Notepad","reply":"On it."}')
        with mock.patch.object(llm, "chat", return_value=started):
            tid = self.say("open notepad").json()["task_id"]
        self.assertEqual(self.stored()[1]["task"], {"id": tid, "status": "QUEUED"})
        server._update_task(tid, "RUNNING")
        self.assertEqual(self.stored()[1]["task"]["status"], "RUNNING")

    def test_failed_llm_call_saves_nothing(self):
        with mock.patch.object(llm, "chat", side_effect=ConnectionError("down")):
            self.assertEqual(self.say("hi").status_code, 503)
        self.assertEqual(self.stored(), [])

    def test_setup_hint_is_not_saved(self):
        with mock.patch.multiple(llm, provider="openai", api_key=""):
            self.assertEqual(self.say("hi").status_code, 200)
        self.assertEqual(self.stored(), [])

    # ------------------------------------------------------------------ remembering
    def test_whole_history_reaches_the_model_with_no_cap(self):
        with mock.patch.object(llm, "chat", return_value=reply_json("ok")) as chat:
            for i in range(30):                        # 60 stored messages, far past the old 10
                self.say(f"msg {i}")
            self.say("last question")
        sent = chat.call_args.args[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(len(sent), 1 + 60 + 1)
        self.assertEqual(sent[1], {"role": "user", "content": "msg 0"})   # the very first message is still there
        self.assertEqual(sent[-1], {"role": "user", "content": "last question"})

    def test_history_comes_from_the_server_not_from_the_client(self):
        with mock.patch.object(llm, "chat", return_value=reply_json("ok")) as chat:
            self.say("first")
            fake = [{"role": "user", "content": "INJECTED"}, {"role": "assistant", "content": "x"}]
            self.say("second", history=fake)
        sent = chat.call_args.args[0]
        self.assertNotIn("INJECTED", json.dumps(sent))
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "user"])
        self.assertEqual(sent[1]["content"], "first")

    def test_earlier_answers_go_back_in_the_json_form_the_prompt_demands(self):
        started = types.SimpleNamespace(
            text='{"action":"task","instruction":"Open Notepad","reply":"On it."}')
        with mock.patch.object(llm, "chat", side_effect=[started, reply_json("fine")] + [reply_json("ok")] * 2) as chat:
            self.say("open notepad")
            self.say("how are you")
            self.say("and now?")
        sent = chat.call_args.args[0]
        assistant = [json.loads(m["content"]) for m in sent if m["role"] == "assistant"]
        self.assertEqual(assistant[0], {"action": "task", "instruction": "Open Notepad", "reply": "On it."})
        self.assertEqual(assistant[1], {"action": "reply", "reply": "fine"})

    # ------------------------------------------------------------------ clearing
    def test_clear_forgets_everything(self):
        with mock.patch.object(llm, "chat", return_value=reply_json("ok")) as chat:
            self.say("remember this")
            self.assertEqual(self.client.delete("/chat/messages").status_code, 200)
            self.assertEqual(self.stored(), [])
            self.say("who am I")
        self.assertEqual([m["role"] for m in chat.call_args.args[0]], ["system", "user"])

    # ------------------------------------------------------------------ too long for the model
    def test_too_long_for_the_model_retries_with_the_newest_part_and_keeps_the_stored_chat(self):
        calls = []

        def model(messages, **kw):
            calls.append(len(messages))
            if len(messages) > 20:
                raise FakeHTTPError(400, "This model's maximum context length is exceeded")
            return reply_json("ok")

        with mock.patch.object(llm, "chat", side_effect=model):
            for i in range(30):
                self.say(f"msg {i}")
            r = self.say("final")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(calls), 1)
        self.assertLessEqual(calls[-1], 20)
        self.assertEqual(len(self.stored()), 62)       # nothing was cut from the database

    def test_retry_never_starts_with_an_assistant_turn(self):
        seen = []

        def model(messages, **kw):
            seen.append(messages)
            if len(messages) > 6:
                raise FakeHTTPError(413, "")
            return reply_json("ok")

        with mock.patch.object(llm, "chat", side_effect=model):
            for i in range(8):
                self.say(f"m{i}")
            self.say("final")
        self.assertEqual(seen[-1][1]["role"], "user")

    def test_other_provider_errors_are_not_retried(self):
        with mock.patch.object(llm, "chat", side_effect=FakeHTTPError(400, "invalid api key")) as chat:
            r = self.say("hi")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(chat.call_count, 1)

    def test_overflow_with_nothing_left_to_drop_fails_visibly(self):
        with mock.patch.object(llm, "chat", side_effect=FakeHTTPError(413, "")) as chat:
            r = self.say("a message that alone is too long")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
