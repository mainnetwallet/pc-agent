"""The agent is LLM-only: no rule-based planner, no mock/fallback mode.

Runs on any OS (no desktop, no network).
"""

import importlib.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
_TMP = tempfile.mkdtemp(prefix="pcagent_test_")
os.environ.setdefault("DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("LOGS_DIR", os.path.join(_TMP, "logs"))

from llm.llm import LLM  # noqa: E402


def _llm(provider, api_key):
    m = LLM()
    m.provider, m.api_key = provider, api_key
    return m


class TestLLMOnly(unittest.TestCase):
    def test_rule_based_planner_module_is_gone(self):
        self.assertIsNone(importlib.util.find_spec("core.planner"))

    def test_llm_has_no_fallback_or_rule_based_methods(self):
        m = LLM()
        for name in ("plan", "_rule_based_plan", "_fallback", "_fallback_text",
                     "_fallback_tool_calls"):
            self.assertFalse(hasattr(m, name), name)

    def test_not_available_without_key_or_with_mock_provider(self):
        self.assertFalse(_llm("openai", "").available)
        self.assertFalse(_llm("mock", "sk-test").available)
        self.assertTrue(_llm("openai", "sk-test").available)
        self.assertTrue(_llm("anthropic", "sk-test").available)
        self.assertTrue(_llm("custom", "sk-test").available)

    def test_chat_raises_instead_of_falling_back(self):
        for provider, key in (("openai", ""), ("mock", "sk-test")):
            with self.assertRaises(RuntimeError) as cm:
                _llm(provider, key).chat([{"role": "user", "content": "open notepad"}])
            self.assertIn("LLM not configured", str(cm.exception))

    def test_provider_errors_propagate(self):
        m = _llm("openai", "sk-test")

        def boom(*a, **k):
            raise ConnectionError("provider down")
        m._chat_openai = boom
        with self.assertRaises(ConnectionError):
            m.chat([{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
