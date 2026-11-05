"""Tests for opening things from the Desktop (control/launcher.py).

Loaded by file path so it works on any OS and is unaffected by the control-module stubs
other test files install.
"""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "agent", "control", "launcher.py")
_spec = importlib.util.spec_from_file_location("launcher_under_test", _PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


def _touch(path, text=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class DesktopTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.user = os.path.join(tmp.name, "user_desktop")
        self.public = os.path.join(tmp.name, "public_desktop")
        os.makedirs(self.user)
        os.makedirs(self.public)
        _touch(os.path.join(self.user, "Google Chrome.lnk"))
        _touch(os.path.join(self.user, "Discord.url"))
        _touch(os.path.join(self.user, "portable_tool.exe"))
        _touch(os.path.join(self.user, "Budget 2026.xlsx"))
        _touch(os.path.join(self.user, "desktop.ini"))
        os.makedirs(os.path.join(self.user, "My Projects"))
        _touch(os.path.join(self.public, "VLC media player.lnk"))
        self.dirs = [self.user, self.public]

        # launching: pretend the shell opens everything, remember what was opened
        self.opened = []
        patcher = mock.patch.object(os, "startfile", lambda p: self.opened.append(p),
                                    create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(launcher, "desktop_dirs", lambda: self.dirs)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Start Menu / registry are empty here
        for name in ("list_start_menu_items", "list_registry_apps"):
            p = mock.patch.object(launcher, name, lambda: [])
            p.start()
            self.addCleanup(p.stop)


class TestListDesktopItems(DesktopTestCase):
    def test_lists_shortcuts_programs_folders_and_files(self):
        items = {i["name"]: i for i in launcher.list_desktop_items()}
        self.assertEqual(items["Google Chrome"]["type"], "shortcut")
        self.assertEqual(items["Discord"]["type"], "shortcut")
        self.assertEqual(items["portable_tool"]["type"], "exe")
        self.assertEqual(items["Budget 2026"]["type"], "xlsx")
        self.assertEqual(items["My Projects"]["type"], "folder")

    def test_includes_public_desktop(self):
        self.assertIn("VLC media player", [i["name"] for i in launcher.list_desktop_items()])

    def test_ignores_desktop_ini(self):
        self.assertNotIn("desktop", [i["name"].lower() for i in launcher.list_desktop_items()])

    def test_discover_installed_apps_includes_desktop_first(self):
        apps = launcher.discover_installed_apps()
        self.assertEqual(apps[0]["source"], "desktop")
        self.assertIn("Google Chrome", [a["name"] for a in apps])


class TestRankMatches(DesktopTestCase):
    def rank(self, q, entries=None):
        return [e["name"] for e in launcher.rank_matches(q, entries or launcher.list_desktop_items())]

    def test_partial_name(self):
        self.assertEqual(self.rank("chrome")[0], "Google Chrome")

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(self.rank("GOOGLE-chrome")[0], "Google Chrome")

    def test_query_with_extra_words(self):
        self.assertEqual(self.rank("google chrome browser")[0], "Google Chrome")

    def test_exact_beats_substring(self):
        entries = [
            {"name": "Code Helper", "filename": "Code Helper.lnk", "path": "a", "source": "desktop"},
            {"name": "Code", "filename": "Code.lnk", "path": "b", "source": "desktop"},
        ]
        self.assertEqual(self.rank("code", entries)[0], "Code")

    def test_desktop_beats_start_menu_on_tie(self):
        entries = [
            {"name": "Chrome", "filename": "Chrome.lnk", "path": "sm", "source": "start_menu"},
            {"name": "Chrome", "filename": "Chrome.lnk", "path": "dk", "source": "desktop"},
        ]
        self.assertEqual(launcher.rank_matches("chrome", entries)[0]["path"], "dk")

    def test_numbered_icons_match_exactly(self):
        """Chrome profile shortcuts '1' and '-1' are different; '1' must not open '10'."""
        entries = [{"name": n, "filename": n + ".lnk", "path": n, "source": "desktop"}
                   for n in ("1", "-1", "10", "24", "-24")]
        self.assertEqual(self.rank("-1", entries), ["-1"])
        self.assertEqual(self.rank("1", entries), ["1"])
        self.assertEqual(self.rank("-24", entries), ["-24"])
        self.assertEqual(self.rank("24", entries), ["24"])
        # no near-miss fallback for numbers: fail instead of opening the wrong profile
        self.assertEqual(self.rank("-1", [entries[0], entries[2]]), [])
        self.assertEqual(self.rank("1", [entries[1], entries[2]]), [])

    def test_exact_desktop_match_numbered(self):
        items = [{"name": "1", "filename": "1.lnk", "path": "p1", "source": "desktop"},
                 {"name": "-1", "filename": "-1.lnk", "path": "pm1", "source": "desktop"}]
        orig = launcher.list_desktop_items
        launcher.list_desktop_items = lambda dirs=None: items
        try:
            self.assertEqual(launcher.exact_desktop_match("-1")["path"], "pm1")
            self.assertEqual(launcher.exact_desktop_match("1")["path"], "p1")
            self.assertIsNone(launcher.exact_desktop_match("-2"))
        finally:
            launcher.list_desktop_items = orig

    def test_empty_and_unknown(self):
        self.assertEqual(self.rank(""), [])
        self.assertEqual(self.rank("zzz nothing"), [])


class TestLaunch(DesktopTestCase):
    def test_launch_by_name_opens_desktop_shortcut_via_shell(self):
        pid = launcher.launch_by_name("chrome")
        self.assertEqual(pid, 0)
        self.assertEqual(self.opened, [os.path.join(self.user, "Google Chrome.lnk")])

    def test_launch_public_desktop_item(self):
        self.assertEqual(launcher.launch_by_name("vlc"), 0)
        self.assertEqual(self.opened, [os.path.join(self.public, "VLC media player.lnk")])

    def test_folder_and_document_open(self):
        self.assertEqual(launcher.launch_by_name("my projects"), 0)
        self.assertEqual(launcher.launch_by_name("budget"), 0)
        self.assertEqual([os.path.basename(p) for p in self.opened],
                         ["My Projects", "Budget 2026.xlsx"])

    def test_shortcut_is_never_started_with_popen(self):
        with mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch_path(os.path.join(self.user, "Google Chrome.lnk"))
        popen.assert_not_called()

    def test_exe_uses_popen_and_returns_pid(self):
        exe = os.path.join(self.user, "portable_tool.exe")
        with mock.patch.object(launcher.subprocess, "Popen",
                               return_value=mock.Mock(pid=4321)) as popen:
            self.assertEqual(launcher.launch_path(exe), 4321)
        self.assertEqual(popen.call_args[0][0], [exe])

    def test_exe_falls_back_to_shell_when_popen_fails(self):
        exe = os.path.join(self.user, "portable_tool.exe")
        with mock.patch.object(launcher.subprocess, "Popen", side_effect=OSError("elevation")):
            self.assertEqual(launcher.launch_path(exe), 0)
        self.assertEqual(self.opened, [exe])

    def test_tries_next_candidate_when_first_fails(self):
        _touch(os.path.join(self.user, "Chrome Beta.lnk"))
        first = os.path.join(self.user, "Chrome Beta.lnk")

        def flaky(path):
            if path == first:
                raise OSError("broken shortcut")
            self.opened.append(path)

        with mock.patch.object(os, "startfile", flaky, create=True):
            self.assertEqual(launcher.launch_by_name("chrome"), 0)
        self.assertEqual(self.opened, [os.path.join(self.user, "Google Chrome.lnk")])

    def test_falls_back_to_registry_when_desktop_does_not_match(self):
        reg_exe = os.path.join(self.user, "portable_tool.exe")
        reg = [{"name": "Some Editor", "filename": "editor.exe", "path": reg_exe,
                "type": "exe", "source": "registry"}]
        with mock.patch.object(launcher, "list_registry_apps", lambda: reg), \
                mock.patch.object(launcher.subprocess, "Popen",
                                  return_value=mock.Mock(pid=7)):
            self.assertEqual(launcher.launch_by_name("some editor"), 7)

    def test_unknown_name_returns_none(self):
        self.assertIsNone(launcher.launch_by_name("does not exist"))
        self.assertEqual(self.opened, [])

    def test_exact_desktop_match(self):
        self.assertEqual(launcher.exact_desktop_match("discord")["name"], "Discord")
        self.assertIsNone(launcher.exact_desktop_match("disc"))


class TestRegistryIconParsing(unittest.TestCase):
    def test_strips_quotes_and_icon_index_and_skips_uninstallers(self):
        with tempfile.TemporaryDirectory() as d:
            app = os.path.join(d, "app.exe")
            un = os.path.join(d, "unins000.exe")
            _touch(app)
            _touch(un)
            self.assertEqual(launcher._clean_icon_path(f'"{app}",0'), app)
            self.assertEqual(launcher._clean_icon_path(f"{app},-101"), app)
            self.assertIsNone(launcher._clean_icon_path(un))
            self.assertIsNone(launcher._clean_icon_path(d))          # InstallLocation-style dir
            self.assertIsNone(launcher._clean_icon_path(None))


if __name__ == "__main__":
    unittest.main()
