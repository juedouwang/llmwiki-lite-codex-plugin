"""Knowledge page kinds: classification, grouped directory with icons, and the 项目现状 landing page."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from llmwiki_registry import register_project  # noqa: E402
from research_web_ui import (  # noqa: E402
    KIND_ICONS,
    KNOWLEDGE_KINDS,
    _ICON_PATHS,
    desktop_update_notice,
    knowledge_records,
    page_kind,
    page_type,
    project_page,
    release_version,
    search_label,
)


class PageKindTests(unittest.TestCase):
    def test_declared_type_wins_and_aliases_map(self):
        self.assertEqual(page_kind("knowledge/notes.md", "随便", "experiment"), "experiment")
        self.assertEqual(page_kind("旧板视频内嵌编码协议与深度链路.md", "旧板视频内嵌编码协议与深度链路", "code-analysis"), "architecture")
        self.assertEqual(page_kind("knowledge/a.md", "A", "current-understanding"), "finding")
        # An unknown declared type falls back to the name.
        self.assertEqual(page_kind("knowledge/key-concepts.md", "关键概念", "whatever"), "concept")

    def test_names_and_titles_from_real_projects(self):
        cases = {
            ("knowledge/project-status.md", "项目现状"): "project-status",
            ("knowledge/board-clock-architecture.md", "新旧板时钟信号全景与差异"): "architecture",
            ("knowledge/new-board-encoding-status.md", "新板编码条迁移的当前认识"): "finding",
            ("knowledge/streak-root-cause.md", "横纹根因（已定案）"): "finding",
            ("knowledge/write-latency-test-20260928.md", "写入延迟测试"): "experiment",
            ("knowledge/local-vlm-deployment-baselines.md", "本地 VLM 部署基线"): "experiment",
            ("experiments/run-01.md", "第一次运行"): "experiment",
            ("project-overview.md", "项目总览"): "overview",
            ("相机数据传输链路与示波器调试手册.md", "相机数据传输链路与示波器调试手册"): "guide",
            ("knowledge/visual-git-operation-semantics.md", "可视化 Git 的保存、恢复与远端操作语义"): "concept",
            ("literature-map.md", "低纹理二维/三维匹配文献图谱"): "review",
            ("defect-detection-trends.md", "工业缺陷检测趋势及其与低纹理配准的关系"): "review",
            ("paper-roadmap.md", "低纹理圆柱 RGB-D 退化感知配准论文路线图"): "plan",
            ("patent-strategy.md", "电缆三维数字孪生与 AR 巡检专利策略"): "plan",
            ("knowledge/workbench-report-evidence-boundaries.md", "工作台报告取材与项目边界"): "other",
        }
        for (path, title), kind in cases.items():
            with self.subTest(path):
                self.assertEqual(page_kind(path, title), kind)

    def test_ascii_keywords_match_whole_words_only(self):
        # "latest" contains "test" and "update" contains "data": neither counts.
        self.assertEqual(page_kind("knowledge/latest-update.md", "Latest update"), "other")

    def test_type_is_read_through_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "旧版开发板功能清单与迁移边界.md"
            page.write_bytes("﻿---\r\ntitle: 旧版开发板功能清单\r\ntype: code-analysis\r\n---\r\n\r\n正文\r\n".encode("utf-8"))
            self.assertEqual(page_type(page), "code-analysis")
            self.assertEqual(page_kind(page.name, "旧版开发板功能清单与迁移边界", page_type(page)), "architecture")

    def test_every_kind_has_an_icon(self):
        for kind, _, icon in KNOWLEDGE_KINDS:
            self.assertIn(icon, _ICON_PATHS, kind)
        self.assertEqual(len(set(KIND_ICONS.values())), len(KNOWLEDGE_KINDS))


class KnowledgeDirectoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="llmwiki-kinds-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "source").mkdir()
        self.home = str(root / "home")
        self.wiki = root / "wiki"
        self.project = register_project(str(root / "source"), home=self.home, wiki_root=str(self.wiki))["project"]
        pages = {
            "knowledge/board-clock-architecture.md": "# 新旧板时钟信号全景与差异\n\n正文。\n",
            "knowledge/streak-root-cause.md": "# 横纹根因（已定案）\n\n正文。\n",
            "knowledge/project-status.md": '---\ntitle: "项目现状"\ntype: project-status\n---\n\n# 项目现状\n\n在用 FPGA 0923。\n',
            "overview.md": "# 项目总览\n\n正文。\n",
        }
        for relative, text in pages.items():
            target = self.wiki / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def test_directory_groups_by_kind_with_status_first(self):
        records = knowledge_records(self.project)
        self.assertEqual([item["kind"] for item in records], ["project-status", "overview", "architecture", "finding"])
        page = project_page(self.home, self.project["id"], {})
        # The project opens on its 项目现状, not on the overview.
        self.assertIn("在用 FPGA 0923", page)
        groups = [part.split('"', 1)[0] for part in page.split('class="knowledge-kind" data-kind="')[1:]]
        self.assertEqual(groups, ["project-status", "overview", "architecture", "finding"])
        self.assertIn("<small>项目现状</small>", page)
        self.assertIn('data-kind="architecture" aria-current="false"', page)
        self.assertIn(_ICON_PATHS["kind-finding"], page)

    def test_search_labels_cover_records_and_papers(self):
        status = self.wiki / "knowledge/project-status.md"
        self.assertEqual(search_label("records/2026/10/2026-10-09.md", "x", status), "科研记录")
        self.assertEqual(search_label("literature/a.md", "x", status), "文献")
        self.assertEqual(search_label("knowledge/project-status.md", "项目现状", status), "项目现状")


class DesktopUpdateNoticeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="llmwiki-update-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        (self.home / "desktop").mkdir()

    def notice(self, current, latest, url="https://github.com/juedouwang/llmwiki-lite-codex-plugin/releases/tag/v0.4.1"):
        (self.home / "desktop" / "update.json").write_text(json.dumps({"latest": latest, "url": url}), encoding="utf-8")
        env = {"LLMWIKI_DESKTOP_VERSION": current} if current else {}
        with patch.dict(os.environ, env, clear=False):
            if not current:
                os.environ.pop("LLMWIKI_DESKTOP_VERSION", None)
            return desktop_update_notice(str(self.home))

    def test_newer_release_is_offered_to_release_builds_only(self):
        self.assertIn("v0.4.1", self.notice("0.4.0", "v0.4.1"))
        self.assertIn('target="_blank"', self.notice("0.4.0", "v0.4.1"))
        self.assertEqual(self.notice("0.4.1", "v0.4.1"), "")
        self.assertEqual(self.notice("0.5.0", "v0.4.1"), "")
        # The plugin's own website and source runs never show it.
        self.assertEqual(self.notice("", "v0.4.1"), "")
        self.assertEqual(self.notice("0.4.0", "v0.4.1", url="https://example.com/x"), "")

    def test_release_version_parsing(self):
        self.assertEqual(release_version("v0.4.1"), (0, 4, 1))
        self.assertEqual(release_version("0.4.2-dev.20261009"), (0, 4, 2))
        self.assertIsNone(release_version("latest"))


if __name__ == "__main__":
    unittest.main()
