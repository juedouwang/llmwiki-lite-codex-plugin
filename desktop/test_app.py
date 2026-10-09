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


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="desktop-update-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.calls = []

    def opener(self, payload):
        def open_url(request, timeout):
            self.calls.append((request.full_url, timeout))
            return FakeResponse(payload)
        return open_url

    def test_records_latest_release_and_checks_at_most_daily(self):
        release = {"tag_name": "v0.4.1", "html_url": "https://github.com/x/y/releases/tag/v0.4.1"}
        state = app.check_for_update(self.home, "0.4.0", opener=self.opener(release), now=1000.0)
        self.assertEqual(state["latest"], "v0.4.1")
        self.assertEqual(self.calls, [(app.LATEST_RELEASE_API, 5)])
        cached = json.loads((self.home / "desktop" / "update.json").read_text(encoding="utf-8"))
        self.assertEqual(cached["url"], release["html_url"])
        # Within a day the cache answers; a new build checks again.
        app.check_for_update(self.home, "0.4.0", opener=self.opener(release), now=1000.0 + 3600)
        self.assertEqual(len(self.calls), 1)
        app.check_for_update(self.home, "0.4.1", opener=self.opener(release), now=1000.0 + 3600)
        self.assertEqual(len(self.calls), 2)

    def test_offline_or_odd_reply_keeps_previous_state(self):
        attempts = []

        def offline(request, timeout):
            attempts.append(1)
            raise OSError("offline")
        with patch("app.time.sleep"):
            self.assertEqual(app.check_for_update(self.home, "0.4.0", opener=offline, now=1.0), {})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(app.check_for_update(self.home, "0.4.0", opener=self.opener([]), now=1.0), {})
        self.assertFalse((self.home / "desktop" / "update.json").exists())

    def test_one_dropped_handshake_is_retried(self):
        replies = [OSError("EOF"), FakeResponse({"tag_name": "v0.4.1", "html_url": "https://github.com/x"})]

        def flaky(request, timeout):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        with patch("app.time.sleep"):
            self.assertEqual(app.check_for_update(self.home, "0.4.0", opener=flaky, now=1.0)["latest"], "v0.4.1")

    def test_update_check_can_be_turned_off(self):
        self.assertTrue(app.update_check_enabled(self.home))
        with patch.dict(os.environ, {"LLMWIKI_UPDATE_CHECK": "0"}):
            self.assertFalse(app.update_check_enabled(self.home))
        app.write_settings(self.home / "desktop" / "window.json", {"update_check": False})
        self.assertFalse(app.update_check_enabled(self.home))

    def test_bundled_version_comes_from_the_plugin_manifest(self):
        manifest = json.loads((app.plugin_scripts().parent / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(app.bundled_version(app.plugin_scripts()), manifest["version"])


if __name__ == "__main__":
    unittest.main()
