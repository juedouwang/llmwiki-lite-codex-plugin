"""Opt-in real Chromium editor acceptance; only disposable registry/wiki data."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
import research_notebook as nb  # noqa: E402
import research_reports as reports  # noqa: E402
from web_server import create_server  # noqa: E402
from run_reports_browser import _png_bytes  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="llmwiki-live-editor-") as tmp:
        root = Path(tmp)
        home = str(root / "home")
        source = root / "source"
        source.mkdir()
        project = register_project(str(source), name="共用编辑器临时验收", home=home, wiki_root=str(root / "wiki"))["project"]
        png = base64.b64encode(_png_bytes()).decode()
        image = nb.upload(project, {"data": png})["image"]
        legacy_id = "a" * 32
        nb.save(project, legacy_id, {"revision": "", "document": {"title": "旧 block 笔记", "blocks": [
            {"id": "old-text", "type": "markdown", "text": "旧正文  \n\n```python\nx = 1\n```", "comments": ["原有批注不能丢"]},
            {"id": "old-image", "type": "image", "image": image, "text": "人工图片说明", "comments": ["图片批注"]}
        ]}})
        legacy_path = Path(project["wiki_root"]) / "records/manual" / (legacy_id + ".md")
        legacy_before = legacy_path.read_bytes()
        owner = reports.workspace(home)
        for kind, day in [("daily", "2026-09-19"), ("weekly", "2026-09-14")]:
            reports.create(owner, kind, day, [project["id"]], home=home)
        server = create_server(home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        evidence = Path(os.environ.get("TEMP", tmp)) / "llmwiki-live-editor-evidence"
        evidence.mkdir(exist_ok=True)
        config = {"origin": f"http://127.0.0.1:{server.server_port}", "pid": project["id"], "png": png,
                  "legacyId": legacy_id, "legacyPath": str(legacy_path), "evidence": str(evidence)}
        try:
            result = subprocess.run([os.environ.get("NODE", "node"), str(Path(__file__).with_name("document_editor_browser_test.cjs")), json.dumps(config)], timeout=240)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        # Opening/previewing the legacy fixture must not migrate it; the browser
        # checks this before the explicit edit, whose original snapshot survives.
        import hashlib
        snapshot = Path(project["wiki_root"]) / ".notebook-history" / legacy_id / (hashlib.sha256(legacy_before).hexdigest() + ".snapshot")
        if result.returncode == 0:
            assert snapshot.read_bytes() == legacy_before
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
