"""End-to-end verification against a real packaged desktop window and WebView2."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import date
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

import app

sys.path.insert(0, str(app.plugin_scripts()))
from llmwiki_registry import register_project  # noqa: E402
from daily_tasks import mutate  # noqa: E402


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_start(process, home, debug_port):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Application exited early: {process.returncode}")
        state = app.read_settings(home / "desktop" / "instance.json")
        try:
            with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=0.5) as response:
                pages = json.load(response)
            if any(page.get("url", "").startswith(state.get("url", "http://not-ready/")) for page in pages):
                return state
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise TimeoutError("WebView2 did not load; see isolated logs/desktop.log")


def close_window(process, pid):
    handle = app.find_window(pid)
    if not handle:
        raise AssertionError("Packaged application has no native visible window")
    ctypes.windll.user32.PostMessageW(wintypes.HWND(handle), 0x0010, 0, 0)  # WM_CLOSE
    process.wait(timeout=15)
    assert process.returncode == 0, process.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=Path(__file__).parent / "dist/WildResearchWorkbench/WildResearchWorkbench.exe")
    args = parser.parse_args()
    executable = args.exe.resolve()
    assert executable.is_file(), executable
    evidence = Path(__file__).resolve().parent / "test-output" / "packaged"
    evidence.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW
    process = None
    with tempfile.TemporaryDirectory(prefix="workbench-desktop-exe-", ignore_cleanup_errors=True) as temp:
        root = Path(temp)
        home = root / "home"
        source = root / "research-source"
        source.mkdir()
        (source / "README.md").write_text("# 临时桌面验收项目\n", encoding="utf-8")
        for command in (["init"], ["config", "user.name", "Desktop Test"],
                        ["config", "user.email", "desktop@example.invalid"], ["add", "README.md"],
                        ["-c", "commit.gpgsign=false", "commit", "-m", "desktop test fixture"]):
            subprocess.run(["git", "-C", str(source), *command], check=True,
                           capture_output=True, creationflags=flags)
        project = register_project(str(source), name="桌面端 · 临时验收", home=str(home),
                                   wiki_root=str(root / "wiki"))["project"]
        mutate(str(home), {"project_id": project["id"], "revision": "", "action": "create",
                          "task": {"title": "验证桌面科研记录", "scheduled_date": date.today().isoformat()}}, actor="user")
        config_path = root / "test-config.json"
        debug_port = available_port()
        command = [str(executable), "--home", str(home), "--debug-port", str(debug_port)]
        try:
            started = time.monotonic()
            process = subprocess.Popen([*command, "--port", "0"], cwd=root, creationflags=flags)
            state = wait_for_start(process, home, debug_port)
            startup = round(time.monotonic() - started, 2)
            assert app.find_window(state["pid"]), "Native application window must exist"
            repeated = subprocess.run(command, cwd=root, timeout=8, creationflags=flags)
            assert repeated.returncode == 0
            assert app.read_settings(home / "desktop/instance.json")["pid"] == state["pid"]
            config = {"origin": state["url"].rstrip("/"), "debug": f"http://127.0.0.1:{debug_port}",
                      "pid": project["id"], "evidence": str(evidence)}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            subprocess.run([os.environ.get("NODE", "node"), str(Path(__file__).with_name("native_acceptance.cjs")),
                            str(config_path)], check=True, timeout=120, creationflags=flags)
            close_window(process, state["pid"])
            assert not (home / "desktop/instance.json").exists()
            port = int(state["url"].split(":")[-1].rstrip("/"))
            with socket.socket() as sock:
                assert sock.connect_ex(("127.0.0.1", port)) != 0, "Closing must stop desktop HTTP"
            note = Path(project["wiki_root"]) / "records/manual" / ("d" * 32 + ".md")
            assert "CLOSE-SAVE-CHECK" in note.read_text(encoding="utf-8")
            # Same deployed EXE, existing home and persisted localStorage origin.
            process = subprocess.Popen(command, cwd=root, creationflags=flags)
            restarted = wait_for_start(process, home, debug_port)
            assert state["url"] == restarted["url"]
            config["phase"] = "restart"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            subprocess.run([os.environ.get("NODE", "node"), str(Path(__file__).with_name("native_acceptance.cjs")),
                            str(config_path)], check=True, timeout=40, creationflags=flags)
            close_window(process, restarted["pid"])
            report = {"ok": True, "executable": str(executable), "startup_seconds": startup,
                      "native_window": True, "single_instance": True, "close_releases_port": True,
                      "close_preserves_autosave": True, "restart_preserves_data_and_theme": True,
                      "isolated_data": True}
            (evidence / "lifecycle-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            return 0
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
