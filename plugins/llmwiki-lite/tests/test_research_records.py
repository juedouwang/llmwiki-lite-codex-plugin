"""Record day filing, ordering and plain-text excerpts; all state is disposable."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from markdown_renderer import plain_text  # noqa: E402
from research_records import list_records, read_record, write_record  # noqa: E402
from research_web_ui import _record_day, format_time  # noqa: E402


class RecordTimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-records-")
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source"
        self.source.mkdir()
        self.state = str(Path(self.tmp.name) / "state")

    def write(self, title, recorded_at):
        return write_record(str(self.source), title, "内容", recorded_at=recorded_at, state_root=self.state)["record"]

    def test_utc_evening_files_under_the_beijing_day(self):
        record = self.write("凌晨记录", "2026-09-23T16:52:00Z")
        self.assertEqual(record["path"], "records/2026/09/2026-09-24.md")
        content = read_record(str(self.source), record["id"], state_root=self.state)["record"]["content"]
        self.assertIn("00:52", content.splitlines()[0])
        # Stored stamps stay UTC; only filing and display follow Beijing time.
        self.assertEqual(record["recorded_at"], "2026-09-23T16:52:00Z")
        self.assertEqual(_record_day(record)[0], "2026-09-24")
        self.assertEqual(format_time(record["recorded_at"]), "2026-09-24 00:52")

    def test_explicit_beijing_offsets_keep_their_day(self):
        self.assertEqual(self.write("上午", "2026-09-19T09:00:00+08:00")["path"], "records/2026/09/2026-09-19.md")

    def test_list_orders_mixed_offsets_by_real_time(self):
        self.write("较早", "2026-09-23T15:00:00Z")
        self.write("中间", "2026-09-24T00:10:00+08:00")
        self.write("最新", "2026-09-23T16:52:00Z")
        titles = [item["title"] for item in list_records(str(self.source), state_root=self.state)["records"]]
        self.assertEqual(titles, ["最新", "中间", "较早"])


class PlainTextTests(unittest.TestCase):
    def test_excerpt_drops_markup_but_keeps_reading_order(self):
        text = ("---\ntitle: x\n---\n# 标题\n<!-- llmwiki-record:{\"a\":1} -->\n## 进展\n"
                "- **已完成** 3.1 节，见 [[notes/a|笔记]] 与 [链接](http://x)\n![](a.png)\n"
                "```text\nraw\n```\n| 列 | 值 |\n| --- | --- |\n> 引用")
        self.assertEqual(plain_text(text), "标题：进展：已完成 3.1 节，见 笔记 与 链接 [图片] raw 列 值 引用")

    def test_identifiers_keep_their_underscores(self):
        self.assertEqual(plain_text("调用 snake_case_name 和 2*3*4"), "调用 snake_case_name 和 2*3*4")


if __name__ == "__main__":
    unittest.main()
