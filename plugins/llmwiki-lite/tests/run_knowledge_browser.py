"""Start only an isolated ephemeral server and run the repository's browser test."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402
import knowledge_maintenance as km  # noqa: E402
from uuid import uuid4


def main():
    with tempfile.TemporaryDirectory(prefix="llmwiki-knowledge-browser-") as tmp:
        root = Path(tmp)
        home = str(root / "home")
        source = root / "source"
        source.mkdir()
        (source / "main.py").write_text(
            "async def compute():\n    return 42\n", encoding="utf-8"
        )
        p = register_project(str(source), name="知识维护验收", home=home)["project"]
        wiki = Path(p["wiki_root"])
        for name in ("architecture", "keep", "stale"):
            (wiki / (name + ".md")).write_text(
                "# 架构说明\n\n同步调用。\n", encoding="utf-8"
            )
        run = km.knowledge_plan("manual", p["id"], home=home)["run_id"]
        items = km.knowledge_sources(run, "changes", home=home)["items"]
        i = next(x for x in items if x["locator"] == "source:main.py")
        actions = []
        for name in ("architecture", "keep", "stale"):
            page = km.knowledge_sources(run, "page", page_path=name + ".md", home=home)[
                "items"
            ][0]
            actions.append(
                {
                    "action_id": uuid4().hex,
                    "mode": "replace",
                    "page_path": name + ".md",
                    "base_sha256": page["base_sha256"],
                    "content": "# 架构说明\n\n**异步调用**。\n\n<script>window.unsafe=true</script>",
                    "reason": "源码中 compute 已改为 async。",
                    "evidence_refs": [
                        {k: i[k] for k in ("source_id", "locator", "revision")}
                        | {"excerpt": "async def compute()"}
                    ],
                }
            )
        km.knowledge_finish(
            run, "reviewed", [x["source_id"] for x in items], actions, home=home
        )
        # External change after generation: the browser must disable adoption.
        (wiki / "stale.md").write_text("# 我的批注\n保留手写内容。", encoding="utf-8")
        server = create_server(home, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        evidence = str(Path(tempfile.gettempdir()) / "llmwiki-knowledge-evidence")
        cfg = {
            "origin": f"http://127.0.0.1:{server.server_port}",
            "pid": p["id"],
            "evidence": evidence,
        }
        try:
            result = subprocess.run(
                [
                    "node",
                    str(
                        Path(__file__).with_name(
                            "knowledge_maintenance_browser_test.cjs"
                        )
                    ),
                    json.dumps(cfg),
                ],
                timeout=150,
                env=os.environ,
            )
            assert (
                (wiki / "architecture.md")
                .read_text(encoding="utf-8")
                .startswith("# 架构说明\n\n**异步调用**")
            )
            assert (
                (wiki / "keep.md").read_text(encoding="utf-8").endswith("同步调用。\n")
            )
            assert (
                (wiki / "stale.md").read_text(encoding="utf-8").startswith("# 我的批注")
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
