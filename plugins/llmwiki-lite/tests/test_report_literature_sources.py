"""Integration with saved project records; no account chat or real automation."""

from pathlib import Path
from unittest.mock import patch
from test_literature_support import Fixture, NOW
import research_reports as reports
import literature_collection as collection
from research_records import write_record


class SavedLiteratureSourceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.settings()
        self.record = write_record(
            self.project["source_root"],
            "Paper discussion",
            "论文 https://doi.org/10.1234/test",
            recorded_at="2026-08-02T10:00:00+08:00",
            project_id=self.pid,
            state_root=self.project["state_root"],
        )["record"]

    def test_saved_provider_does_not_truncate_to_report_window(self):
        provider = collection.ReportSourceProvider()
        self.assertTrue(provider.available)
        result = collection.literature_plan(self.home, now=NOW)
        self.assertEqual(len(result["runs"]), 1)
        run = result["runs"][0]
        page = collection.literature_sources(run["run_id"], home=self.home, now=NOW)
        item = page["items"][0]
        self.assertIn("2026-08-02", item["occurred_at"])
        self.assertIn("10.1234/test", item["text"])
        candidate = {
            "locator": "10.1234/test",
            "source_ids": [item["id"]],
            "evidence": {item["id"]: "https://doi.org/10.1234/test"},
        }
        result = collection.literature_finish(
            run["run_id"], "reviewed", [candidate], home=self.home, now=NOW
        )
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(self.catalog.list_items()["count"], 1)

    def test_scope_reports_unknown_dates_and_redaction(self):
        wiki = Path(self.project["wiki_root"])
        (wiki / "records/reports").mkdir()
        (wiki / "records/reports/fake.md").write_text(
            "https://doi.org/10.1234/forbidden", encoding="utf-8"
        )
        (wiki / "records/unknown.md").write_text(
            "# No trusted time\nhttps://doi.org/10.1234/unknown", encoding="utf-8"
        )
        items, gaps = reports.literature_inventory(
            self.project, start_date="2026-08-01", now=NOW, home=self.home
        )
        self.assertEqual(len(items), 1)
        self.assertTrue(any("对话未接入" in g for g in gaps))
        self.assertTrue(any("可信时间" in g for g in gaps))
        self.assertFalse(
            reports.literature_authorize_source(
                self.other, items[0], start_date="2026-08-01", now=NOW, home=self.home
            )
        )
        altered = dict(items[0], locator="records/../../secret.md")
        self.assertFalse(
            reports.literature_authorize_source(
                self.project, altered, start_date="2026-08-01", now=NOW, home=self.home
            )
        )

    def test_deleted_not_unreadable_and_late_saved_record(self):
        items, _ = reports.literature_inventory(
            self.project, start_date="2026-08-01", now=NOW, home=self.home
        )
        target = Path(self.project["wiki_root"]) / self.record["path"]
        original_read = Path.read_text

        def selective_read(path, *args, **kwargs):
            if path == target:
                raise PermissionError("fixture")
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read):
            with self.assertRaises(PermissionError):
                reports.literature_read_source(
                    self.project,
                    items[0],
                    start_date="2026-08-01",
                    now=NOW,
                    home=self.home,
                )
        target.unlink()
        self.assertIsNone(
            reports.literature_read_source(
                self.project, items[0], start_date="2026-08-01", now=NOW, home=self.home
            )
        )

    def test_mcp_four_tools_and_explicit_project(self):
        from mcp_server import dispatch, TOOL_NAMES

        self.assertTrue(
            {
                "llmwiki_literature_collect",
                "llmwiki_literature_plan",
                "llmwiki_literature_sources",
                "llmwiki_literature_finish",
            }.issubset(TOOL_NAMES)
        )
        with self.assertRaises(Exception):
            dispatch(
                "llmwiki_literature_collect",
                {"locator": "10.1234/test", "request_id": "a" * 32},
            )
        result = dispatch(
            "llmwiki_literature_collect",
            {
                "project_id": self.pid,
                "locator": "10.1234/test",
                "request_id": "a" * 32,
                "home": self.home,
            },
        )
        self.assertEqual(result["action"], "created")
