"""Tests for the mouse tools the agent was missing (scroll, double-click, drag, ...),
typing of non-ASCII text, and the autonomy / wallet-safety rules in the system prompt.

Runs on Linux/CI: OS-level libraries are stubbed (see test_executor_actions), and the real
DesktopControl is loaded against fake pyautogui / pyperclip modules.
"""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent")
sys.path.insert(0, AGENT_DIR)

# Importing this module installs the control/screen stubs and an isolated DATA_DIR.
from tests.test_executor_actions import executor, registry, task_engine  # noqa: E402
from discovery.adapters import DesktopAdapter  # noqa: E402
from core.loop import SYSTEM_PROMPT, format_tools  # noqa: E402


class TestNewDesktopTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = DesktopAdapter()
        cls.names = cls.adapter.register()

    def setUp(self):
        self.adapter.desktop.reset_mock()
        self.tid = task_engine.create_task("scroll test")
        self.task = types.SimpleNamespace(id=self.tid, instruction="scroll test")

    def run_action(self, tool, args):
        return executor.execute_action(tool, args, self.task, task_engine)

    # ---------------------------------------------------------------- registration
    def test_tools_are_registered_and_shown_to_the_llm(self):
        wanted = {"desktop.scroll", "desktop.double_click", "desktop.drag",
                  "desktop.move_mouse", "desktop.focus_window"}
        self.assertTrue(wanted.issubset(set(self.names)), self.names)
        catalog = format_tools(registry.list_tools())
        for name in wanted:
            self.assertIn(name, catalog)

    # ---------------------------------------------------------------- scroll
    def test_scroll_down_uses_negative_wheel_clicks(self):
        res = self.run_action("desktop.scroll", {"direction": "down", "amount": 3, "x": 900, "y": 500})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.scroll.assert_called_once_with(-3, x=900, y=500)

    def test_scroll_up_uses_positive_wheel_clicks(self):
        res = self.run_action("desktop.scroll", {"direction": "up", "amount": 4})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.scroll.assert_called_once_with(4, x=None, y=None)

    def test_scroll_defaults_to_five_notches_down(self):
        res = self.run_action("desktop.scroll", {})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.scroll.assert_called_once_with(-5, x=None, y=None)

    def test_scroll_amount_is_capped(self):
        res = self.run_action("desktop.scroll", {"direction": "down", "amount": 999})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.scroll.assert_called_once_with(-30, x=None, y=None)

    def test_scroll_rejects_bad_direction_without_touching_the_mouse(self):
        res = self.run_action("desktop.scroll", {"direction": "sideways"})
        self.assertFalse(res["ok"])
        self.adapter.desktop.scroll.assert_not_called()

    def test_scroll_needs_both_coordinates_or_neither(self):
        res = self.run_action("desktop.scroll", {"direction": "down", "x": 500})
        self.assertFalse(res["ok"])
        self.assertIn("both x and y", res["error"])
        self.adapter.desktop.scroll.assert_not_called()

    # ---------------------------------------------------------------- other mouse tools
    def test_double_click(self):
        res = self.run_action("desktop.double_click", {"x": 10, "y": 20})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.double_click.assert_called_once_with(10, 20)

    def test_drag_goes_from_start_to_end(self):
        res = self.run_action("desktop.drag", {"from_x": 1, "from_y": 2, "to_x": 30, "to_y": 40})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.drag_to.assert_called_once_with(30, 40, from_x=1, from_y=2)

    def test_move_mouse(self):
        res = self.run_action("desktop.move_mouse", {"x": 5, "y": 6})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.move_to.assert_called_once_with(5, 6)

    def test_focus_window_success(self):
        self.adapter.desktop.switch_to_window.return_value = True
        res = self.run_action("desktop.focus_window", {"title_contains": "Telegram"})
        self.assertTrue(res["ok"], res)
        self.adapter.desktop.switch_to_window.assert_called_once_with("Telegram")

    def test_focus_window_failure_lists_open_windows_so_the_llm_can_retry(self):
        self.adapter.desktop.switch_to_window.return_value = False
        self.adapter.desktop.get_all_windows.return_value = [
            types.SimpleNamespace(title="Notepad"), types.SimpleNamespace(title="Chrome")]
        res = self.run_action("desktop.focus_window", {"title_contains": "Telegram"})
        self.assertFalse(res["ok"])
        self.assertIn("Notepad", res["error"])
        self.assertIn("Chrome", res["error"])


class TestUnicodeTyping(unittest.TestCase):
    """Load the REAL DesktopControl against fake pyautogui/pyperclip."""

    def setUp(self):
        self.pg, self.pc = mock.MagicMock(), mock.MagicMock()
        self.mods = {"mss": mock.MagicMock(), "pyautogui": self.pg, "pyperclip": self.pc}
        path = os.path.join(AGENT_DIR, "control", "desktop.py")
        with mock.patch.dict(sys.modules, self.mods):
            spec = importlib.util.spec_from_file_location("real_desktop_under_test", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.desktop = mod.DesktopControl()

    def type_text(self, text):
        with mock.patch.dict(sys.modules, self.mods), mock.patch("time.sleep"):
            self.desktop.type_text(text)

    def test_ascii_is_typed_key_by_key(self):
        self.type_text("hello world")
        self.pg.typewrite.assert_called_once()
        self.pc.copy.assert_not_called()

    def test_bengali_is_pasted_instead_of_silently_dropped(self):
        text = "আমি টেলিগ্রাম খুলছি"
        self.pc.paste.return_value = ""
        self.type_text(text)
        self.pg.typewrite.assert_not_called()
        self.pc.copy.assert_called_once_with(text)
        self.pg.hotkey.assert_called_once_with("ctrl", "v")

    def test_mixed_text_is_pasted_whole(self):
        self.pc.paste.return_value = ""
        self.type_text("Hi আমি")
        self.pg.typewrite.assert_not_called()
        self.pc.copy.assert_called_once_with("Hi আমি")

    def test_previous_clipboard_is_restored_after_pasting(self):
        self.pc.paste.return_value = "my old clipboard"
        self.type_text("emoji 👍")
        copies = [c.args[0] for c in self.pc.copy.call_args_list]
        self.assertEqual(copies, ["emoji 👍", "my old clipboard"])


class TestPromptRules(unittest.TestCase):
    def test_prompt_tells_the_llm_to_scroll_long_content(self):
        self.assertIn("desktop.scroll", SYSTEM_PROMPT)

    def test_prompt_tells_the_llm_to_decide_for_itself(self):
        self.assertIn("BE AUTONOMOUS", SYSTEM_PROMPT)

    def test_prompt_keeps_wallet_and_signing_behind_the_owner(self):
        low = SYSTEM_PROMPT.lower()
        for phrase in ("seed phrase", "private key", "sign a message", "needs_user"):
            self.assertIn(phrase, low)


if __name__ == "__main__":
    unittest.main()
