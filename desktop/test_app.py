"""Desktop lifecycle tests with temporary registries; never use real project data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402

sys.path.insert(0, str(app.plugin_scripts()))
from llmwiki_registry import register_project  # noqa: E402


class DesktopLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="desktop-test-")
        self.root = Path(self.temporary.name)
        self.runtime = app.DesktopServer(self.root / "home", port=0)

    def tearDown(self):
        self.runtime.stop()
        self.temporary.cleanup()

    def read(self, url):
        with urlopen(url, timeout=3) as response:
            return response.read().decode("utf-8")

    def test_real_service_displays_existing_registry_without_copy(self):
        source = self.root / "source"
        source.mkdir()
        register_project(str(source), name="桌面测试项目", home=str(self.runtime.home),
                         wiki_root=str(self.root / "wiki"))
        registry = self.runtime.home / "registry.json"
        before = registry.read_bytes()
        url = self.runtime.start()
        self.assertEqual(json.loads(self.read(url + "health"))["service"], "llmwiki-web")
        self.assertIn("桌面测试项目", self.read(url + "projects"))
        self.assertIn("每日待办", self.read(url + "daily"))
        self.assertEqual(registry.read_bytes(), before)
        self.assertEqual(self.runtime.server.server_address[0], "127.0.0.1")

    def test_close_releases_port_and_leaves_regular_web_server_untouched(self):
        url = self.runtime.start()
        regular = self.runtime.home / "web-server.json"
        regular.write_text('{"pid":123,"port":8766}', encoding="utf-8")
        self.runtime.stop()
        with self.assertRaises(OSError):
            self.read(url + "health")
        self.assertFalse(self.runtime.instance_file.exists())
        self.assertEqual(json.loads(regular.read_text())["pid"], 123)

    def test_busy_port_uses_owned_server_and_preserves_other_service(self):
        first_url = self.runtime.start()
        second = app.DesktopServer(self.root / "another-home", self.runtime.port)
        try:
            second_url = second.start()
            self.assertNotEqual(first_url, second_url)
            self.assertEqual(json.loads(self.read(first_url + "health"))["service"], "llmwiki-web")
        finally:
            second.stop()
        self.assertEqual(json.loads(self.read(first_url + "health"))["service"], "llmwiki-web")

    def test_restart_preserves_origin_for_theme_and_recovery_drafts(self):
        original = self.runtime.start()
        self.runtime.stop()
        self.runtime = app.DesktopServer(self.root / "home")
        self.assertEqual(self.runtime.start(), original)

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_single_instance_is_scoped_to_the_data_directory(self):
        first = app.SingleInstance(self.runtime.home)
        second = app.SingleInstance(self.runtime.home)
        independent = app.SingleInstance(self.root / "another-home")
        try:
            self.assertTrue(first.is_first)
            self.assertFalse(second.is_first)
            self.assertTrue(independent.is_first)
        finally:
            first.close()
            second.close()
            independent.close()


class CloseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="desktop-close-")
        self.runtime = app.DesktopServer(Path(self.temporary.name))
        self.window = Mock(width=1200, height=800)
        self.window.events.loaded.is_set.return_value = True
        self.guard = app.CloseGuard(self.window, self.runtime)

    def tearDown(self):
        self.temporary.cleanup()

    def test_saved_page_closes_without_prompt(self):
        self.window.evaluate_js.return_value = False
        self.guard._check()
        self.window.create_confirmation_dialog.assert_not_called()
        self.window.destroy.assert_called_once()
        self.assertTrue(self.guard.allowed)

    def test_autosave_finishes_before_close_without_prompt(self):
        self.window.evaluate_js.side_effect = [True, False]
        with patch("app.time.sleep"):
            self.guard._check()
        self.window.create_confirmation_dialog.assert_not_called()
        self.window.destroy.assert_called_once()

    def test_cancel_close_preserves_unsaved_content(self):
        self.window.evaluate_js.return_value = True
        self.window.create_confirmation_dialog.return_value = False
        with patch("app.time.sleep"):
            self.guard._check()
        self.window.destroy.assert_not_called()
        self.assertFalse(self.guard.allowed)

    def test_explicit_discard_can_close(self):
        self.window.evaluate_js.return_value = True
        self.window.create_confirmation_dialog.return_value = True
        with patch("app.time.sleep"):
            self.guard._check()
        self.window.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
