"""Opt-in browser test runner. Only temporary data; no server integration edits.

Run directly, not via unittest. LLMWIKI_PLAYWRIGHT selects an existing installation.
Uses production create_server routing without dispatcher shims.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def main():
    from llmwiki_registry import register_project
    from literature_catalog import literature_collect
    from web_server import create_server
    with tempfile.TemporaryDirectory(prefix="llmwiki-literature-browser-") as tmp:
        root = Path(tmp)
        home = str(root / "home")
        source = root / "source"
        source.mkdir()
        project = register_project(str(source), name="文献隔离验收", wiki_root=str(root / "wiki"), state_root=str(root / "state"), home=home)["project"]
        # Separate project has a reading fixture so the first UI list starts empty.
        other = root / "other"
        other.mkdir()
        reading = register_project(str(other), name="明确阅读绑定", wiki_root=str(root / "other-wiki"), state_root=str(root / "other-state"), home=home)["project"]
        (other / "paper.pdf").write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
        (Path(reading["wiki_root"]) / "note.md").write_text('---\npaper_file: paper.pdf\n---\n# 隔离阅读笔记\n本地临时测试。', encoding="utf-8")
        result = literature_collect(reading["id"], "10.1234/read", "f" * 32, paper_file="paper.pdf", reading_note_paths=["note.md"], home=home)
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        evidence = str(Path(os.environ.get("TEMP", tmp)) / "llmwiki-literature-evidence")
        config = {"origin": f"http://127.0.0.1:{server.server_port}", "pid": project["id"], "readingUrl": result["url"], "evidence": evidence}
        try:
            result = subprocess.run(["node", str(Path(__file__).with_suffix(".cjs")), json.dumps(config)], timeout=180)
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    sys.exit(main())
