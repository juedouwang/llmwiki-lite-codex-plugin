"""Optional browser harness. Uses an existing Node/Playwright install, never installs one."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402
from llmwiki_core import wiki_write  # noqa: E402
from research_records import write_record  # noqa: E402

with tempfile.TemporaryDirectory(prefix="llmwiki-browser-") as folder:
    root = Path(folder)
    (root / "source").mkdir()
    home = str(root / "home")
    project = register_project(
        str(root / "source"),
        name="科研笔记体验测试",
        home=home,
        wiki_root=str(root / "wiki"),
    )["project"]
    wiki_write(str(root / "source"), "experiment.md", "# 低纹理配准实验\n\n## 实验设置\n固定随机种子，比较基线与改进模型。\n\n| 方法 | 误差 |\n| --- | --- |\n| 基线 | 0.082 |\n| 改进 | 0.046 |\n", state_root=project["state_root"])
    refs = root / "source" / "references"
    refs.mkdir()
    # Sufficient for route/viewer tests; no PDF parsing dependency required.
    (refs / "demo-paper.pdf").write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
    wiki_write(str(root / "source"), "paper-reading.md", "---\ntitle: 几何约束方法精读\ntype: literature-note\npaper_file: references/demo-paper.pdf\n---\n\n# 几何约束方法精读\n\n## 关键结论\n需要复现实验验证。\n", state_root=project["state_root"])
    write_record(str(root / "source"), "实验设计与阶段结论", "弱纹理场景仍需补充跨场景验证。", tags=["实验"], next_steps=["补充夜间数据", "运行消融实验"], related_pages=["experiment.md"], state_root=project["state_root"])
    server = create_server(home, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        site_result = subprocess.run([
            os.environ.get("NODE", "node"),
            str(Path(__file__).with_name("site_browser_test.cjs")),
            f"http://127.0.0.1:{server.server_port}", project["id"],
        ], timeout=120)
        if site_result.returncode:
            raise SystemExit(site_result.returncode)
        result = subprocess.run(
            [
                os.environ.get("NODE", "node"),
                str(Path(__file__).with_name("notebook_browser_test.cjs")),
                f"http://127.0.0.1:{server.server_port}",
                project["id"],
            ],
            timeout=150,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    sys.exit(result.returncode)
