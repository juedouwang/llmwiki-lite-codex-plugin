"""Opt-in headed-browser acceptance; never uses real projects or browser profiles."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
import research_progress as progress  # noqa: E402
from web_server import create_server  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="llmwiki-real-visibility-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        home = str(root / "home")
        project = register_project(str(source), home=home, wiki_root=str(root / "wiki"))["project"]
        created = progress.update(project, {
            "action": "create", "revision": "",
            "task": {"title": "真实标签页切换验收", "status": "active"},
        })
        tasks_file = root / "wiki/.research-progress/tasks.json"
        before = tasks_file.read_bytes()
        server = create_server(home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        chrome = Path(os.environ.get("LLMWIKI_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"))
        browser_process = None
        try:
            if not chrome.is_file():
                raise RuntimeError("Set LLMWIKI_CHROME to an installed Chrome executable; no browser will be installed.")
            profile = root / "browser-profile"
            # A fresh, ordinary Chrome. Do not launch through Playwright: its default
            # focus emulation would make document.hidden stay false in background tabs.
            browser_process = subprocess.Popen([
                str(chrome), f"--user-data-dir={profile}", "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-sync", "--window-size=1200,850", "about:blank",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.exists():
                if browser_process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("The isolated Chrome did not start its local test endpoint.")
                time.sleep(0.1)
            port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
            config = json.dumps({
                "origin": f"http://127.0.0.1:{server.server_port}",
                "browserEndpoint": f"http://127.0.0.1:{port}",
                "projectId": project["id"], "taskId": created["tasks"][0]["id"],
            })
            result = subprocess.run([
                os.environ.get("NODE", "node"),
                str(Path(__file__).with_name("progress_visibility_browser_test.cjs")), config,
            ], env={**os.environ, "LLMWIKI_HOME": home}, timeout=120)
        finally:
            if browser_process is not None:
                try:
                    browser_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    browser_process.terminate()
                    browser_process.wait(timeout=10)
            server.shutdown()
            server.server_close()
            worker.join()
        if tasks_file.read_bytes() != before:
            raise AssertionError("Visibility polling changed persisted tasks")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
