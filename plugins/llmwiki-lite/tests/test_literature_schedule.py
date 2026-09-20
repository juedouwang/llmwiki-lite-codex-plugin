"""Fake-clock daily slots, bounded pages, failures and fairness."""
from datetime import timedelta
import unittest
from unittest.mock import patch

from test_literature_support import Fixture, MemoryProvider, NOW
from literature_catalog import LiteratureCatalogError
import literature_collection as collection


class ScheduleTests(Fixture):
    def setUp(self):
        super().setUp()
        self.settings()
        self.provider = MemoryProvider([self.source()])

    def plan(self, now=NOW, max_projects=3):
        return collection.literature_plan(self.home, max_projects, now=now, source_provider=self.provider)

    def review(self, run, now=NOW):
        cursor = None
        while True:
            page = collection.literature_sources(run["run_id"], cursor, self.home, now=now, source_provider=self.provider)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        return collection.literature_finish(run["run_id"], "reviewed", candidates=[], home=self.home, now=now, source_provider=self.provider)

    def test_before_daily_time_and_same_day_late_material_waits(self):
        self.assertEqual(self.plan(NOW - timedelta(hours=2))["runs"], [])
        run = self.plan()["runs"][0]
        self.review(run)
        self.provider.sources.append(self.source(locator="record:late"))
        self.assertEqual(self.plan(NOW + timedelta(minutes=30))["runs"], [])
        later = self.plan(NOW + timedelta(days=1))["runs"]
        self.assertEqual(len(later), 1)
        page = collection.literature_sources(later[0]["run_id"], home=self.home, now=NOW + timedelta(days=1), source_provider=self.provider)
        self.assertEqual(page["items"][0]["locator"], "record:late")

    def test_unchanged_no_model_and_offline_beyond_report_14_days(self):
        self.provider.sources[0] = self.source(occurred_at="2026-08-02T12:00:00+00:00")
        run = self.plan()["runs"][0]
        self.review(run)
        self.assertEqual(self.plan(NOW + timedelta(days=5))["runs"], [])
        self.provider.sources.append(self.source(locator="record:old-unchecked", occurred_at="2026-08-03T12:00:00+00:00"))
        self.assertEqual(len(self.plan(NOW + timedelta(days=6))["runs"]), 1)

    def test_long_source_parts_read_all_before_finish_and_crossday_pending(self):
        self.provider.sources[0] = self.source("x" * 260001)
        run = self.plan()["runs"][0]
        page = collection.literature_sources(run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
        self.assertLessEqual(sum(len(i["text"]) for i in page["items"]), 20000)
        self.assertEqual(page["items"][0]["part_count"], 14)
        with self.assertRaises(LiteratureCatalogError) as ctx:
            collection.literature_finish(run["run_id"], "reviewed", [], home=self.home, now=NOW, source_provider=self.provider)
        self.assertEqual(ctx.exception.code, "UNREAD_SOURCES")
        self.review(run)
        state = collection._state(self.project)
        checked = next(iter(state["checked_sources"].values()))
        self.assertNotIn("checked_revision", checked)
        self.assertEqual(len(checked["done_parts"]), 5)
        second_time = NOW + timedelta(days=2)
        second = self.plan(second_time)["runs"][0]
        self.review(second, second_time)
        third = self.plan(second_time + timedelta(minutes=30))["runs"][0]
        self.review(third, second_time + timedelta(minutes=30))
        self.assertEqual(collection._state(self.project)["pending_sources"], [])
        self.assertIn("checked_revision", next(iter(collection._state(self.project)["checked_sources"].values())))

    def test_pages_stable_and_cursor_cannot_be_path(self):
        self.provider.sources[0] = self.source("a" * 42000)
        run = self.plan()["runs"][0]
        first = collection.literature_sources(run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
        reads = self.provider.read_count
        again = collection.literature_sources(run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
        self.assertEqual(first, again)
        self.assertEqual(self.provider.read_count, reads)
        for cursor in ("../secret", "C:/secret", 1):
            with self.assertRaises(LiteratureCatalogError):
                collection.literature_sources(run["run_id"], cursor, self.home, now=NOW, source_provider=self.provider)

    def test_claim_expires_and_retry_after_30_minutes(self):
        run = self.plan()["runs"][0]
        self.assertEqual(self.plan()["runs"], [])
        with self.assertRaises(LiteratureCatalogError):
            collection.literature_sources(run["run_id"], home=self.home, now=NOW + timedelta(minutes=11), source_provider=self.provider)
        self.assertEqual(self.plan(NOW + timedelta(minutes=11))["runs"], [])
        self.assertEqual(self.plan(NOW + timedelta(minutes=39))["runs"], [])
        again = self.plan(NOW + timedelta(minutes=40))["runs"][0]
        self.assertNotEqual(run["run_id"], again["run_id"])

    def test_three_attempts_then_manual_retry_and_new_revision_resets(self):
        for attempt in range(3):
            now = NOW + timedelta(minutes=30 * attempt)
            run = self.plan(now)["runs"][0]
            collection.literature_finish(run["run_id"], "failed", error_code="MODEL_FAILED", home=self.home, now=now, source_provider=self.provider)
        self.assertEqual(self.plan(NOW + timedelta(minutes=90))["runs"], [])
        self.assertEqual(collection._state(self.project)["failure_count"], 3)
        collection.collection_retry(self.pid, self.home)
        self.assertEqual(len(self.plan(NOW + timedelta(minutes=90))["runs"]), 1)

    def test_queue_revision_change_resets_parts_and_deleted_is_gap(self):
        self.provider.sources[0] = self.source("x" * 120000)
        run = self.plan()["runs"][0]
        self.review(run)
        self.provider.sources[0] = self.source("changed content")
        next_run = self.plan(NOW + timedelta(minutes=30))["runs"][0]
        page = collection.literature_sources(next_run["run_id"], home=self.home, now=NOW + timedelta(minutes=30), source_provider=self.provider)
        self.assertEqual(page["items"][0]["part_index"], 0)
        self.assertTrue(any("旧版本" in gap for gap in page["gaps"]))
        self.review(next_run, NOW + timedelta(minutes=30))
        self.assertEqual(collection._state(self.project)["pending_sources"], [])

    def test_unreadable_not_deleted_and_unknown_date_gap(self):
        self.provider.read_error = True
        self.assertEqual(self.plan()["runs"], [])
        state = collection._state(self.project)
        self.assertEqual(state["last_result"], "failed")
        self.assertTrue(state["pending_sources"])
        self.assertEqual(state["checked_sources"], {})
        self.provider.read_error = False
        self.assertEqual(len(self.plan(NOW + timedelta(minutes=30))["runs"]), 1)

    def test_fairness_maximum_three_and_no_source_type_expansion(self):
        self.provider.sources.append(self.source(project_id=self.other["id"], locator="record:beta"))
        first = self.plan(max_projects=1)["runs"][0]
        second = self.plan(NOW + timedelta(minutes=1), max_projects=1)["runs"][0]
        self.assertNotEqual(first["project_id"], second["project_id"])
        with self.assertRaises(LiteratureCatalogError):
            self.plan(max_projects=4)

    def test_start_date_unknown_time_and_excluded_source(self):
        self.provider.sources = [self.source(occurred_at=None, observed_at=None), self.source(locator="old", occurred_at="2026-07-01T00:00:00+00:00")]
        result = self.plan()
        self.assertEqual(result["runs"], [])
        self.assertTrue(any("时间" in gap["message"] for gap in result["gaps"]))
        self.assertEqual(collection._state(self.project)["checked_sources"], {})


class FailureBudgetTests(Fixture):
    plan = ScheduleTests.plan

    def setUp(self):
        super().setUp()
        self.settings()
        self.provider = MemoryProvider([self.source()])
    def single_project(self):
        self.settings(project_ids=[self.pid])

    def test_inventory_failure_budget_is_stable_across_days_and_manual_retry(self):
        self.single_project()
        with patch.object(self.provider, "inventory", side_effect=OSError("private fixture details")) as inventory:
            fingerprint = None
            for minute, attempts in ((0, 1), (29, 1), (30, 2), (59, 2), (60, 3), (90, 3), (1440, 3)):
                self.assertEqual(self.plan(NOW + timedelta(minutes=minute))["runs"], [])
                state = collection._state(self.project)
                self.assertEqual(inventory.call_count, attempts)
                self.assertEqual(state["failure_count"], attempts)
                self.assertIsNone(state["last_daily_slot"])
                self.assertIsNone(state["last_checked_at"])
                self.assertEqual(state["checked_sources"], {})
                self.assertIsNone(state["active_run"])
                self.assertEqual(state["last_error"], "READ_FAILED")
                if fingerprint:
                    self.assertEqual(state["failure_fingerprint"], fingerprint)
                fingerprint = state["failure_fingerprint"]
            with patch.object(collection, "_now", return_value=NOW + timedelta(days=1)):
                collection.collection_retry(self.pid, self.home)
            self.assertEqual(self.plan(NOW + timedelta(days=1))["runs"], [])
            self.assertEqual(inventory.call_count, 4)
            self.assertEqual(collection._state(self.project)["failure_count"], 1)
        self.assertEqual(self.provider.read_count, 0)

    def test_read_budget_blocks_helpers_until_due_and_revision_resets_ceiling(self):
        self.single_project()
        self.provider.read_error = True
        for minute, attempts in ((0, 1), (29, 1), (30, 2), (59, 2), (60, 3), (90, 3)):
            result = self.plan(NOW + timedelta(minutes=minute))
            self.assertEqual(result["runs"], [])
            self.assertEqual(result["pending_count"], 1)
            self.assertEqual(self.provider.read_count, attempts)
            self.assertEqual(collection._state(self.project)["failure_count"], attempts)
        # Re-reading time and a late, unrelated source must not defeat the budget.
        self.provider.sources[0]["observed_at"] = (NOW + timedelta(minutes=91)).isoformat()
        self.provider.sources.append(self.source(locator="record:late"))
        self.provider.read_error = False
        self.assertEqual(self.plan(NOW + timedelta(minutes=91))["runs"], [])
        self.assertEqual(self.provider.read_count, 3)
        self.provider.sources[0] = self.source("new revision doi:10.1234/test")
        run = self.plan(NOW + timedelta(minutes=92))["runs"][0]
        self.assertEqual(collection._state(self.project)["failure_count"], 0)
        page = collection.literature_sources(run["run_id"], home=self.home, now=NOW + timedelta(minutes=92), source_provider=self.provider)
        self.assertEqual([s["locator"] for s in page["items"]], ["record:one"])

    def test_read_and_model_failures_share_one_input_budget(self):
        self.single_project()
        self.provider.read_error = True
        self.plan()
        fingerprint = collection._state(self.project)["failure_fingerprint"]
        self.provider.read_error = False
        now = NOW + timedelta(minutes=30)
        run = self.plan(now)["runs"][0]
        self.assertEqual(run["input_fingerprint"], fingerprint)
        collection.literature_finish(run["run_id"], "failed", error_code="MODEL_FAILED", home=self.home, now=now, source_provider=self.provider)
        self.assertEqual(collection._state(self.project)["failure_count"], 2)
        self.provider.read_error = True
        self.plan(NOW + timedelta(minutes=60))
        self.assertEqual(collection._state(self.project)["failure_count"], 3)
        self.assertEqual(self.plan(NOW + timedelta(minutes=90))["runs"], [])
        self.assertEqual(self.provider.read_count, 3)

    def test_failed_inventory_probe_does_not_bypass_existing_read_budget(self):
        self.single_project()
        self.provider.read_error = True
        self.plan()
        with patch.object(self.provider, "inventory", side_effect=OSError("probe failed")) as inventory:
            self.plan(NOW + timedelta(minutes=1))
            self.assertEqual(collection._state(self.project)["failure_count"], 1)
            for minute in (2, 10, 29):
                self.plan(NOW + timedelta(minutes=minute))
            self.assertEqual(inventory.call_count, 1)
            self.plan(NOW + timedelta(minutes=30))
            self.assertEqual(collection._state(self.project)["failure_count"], 2)
            self.plan(NOW + timedelta(minutes=60))
            self.plan(NOW + timedelta(minutes=90))
            self.plan(NOW + timedelta(days=1))
            self.assertEqual(inventory.call_count, 3)
            self.assertEqual(collection._state(self.project)["failure_count"], 3)
        self.assertEqual(collection._state(self.project)["checked_sources"], {})

    def test_new_revision_resets_cooldown_without_manual_retry(self):
        self.single_project()
        self.provider.read_error = True
        self.plan()
        self.provider.sources[0] = self.source("changed doi:10.1234/test")
        self.provider.read_error = False
        self.assertEqual(len(self.plan(NOW + timedelta(minutes=1))["runs"]), 1)

    def test_expiration_three_attempts_and_status_projection_do_not_double_charge(self):
        self.single_project()
        for attempt in range(3):
            now = NOW + timedelta(minutes=40 * attempt)
            run = self.plan(now)["runs"][0]
            at_expiry = now + timedelta(minutes=10)
            state_path = collection._path(self.project, "state.json")
            before = state_path.read_bytes()
            with patch.object(collection, "_now", return_value=at_expiry):
                for _ in range(2):
                    status = collection.collection_status(self.pid, self.home)
                    self.assertEqual(status["status"], "failed")
                    self.assertEqual(status["last_error"], "RUN_EXPIRED")
                    self.assertEqual(status["failure_count"], attempt + 1)
            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(self.plan(at_expiry)["runs"], [])
            self.assertEqual(self.plan(at_expiry)["runs"], [])
            self.assertEqual(collection._state(self.project)["failure_count"], attempt + 1)
            saved = collection._read(collection._path(self.project, f"runs/{run['run_id']}.json"), {})
            self.assertNotIn("items", saved)
        self.assertEqual(self.plan(NOW + timedelta(minutes=120))["runs"], [])

    def test_pending_count_includes_queues_after_project_limit(self):
        for project in self.projects:
            state = collection._state(project)
            source = self.source(project_id=project["id"])
            state["pending_sources"] = [{**collection._meta(source), "done_parts": []}]
            state["last_daily_slot"] = NOW.date().isoformat()
            collection._save_state(project, state)
        self.provider.sources.append(self.source(project_id=self.other["id"]))
        self.assertEqual(self.plan(max_projects=1)["pending_count"], 2)


if __name__ == "__main__":
    unittest.main()
