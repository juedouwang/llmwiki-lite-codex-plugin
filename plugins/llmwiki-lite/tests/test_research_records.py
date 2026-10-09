"""Record day filing, ordering and plain-text excerpts; all state is disposable."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_core import LLMWikiError  # noqa: E402
from markdown_renderer import plain_text, render_markdown  # noqa: E402
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


class RecordLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-links-")
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source"
        self.source.mkdir()
        self.state = str(Path(self.tmp.name) / "state")

    def write(self, title, **fields):
        return write_record(str(self.source), title, fields.pop("understanding", "内容"),
                            recorded_at=fields.pop("at", "2026-09-29T03:00:00Z"), state_root=self.state, **fields)["record"]

    def read(self, record_id):
        return read_record(str(self.source), record_id, state_root=self.state)["record"]

    def listing(self, **kwargs):
        return {item["id"]: item for item in list_records(str(self.source), state_root=self.state, **kwargs)["records"]}

    def test_later_entry_supersedes_and_resolves_without_editing_the_old_one(self):
        old = self.write("横纹是 FIFO 积压", next_steps=["降帧复测", "抓 USB 流"], open_questions=["USB 流格式？"])
        before = self.read(old["id"])["content"]
        new = self.write("横纹根因定案", at="2026-09-29T08:47:00Z", supersedes=[old["ref"]],
                         resolves=[old["ref"] + ".s2", old["ref"] + ".q1"])
        self.assertEqual(self.read(old["id"])["content"], before)
        records = self.listing()
        self.assertEqual(records[old["id"]]["superseded_by"], [new["id"]])
        self.assertEqual(records[old["id"]]["resolved_items"], {"s2": new["id"], "q1": new["id"]})
        self.assertEqual(records[new["id"]]["supersedes"], [old["id"]])
        self.assertEqual(records[new["id"]]["superseded_by"], [])
        # A single read sees the status too, and the new entry says what it replaced.
        self.assertEqual(self.read(old["id"])["superseded_by"], [new["id"]])
        content = self.read(new["id"])["content"]
        self.assertIn("### 取代的旧记录\n\n- 09-29 11:00｜横纹是 FIFO 积压", content)
        self.assertIn("- 抓 USB 流（下一步，来自 09-29 11:00《横纹是 FIFO 积压》）", content)
        self.assertIn("- USB 流格式？（问题，来自", content)

    def test_links_accept_full_ids_and_uppercase_refs(self):
        old = self.write("旧结论", next_steps=["做实验"])
        new = self.write("新结论", at="2026-09-29T04:00:00Z", supersedes=[old["id"]],
                         resolves=[old["ref"].upper() + ".s1"])
        self.assertEqual(self.listing()[old["id"]]["superseded_by"], [new["id"]])
        self.assertEqual(self.listing()[old["id"]]["resolved_items"], {"s1": new["id"]})

    def test_bad_links_fail_before_anything_is_written(self):
        old = self.write("旧结论", next_steps=["做实验"])
        cases = {
            "R000000": {"supersedes": ["R000000"]},
            "只有 1 个下一步": {"resolves": [old["ref"] + ".s2"]},
            "格式不对": {"resolves": [old["ref"]]},
        }
        for message, fields in cases.items():
            with self.subTest(message), self.assertRaisesRegex(LLMWikiError, message):
                self.write("不该写入", at="2026-09-29T05:00:00Z", **fields)
        self.assertEqual(len(self.listing()), 1)

    def test_listing_without_content_omits_item_lists(self):
        self.write("旧结论", next_steps=["做实验"])
        record = next(iter(self.listing().values()))
        self.assertNotIn("next_steps", record)
        self.assertRegex(record["ref"], r"^R[0-9a-f]{6}$")
        record = next(iter(self.listing(include_content=True).values()))
        self.assertEqual(record["next_steps"], ["做实验"])


class RecordStatusPageTests(unittest.TestCase):
    def setUp(self):
        from llmwiki_registry import register_project

        tmp = tempfile.TemporaryDirectory(prefix="llmwiki-status-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "source").mkdir()
        self.home = str(root / "home")
        self.project = register_project(str(root / "source"), home=self.home, wiki_root=str(root / "wiki"))["project"]

    def write(self, title, at, **fields):
        return write_record(self.project["source_root"], title, "内容", recorded_at=at,
                            state_root=self.project["state_root"], **fields)["record"]

    def test_pages_and_task_candidates_follow_record_status(self):
        from research_progress import candidates
        from research_web_ui import record_view, records_page

        old = self.write("横纹是 FIFO 积压", "2026-09-29T03:00:00Z", next_steps=["降帧复测"])
        keep = self.write("深度软件按 ID 选标定", "2026-09-29T04:00:00Z",
                          next_steps=["试 ID7 标定", "看散斑亮度"], open_questions=["投射器型号？"])
        new = self.write("横纹根因定案", "2026-09-29T08:00:00Z", supersedes=[old["ref"]],
                         resolves=[keep["ref"] + ".s1"])

        page = record_view(self.home, self.project["id"], old["id"])
        self.assertIn('class="notice record-status"', page)
        self.assertIn("这条记录的结论已被取代", page)
        self.assertIn("横纹根因定案", page)
        page = record_view(self.home, self.project["id"], keep["id"])
        self.assertIn("<del>试 ID7 标定</del>（已完成）", page)
        self.assertIn("下一步 1 已在", page)
        self.assertNotIn("<del>看散斑亮度</del>", page)
        self.assertIn("这条记录取代了", record_view(self.home, self.project["id"], new["id"]))

        listing = records_page(self.home, self.project["id"], {})
        self.assertEqual(listing.count("is-superseded"), 1)
        self.assertIn("<span>已被取代</span>", listing)

        titles = [item["title"] for item in candidates(self.project)]
        self.assertEqual(sorted(titles), ["投射器型号？", "看散斑亮度"])


class PlainTextTests(unittest.TestCase):
    def test_excerpt_drops_markup_but_keeps_reading_order(self):
        text = ("---\ntitle: x\n---\n# 标题\n<!-- llmwiki-record:{\"a\":1} -->\n## 进展\n"
                "- **已完成** 3.1 节，见 [[notes/a|笔记]] 与 [链接](http://x)\n![](a.png)\n"
                "```text\nraw\n```\n| 列 | 值 |\n| --- | --- |\n> 引用")
        self.assertEqual(plain_text(text), "标题：进展：已完成 3.1 节，见 笔记 与 链接 [图片] raw 列 值 引用")

    def test_identifiers_keep_their_underscores(self):
        self.assertEqual(plain_text("调用 snake_case_name 和 2*3*4"), "调用 snake_case_name 和 2*3*4")
        self.assertEqual(plain_text("见 a__b__c"), "见 a__b__c")


class RenderTests(unittest.TestCase):
    def test_underscores_inside_words_are_not_emphasis(self):
        # Seen in a real record: everything between the two names went italic.
        html = render_markdown("ROI 在 param/camera_params.json 里；参考散斑是 param/rgbd_11.data。", "p", "r.md")
        self.assertNotIn("<em>", html)
        self.assertIn("camera_params.json", html)
        self.assertIn("rgbd_11.data", html)
        self.assertNotIn("<strong>", render_markdown("a__b__c", "p", "r.md"))

    def test_standalone_emphasis_still_renders(self):
        html = render_markdown("这是 _推测_，那是 __已验证__，还有 *斜体*。", "p", "r.md")
        self.assertIn("<em>推测</em>", html)
        self.assertIn("<strong>已验证</strong>", html)
        self.assertIn("<em>斜体</em>", html)


if __name__ == "__main__":
    unittest.main()
