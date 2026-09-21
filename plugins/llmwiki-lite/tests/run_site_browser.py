"""Run site regression without notebook/reports suites, using only disposable projects."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from git_service import _run_git_command  # noqa: E402
from llmwiki_core import wiki_write  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_records import write_record  # noqa: E402
from web_server import create_server  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prototype", type=Path, help="Read-only approved HTML for visual comparison")
    parser.add_argument("--refinements", action="store_true")
    parser.add_argument("--alignment", action="store_true")
    args = parser.parse_args()
    if args.prototype and not args.prototype.is_file():
        parser.error("prototype must be an existing HTML file")
    with tempfile.TemporaryDirectory(prefix="llmwiki-site-") as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        home = str(root / "home")
        os.environ.update(GIT_CONFIG_GLOBAL=str(root / "global-empty"), GIT_CONFIG_NOSYSTEM="1")
        project = register_project(str(source), name="科研笔记体验测试", home=home,
                                   wiki_root=str(root / "wiki"), state_root=str(root / "state"))["project"]
        other = root / "other"
        other.mkdir()
        register_project(str(other), name="切换验收项目", home=home,
                         wiki_root=str(root / "other-wiki"), state_root=str(root / "other-state"))
        wiki_write(str(source), "experiment.md", "# 低纹理配准实验\n\n## 实验设置\n固定随机种子，比较基线与改进模型。\n", state_root=project["state_root"])
        refs = source / "references"
        refs.mkdir()
        (refs / "demo-paper.pdf").write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
        wiki_write(str(source), "paper-reading.md", "---\ntitle: 几何约束方法精读\ntype: literature-note\npaper_file: references/demo-paper.pdf\n---\n\n# 几何约束方法精读\n\n## 关键结论\n需要复现实验验证。\n", state_root=project["state_root"])
        write_record(str(source), "实验设计与阶段结论", "弱纹理场景仍需补充跨场景验证。",
                     tags=["实验"], next_steps=["补充夜间数据", "运行消融实验"],
                     related_pages=["experiment.md"], state_root=project["state_root"])

        def git(*argv):
            _run_git_command("git", list(argv), cwd=source, timeout=20)

        git("init", "-b", "main")
        git("config", "core.autocrlf", "false")
        git("config", "user.name", "刘亚宁")
        git("config", "user.email", "visual@example.test")
        (source / "configs").mkdir()
        (source / "src").mkdir()
        (source / "configs/camera.yaml").write_text(" camera:\n  exposure_ms: 6.0\n  auto_exposure: true\n   fps: 30\n", encoding="utf-8")
        (source / "src/capture.py").write_text("def open_camera(config):\n    old_one()\n    old_two()\n    old_three()\n    return camera\n", encoding="utf-8")
        titles = ["建立缺陷检测基线", "完成相机采集与标定", "加入 ROI 稳定性实验",
                  "修正标定参数读取", "验证低纹理区域跟踪", "完善缺陷结果导出"]
        for index, title in enumerate(titles):
            if index == 3:
                (source / "configs/camera.yaml").write_text(" camera:\n  exposure_ms: 8.0\n  auto_exposure: false\n   fps: 30\n", encoding="utf-8")
                (source / "src/capture.py").write_text("def open_camera(config):\n" + "".join(f"    step_{n}()\n" for n in range(8)) + "    return camera\n", encoding="utf-8")
            else:
                (source / "research.txt").write_text(f"阶段 {index}\n", encoding="utf-8")
            git("add", ".")
            git("commit", "--date=2026-09-20T16:42:00+08:00", "-m", title)
        server = create_server(home, port=0)
        # Join active request threads before deleting the disposable Git fixture.
        server.daemon_threads = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        tests = [("site_browser_test.cjs", [])]
        if args.prototype:
            tests.append(("site_prototype_browser_test.cjs", [str(args.prototype.resolve())]))
        if args.alignment:
            tests.append(("workbench_alignment_browser_test.cjs", [str(args.prototype.resolve())] if args.prototype else []))
        if args.refinements:
            tests.append(("workbench_refinements_browser_test.cjs", []))
        try:
            for name, extra in tests:
                result = subprocess.run(
                    [os.environ.get("NODE", "node"), str(Path(__file__).with_name(name)), origin, project["id"], *extra],
                    timeout=180, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, file=sys.stderr, end="")
                if result.returncode:
                    return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
