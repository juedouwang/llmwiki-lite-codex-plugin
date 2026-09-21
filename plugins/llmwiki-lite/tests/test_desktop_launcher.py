"""Desktop launcher checks: reuse a loopback server, never touch user data."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import desktop_launcher  # noqa: E402
from web_server import create_server  # noqa: E402


class DesktopLauncherTests(unittest.TestCase):
    def test_explicit_home_and_port_open_browser(self):
        with patch("web_server.start_background") as start, patch.object(desktop_launcher, "show_error") as error:
            self.assertEqual(desktop_launcher.main(["--home", "test-home", "--port", "8766"]), 0)
        start.assert_called_once_with(home="test-home", port=8766, open_browser=True)
        error.assert_not_called()

    def test_default_arguments_delegate_to_existing_settings(self):
        with patch("web_server.start_background") as start:
            self.assertEqual(desktop_launcher.main([]), 0)
        start.assert_called_once_with(home=None, port=None, open_browser=True)

    def test_failure_is_logged_without_console(self):
        with tempfile.TemporaryDirectory() as tmp, patch("web_server.start_background", side_effect=OSError("port busy")), patch.object(desktop_launcher, "show_error") as error:
            self.assertEqual(desktop_launcher.main(["--home", tmp, "--port", "8766"]), 1)
            log = Path(tmp) / "logs" / "desktop-launch-error.log"
            self.assertIn("port busy", log.read_text(encoding="utf-8"))
            self.assertIn(str(log), error.call_args.args[0])

    def test_unwritable_error_log_still_notifies(self):
        with patch("web_server.start_background", side_effect=OSError("failure")), patch.object(Path, "mkdir", side_effect=OSError("read-only")), patch.object(desktop_launcher, "show_error") as error:
            self.assertEqual(desktop_launcher.main(["--home", "unwritable"]), 1)
        self.assertIn("无法写入", error.call_args.args[0])

    @unittest.skipUnless(os.name == "nt", "Windows silent process flags")
    def test_new_server_uses_silent_existing_startup(self):
        from web_server import start_background
        with tempfile.TemporaryDirectory() as tmp, patch("web_server.is_server", side_effect=[False, True]), patch("web_server.subprocess.Popen") as spawn, patch("web_server.webbrowser.open", return_value=True):
            result = start_background(home=tmp, port=8766, open_browser=True)
        self.assertTrue(result["started"])
        command = spawn.call_args.args[0]
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        self.assertEqual(command[0], str(pythonw) if pythonw.is_file() else sys.executable)
        self.assertIn("--port", command)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(spawn.call_args.kwargs[stream], subprocess.DEVNULL)
        self.assertTrue(spawn.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(spawn.call_args.kwargs["creationflags"] & subprocess.DETACHED_PROCESS)

    def test_repeated_launch_reuses_real_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = create_server(tmp, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_port
                with patch("web_server.webbrowser.open", return_value=True) as browser, patch("web_server.subprocess.Popen") as spawn:
                    for _ in range(2):
                        self.assertEqual(desktop_launcher.main(["--home", tmp, "--port", str(port)]), 0)
                    self.assertEqual(browser.call_count, 2)
                    browser.assert_called_with(f"http://127.0.0.1:{port}/")
                    spawn.assert_not_called()
                self.assertFalse((Path(tmp) / "logs").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
