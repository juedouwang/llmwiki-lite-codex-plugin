"""Headless progress acceptance using an existing Playwright install; no real data."""
import base64
from datetime import date, timedelta
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_progress as progress  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402

def screenshot_png():
    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 96, 48, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x80\xad\xd4" * 96) * 48)) + chunk(b"IEND", b""))


PNG = base64.b64encode(screenshot_png()).decode("ascii")


def main():
    with tempfile.TemporaryDirectory(prefix="progress-workbench-browser-") as directory:
        root = Path(directory)
        home = str(root / "home")
        projects = []
        for name in ("research", "other", "legacy-readonly"):
            source = root / name
            source.mkdir()
            projects.append(register_project(str(source), home=home, wiki_root=str(root / (name + "-wiki")))["project"])
        project, other, legacy = projects

        def create(owner, **fields):
            return progress.update(owner, {"action": "create", "revision": progress.load_summary(owner)["revision"], "task": fields})["tasks"][-1]

        create(project, title="高优先级未排期", priority="high", description="高优先级说明")
        create(project, title="中优先级今天截止", priority="medium", ddl=date.today().isoformat())
        create(project, title="低优先级明天截止", priority="low", ddl=(date.today() + timedelta(days=1)).isoformat())
        old = create(project, title="旧任务兼容", status="blocked", checkpoint="上次完成基线", next_step="下一步验证", start="2026-09-01", end="2026-09-20")
        create(project, title="旧已完成", status="done", checkpoint="原始完成记录")
        path = Path(project["wiki_root"]) / ".research-progress/tasks.json"
        data = json.loads(path.read_bytes())
        raw = next(t for t in data["tasks"] if t["id"] == old["id"])
        raw["assistant_context"] = {"opaque": ["保留助手原文", 42]}
        before_old = json.loads(json.dumps(raw))
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        create(other, title="其他项目不可修改", status="active")
        legacy_path = Path(legacy["wiki_root"]) / ".research-progress/tasks.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_bytes(path.read_bytes())
        other_path = Path(other["wiki_root"]) / ".research-progress/tasks.json"
        protected = {legacy_path: legacy_path.read_bytes(), other_path: other_path.read_bytes()}
        output = Path(os.environ.get("LLMWIKI_PROGRESS_ARTIFACTS", root / "artifacts"))
        output.mkdir(parents=True, exist_ok=True)
        png_path = root / "clipboard.png"
        png_path.write_bytes(base64.b64decode(PNG))
        server = create_server(home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        config = {"origin": f"http://127.0.0.1:{server.server_port}", "projectId": project["id"], "otherProjectId": other["id"], "legacyProjectId": legacy["id"], "png": PNG, "pngPath": str(png_path), "output": str(output), "legacyId": old["id"]}
        try:
            result = subprocess.run([os.environ.get("NODE", "node"), str(Path(__file__).with_name("progress_workbench_browser_test.cjs")), json.dumps(config)], env={**os.environ, "LLMWIKI_HOME": home}, timeout=240, capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(result.stdout)
            if result.returncode:
                print(result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        for file, before in protected.items():
            if file.read_bytes() != before:
                raise AssertionError("Read-only/other project was modified")
        saved = next(t for t in json.loads(path.read_bytes())["tasks"] if t["id"] == old["id"])
        for key in ("checkpoint", "next_step", "start", "end", "assistant_context", "context_mode"):
            if saved[key] != before_old[key]:
                raise AssertionError("Legacy metadata was lost: " + key)
        if not result.returncode:
            print("PASS: protected projects unchanged; legacy metadata preserved; screenshots:", output)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
