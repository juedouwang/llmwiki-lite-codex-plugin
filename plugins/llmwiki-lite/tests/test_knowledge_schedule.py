"""Fake-clock tests. Runtime receipts here are test fixtures, not real bindings."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch
from test_knowledge_maintenance import KnowledgeFixture
import knowledge_maintenance as km
from llmwiki_registry import register_project
from research_reports import report_settings, save_settings


class KnowledgeScheduleTests(KnowledgeFixture):
    def setUp(self):
        super().setUp()
        self.clock = datetime(2026, 9, 19, 14, tzinfo=timezone.utc)
        self.patch = patch.object(km, "now", side_effect=lambda: self.clock)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config = {
            "schema_version": 1,
            "enabled": True,
            "knowledge_enabled": True,
            "project_ids": [self.p["id"]],
            "timezone": "Asia/Shanghai",
            "weekly_time": "21:30",
            "weekly_weekday": 5,
            "weekly_owner_project_id": self.p["id"],
            "daily_time": "21:00",
            "start_date": "2026-09-19",
            "runtime": {
                "automation_id": "fixture-not-real",
                "target_thread_id": "fixture-not-real",
                "knowledge_bound_at": "2026-09-19T00:00:00Z",
            },
        }
        self.settings()

    def settings(self):
        km.write_json(Path(self.home) / "reports-settings.json", self.config)

    def scheduled(self):
        return km.knowledge_plan("scheduled", home=self.home)

    def test_paused_unbound_and_manual(self):
        self.config["knowledge_enabled"] = False
        self.settings()
        self.assertIsNone(self.scheduled()["run_id"])
        run = self.plan()
        self.assertIsNotNone(run)
        self.finish(run)
        self.config["knowledge_enabled"] = True
        self.config["runtime"].pop("knowledge_bound_at")
        self.settings()
        self.assertIsNone(self.scheduled()["run_id"])

    def test_daily_slot_ignores_late_file_until_next_day(self):
        run = self.scheduled()["run_id"]
        self.finish(run)
        (self.source / "new.py").write_text("new", encoding="utf-8")
        self.assertIsNone(self.scheduled()["run_id"])
        self.clock += timedelta(days=4)
        run = self.scheduled()["run_id"]
        self.assertIsNotNone(run)
        self.finish(run)
        self.assertIsNone(self.scheduled()["run_id"])

    def test_before_due(self):
        self.clock = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        self.assertEqual(self.scheduled()["reason"], "not_due")

    def test_stop_during_run(self):
        run = self.scheduled()["run_id"]
        self.read(run)
        self.config["enabled"] = False
        self.settings()
        with self.assertRaises(km.KnowledgeError) as c:
            self.finish(run)
        self.assertEqual(c.exception.code, "PAUSED")
        self.assertFalse(km.state(self.p)["sources"])

    def test_expiration_busy_and_same_finished_receipt(self):
        run = self.plan()
        self.assertIsNone(self.plan())
        self.clock += timedelta(minutes=11)
        with self.assertRaises(km.KnowledgeError):
            self.finish(run)
        run = self.plan()
        result = self.finish(run)
        self.clock += timedelta(days=5)
        self.assertEqual(km.knowledge_finish(run, "reviewed", home=self.home), result)

    def test_retry_delay_limit_changed_material_resets(self):
        for attempt in range(3):
            run = self.scheduled()["run_id"]
            self.assertIsNotNone(run)
            km.knowledge_finish(run, "failed", error_code="HOST_FAILED", home=self.home)
            self.assertIsNone(self.scheduled()["run_id"])
            self.clock += timedelta(minutes=31)
        self.assertIsNone(self.scheduled()["run_id"])
        (self.source / "main.py").write_text("changed", encoding="utf-8")
        self.assertIsNotNone(self.scheduled()["run_id"])

    def test_fairness_and_batch_catchup(self):
        other = self.root / "other"
        other.mkdir()
        (other / "x.py").write_text("x")
        p = register_project(str(other), home=self.home)["project"]
        self.config["project_ids"].append(p["id"])
        self.settings()
        for i in range(25):
            (self.source / f"x{i:02}.py").write_text("x")
        first = self.scheduled()
        self.read(first["run_id"])
        self.finish(first["run_id"])
        second = self.scheduled()
        self.assertNotEqual(first["project_id"], second["project_id"])
        self.finish(second["run_id"])
        third = self.scheduled()
        self.assertIsNotNone(third["run_id"])
        self.finish(third["run_id"])
        self.assertIsNone(self.scheduled()["run_id"])

    def test_paused_runtime_revokes_new_writes(self):
        run = self.scheduled()["run_id"]
        items = self.read(run)
        self.config["runtime"]["status"] = "PAUSED"
        self.settings()
        with self.assertRaises(km.KnowledgeError):
            km.knowledge_finish(
                run, "reviewed", [i["source_id"] for i in items], [], home=self.home
            )

    def test_setting_intent_does_not_forge_runtime(self):
        before = report_settings(self.home)
        after = save_settings(
            {
                "expected_revision": before["revision"],
                "knowledge_enabled": False,
                "literature_enabled": True,
            },
            self.home,
        )
        self.assertEqual(after["runtime"], before["runtime"])
        with self.assertRaises(Exception):
            save_settings(
                {
                    "expected_revision": after["revision"],
                    "runtime": {"knowledge_bound_at": "fake"},
                },
                self.home,
            )


if __name__ == "__main__":
    unittest.main()
