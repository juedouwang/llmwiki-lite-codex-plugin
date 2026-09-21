"""Lease fencing and crash replay: temporary registries only, no real jobs/data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch

from test_literature_support import Fixture, MemoryProvider, NOW
from literature_catalog import LiteratureCatalogError, item_revision
import literature_collection as collection


class RecoveryTests(Fixture):
    def setUp(self):
        super().setUp()
        self.settings(project_ids=[self.pid])
        self.provider = MemoryProvider([self.source()])

    def plan(self, now=NOW, provider=None):
        return collection.literature_plan(self.home, now=now, source_provider=provider or self.provider)

    def prepare(self):
        run = self.plan()["runs"][0]
        page = collection.literature_sources(run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
        item = page["items"][0]
        candidate = {"locator": "10.1234/test", "source_ids": [item["id"]], "evidence": {item["id"]: item["text"]}}
        return run, [candidate]

    def finish(self, run, candidates, now=NOW):
        return collection.literature_finish(run["run_id"], "reviewed", candidates, home=self.home, now=now, source_provider=self.provider)

    def assert_lock_available(self, executor):
        def probe():
            with collection._lock(self.project):
                return True
        self.assertTrue(executor.submit(probe).result(timeout=2))

    def test_slow_inventory_and_read_leave_lock_free_and_fence_duplicate_plans(self):
        for method in ("inventory", "read", "authorize"):
            with self.subTest(helper=method):
                state = collection._state(self.project)
                state.update(active_run=None, pending_sources=[], last_daily_slot=None)
                collection._save_state(self.project, state)
                entered, release = Event(), Event()
                original = getattr(self.provider, method)
                def slow(*args, **kwargs):
                    entered.set()
                    if not release.wait(8):
                        raise AssertionError("test helper was not released")
                    return original(*args, **kwargs)
                with patch.object(self.provider, method, side_effect=slow) as helper, ThreadPoolExecutor(max_workers=3) as executor:
                    task = executor.submit(self.plan)
                    try:
                        self.assertTrue(entered.wait(3))
                        self.assert_lock_available(executor)
                        self.assertEqual(executor.submit(self.plan).result(timeout=2)["runs"], [])
                        self.assertEqual(helper.call_count, 1)
                    finally:
                        release.set()
                    self.assertEqual(len(task.result(timeout=3)["runs"]), 1)

    def test_late_preparation_cannot_overwrite_successor_lease(self):
        entered, release = Event(), Event()
        original = self.provider.read
        def slow(*args, **kwargs):
            entered.set()
            release.wait(8)
            return original(*args, **kwargs)
        clock = [NOW]
        with patch.object(collection, "_now", side_effect=lambda value: value or clock[0]), patch.object(self.provider, "read", side_effect=slow), ThreadPoolExecutor(max_workers=2) as executor:
            task = executor.submit(self.plan, None)
            try:
                self.assertTrue(entered.wait(3))
                old_id = collection._state(self.project)["active_run"]
                clock[0] = NOW + timedelta(minutes=40)
                provider = MemoryProvider([self.source("new revision doi:10.1234/test")])
                successor = self.plan(None, provider)["runs"][0]
                self.assertNotEqual(successor["run_id"], old_id)
            finally:
                release.set()
            self.assertEqual(task.result(timeout=3)["runs"], [])
        self.assertEqual(collection._state(self.project)["active_run"], successor["run_id"])
        old = collection._read(collection._path(self.project, f"runs/{old_id}.json"), {})
        self.assertNotIn("items", old)

    def test_disable_while_inventory_reads_does_not_publish_frozen_input(self):
        original = self.provider.inventory
        def revoked(*args, **kwargs):
            result = original(*args, **kwargs)
            self.settings(project_ids=[self.pid], literature_enabled=False)
            return result
        with patch.object(self.provider, "inventory", side_effect=revoked):
            self.assertEqual(self.plan()["runs"], [])
        self.assertIsNone(collection._state(self.project)["active_run"])
        self.assertEqual(self.provider.read_count, 0)

    def test_status_is_read_only_and_retry_recovers_expiration_before_plan(self):
        run = self.plan()["runs"][0]
        state_path = collection._path(self.project, "state.json")
        run_path = collection._path(self.project, f"runs/{run['run_id']}.json")
        before = (state_path.read_bytes(), run_path.read_bytes())
        with patch.object(collection, "_now", return_value=NOW + timedelta(minutes=10)):
            self.assertEqual(collection.collection_status(self.pid, self.home)["status"], "failed")
            self.assertEqual((state_path.read_bytes(), run_path.read_bytes()), before)
            self.assertTrue(collection.collection_retry(self.pid, self.home)["accepted"])
        state = collection._state(self.project)
        self.assertIsNone(state["active_run"])
        self.assertEqual(state["failure_count"], 0)
        self.assertTrue(state["retry_requested"])
        self.assertEqual(len(self.plan(NOW + timedelta(minutes=10))["runs"]), 1)
        with self.assertRaises(LiteratureCatalogError) as error:
            self.finish(run, [], NOW + timedelta(minutes=10))
        self.assertEqual(error.exception.code, "RUN_EXPIRED")

    def test_expiry_recovery_envelope_survives_state_write_crash_once(self):
        self.plan()
        expiry = NOW + timedelta(minutes=10)
        with patch.object(collection, "_save_state", side_effect=OSError("state interrupted")):
            with self.assertRaises(OSError):
                self.plan(expiry)
        with patch.object(collection, "_now", return_value=expiry):
            self.assertEqual(collection.collection_status(self.pid, self.home)["failure_count"], 1)
        for _ in range(2):
            self.assertEqual(self.plan(expiry)["runs"], [])
            self.assertEqual(collection._state(self.project)["failure_count"], 1)
        self.assertEqual(collection._state(self.project)["checked_sources"], {})

    def test_live_claim_cannot_be_reset_using_an_earlier_failure(self):
        self.provider.read_error = True
        self.plan()
        self.provider.read_error = False
        self.plan(NOW + timedelta(minutes=30))
        with patch.object(collection, "_now", return_value=NOW + timedelta(minutes=31)):
            with self.assertRaises(LiteratureCatalogError) as error:
                collection.collection_retry(self.pid, self.home)
        self.assertEqual(error.exception.code, "NO_FAILED_RUN")

    def test_slow_sources_authorization_is_unlocked_and_revocation_is_rechecked(self):
        run = self.plan()["runs"][0]
        entered, release = Event(), Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(8)
            return True  # An old helper result must not override current settings.
        with patch.object(self.provider, "authorize", side_effect=slow), ThreadPoolExecutor(max_workers=2) as executor:
            task = executor.submit(collection.literature_sources, run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
            try:
                self.assertTrue(entered.wait(3))
                self.assert_lock_available(executor)
                self.settings(project_ids=[self.pid], literature_enabled=False)
            finally:
                release.set()
            with self.assertRaises(LiteratureCatalogError) as error:
                task.result(timeout=3)
        self.assertEqual(error.exception.code, "FORBIDDEN")
        saved = collection._read(collection._path(self.project, f"runs/{run['run_id']}.json"), {})
        self.assertEqual(saved["read_pages"], [])

    def test_slow_finish_authorization_is_unlocked_and_expiry_blocks_writes(self):
        run, candidates = self.prepare()
        entered, release = Event(), Event()
        clock = [NOW]
        def slow(*args, **kwargs):
            entered.set()
            release.wait(8)
            return True
        with patch.object(collection, "_now", side_effect=lambda value: value or clock[0]), patch.object(self.provider, "authorize", side_effect=slow), ThreadPoolExecutor(max_workers=2) as executor:
            task = executor.submit(self.finish, run, candidates, None)
            try:
                self.assertTrue(entered.wait(3))
                self.assert_lock_available(executor)
                clock[0] = NOW + timedelta(minutes=10)
                self.assertEqual(collection.collection_status(self.pid, self.home)["status"], "failed")
            finally:
                release.set()
            with self.assertRaises(LiteratureCatalogError) as error:
                task.result(timeout=3)
        self.assertEqual(error.exception.code, "RUN_EXPIRED")
        self.assertEqual(self.catalog.list_items()["count"], 0)
        self.assertEqual(collection._state(self.project)["checked_sources"], {})

    def test_each_candidate_authorization_is_outside_lock_and_config_is_rechecked(self):
        run, candidates = self.prepare()
        calls = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            def authorize(*args, **kwargs):
                self.assert_lock_available(executor)
                calls.append(1)
                if len(calls) == 2:
                    self.settings(project_ids=[self.pid], start_date="2026-09-01")
                return True
            with patch.object(self.provider, "authorize", side_effect=authorize):
                with self.assertRaises(LiteratureCatalogError):
                    self.finish(run, candidates)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.catalog.list_items()["count"], 0)

    def test_concurrent_same_finish_returns_one_receipt_and_one_creation(self):
        run, candidates = self.prepare()
        barrier, mutex, calls = Barrier(2), Lock(), []
        def authorize(*args, **kwargs):
            with mutex:
                calls.append(1)
                wait = len(calls) <= 2
            if wait:
                barrier.wait(timeout=4)
            return True
        with patch.object(self.provider, "authorize", side_effect=authorize), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.finish, run, candidates)
            second = executor.submit(self.finish, run, candidates)
            a, b = first.result(timeout=6), second.result(timeout=6)
        self.assertEqual(a, b)
        self.assertEqual(a["counts"]["created"], 1)
        self.assertEqual(self.catalog.list_items()["count"], 1)
        self.assertEqual(len(self.catalog.list_items()["items"][0]["source_refs"]), 1)

    def crash_after_catalog_commit(self, run, candidates):
        original = collection.LiteratureCatalog.upsert
        def interrupted(catalog, *args, **kwargs):
            original(catalog, *args, **kwargs)
            raise OSError("crash after catalog replacement, before result persistence")
        with patch.object(collection.LiteratureCatalog, "upsert", interrupted):
            with self.assertRaises(OSError):
                self.finish(run, candidates)
        self.assertEqual(collection._state(self.project)["checked_sources"], {})
        saved = collection._read(collection._path(self.project, f"runs/{run['run_id']}.json"), {})
        self.assertIn("applying", saved)
        self.assertNotIn("applied_results", saved)

    def test_narrow_create_crash_recovers_counts_and_original_update_time(self):
        run, candidates = self.prepare()
        self.crash_after_catalog_commit(run, candidates)
        item = self.catalog.list_items()["items"][0]
        self.catalog.update_item(item["id"], {"title": "Human edit after crash"}, expected_item_revision=item_revision(item))
        result = self.finish(run, candidates, NOW + timedelta(minutes=1))
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(self.catalog.list_items()["count"], 1)
        self.assertEqual(self.catalog.get_item(item["id"])["title"], "Human edit after crash")
        self.assertEqual(collection._state(self.project)["last_updated_at"], NOW.isoformat())
        saved = collection._read(collection._path(self.project, f"runs/{run['run_id']}.json"), {})
        self.assertNotIn("applying", saved)
        self.assertNotIn("items", saved)

    def test_narrow_update_crash_uses_new_source_proof_despite_human_edit(self):
        original = self.collect(title="Human title")
        run, candidates = self.prepare()
        self.crash_after_catalog_commit(run, candidates)
        item = self.catalog.get_item(original["item_id"])
        self.catalog.update_item(item["id"], {"title": "Later human title"}, expected_item_revision=item_revision(item))
        result = self.finish(run, candidates, NOW + timedelta(minutes=1))
        self.assertEqual(result["counts"]["updated"], 1)
        self.assertEqual(self.catalog.get_item(item["id"])["title"], "Later human title")
        self.assertEqual(collection._state(self.project)["last_updated_at"], NOW.isoformat())

    def test_intent_before_failed_catalog_write_is_not_false_creation(self):
        run, candidates = self.prepare()
        with patch.object(collection.LiteratureCatalog, "_save_catalog", side_effect=OSError("before catalog commit")):
            with self.assertRaises(OSError):
                self.finish(run, candidates)
        self.assertEqual(self.catalog.list_items()["count"], 0)
        item = self.collect(title="Created by human instead")
        result = self.finish(run, candidates, NOW + timedelta(minutes=1))
        self.assertEqual(result["counts"]["created"], 0)
        self.assertEqual(result["counts"]["updated"], 1)
        self.assertEqual(result["results"][0]["item_id"], item["item_id"])

    def test_narrow_crash_then_removal_never_resurrects_item(self):
        run, candidates = self.prepare()
        self.crash_after_catalog_commit(run, candidates)
        item = self.catalog.list_items()["items"][0]
        self.catalog.remove_item(item["id"], expected_item_revision=item_revision(item))
        result = self.finish(run, candidates, NOW + timedelta(minutes=1))
        self.assertEqual(result["results"][0]["reason"], "dismissed")
        self.assertEqual(self.catalog.list_items()["count"], 0)
        self.assertEqual(collection._state(self.project)["last_updated_at"], NOW.isoformat())

    def test_metadata_only_crash_followed_by_human_edit_discloses_uncertain_statistics(self):
        run, candidates = self.prepare()
        source = self.provider.sources[0]
        ref = collection.source_ref(source["kind"], source["locator"], source["revision"], occurred_at=source["occurred_at"],
                                    observed_at=source["observed_at"], collected_at=NOW.isoformat(), summary=source["text"])
        self.catalog.upsert("10.1234/test", source_refs=[ref], explicit=False)
        candidates[0]["title"] = "Evidence title"
        self.crash_after_catalog_commit(run, candidates)
        item = self.catalog.list_items()["items"][0]
        self.catalog.update_item(item["id"], {"title": "Human title"}, expected_item_revision=item_revision(item))
        result = self.finish(run, candidates, NOW + timedelta(minutes=1))
        self.assertEqual(result["counts"]["updated"], 0)
        self.assertIn({"code": "replay_statistics_unproven"}, result["results"][0]["warnings"])
        self.assertEqual(collection._state(self.project)["last_result"], "partial")
        self.assertEqual(self.catalog.get_item(item["id"])["title"], "Human title")

    def test_concurrent_different_finish_cannot_replace_digest_or_receipt(self):
        run, candidates = self.prepare()
        barrier, mutex, calls = Barrier(2), Lock(), []
        def authorize(*args, **kwargs):
            with mutex:
                calls.append(1)
                wait = len(calls) <= 2
            if wait:
                barrier.wait(timeout=4)
            return True
        with patch.object(self.provider, "authorize", side_effect=authorize), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.finish, run, body) for body in (candidates, [])]
            receipts, errors = [], []
            for future in futures:
                try:
                    receipts.append(future.result(timeout=6))
                except LiteratureCatalogError as error:
                    errors.append(error.code)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(errors, ["RECEIPT_CONFLICT"])
        self.assertEqual(self.catalog.list_items()["count"], receipts[0]["counts"]["created"])

    def test_sources_slow_authorizer_cannot_publish_after_lease_expiry(self):
        run = self.plan()["runs"][0]
        clock = [NOW]
        def expired(*args, **kwargs):
            clock[0] = NOW + timedelta(minutes=10)
            return True
        with patch.object(collection, "_now", side_effect=lambda value: value or clock[0]), patch.object(self.provider, "authorize", side_effect=expired):
            with self.assertRaises(LiteratureCatalogError) as error:
                collection.literature_sources(run["run_id"], home=self.home, source_provider=self.provider)
        self.assertEqual(error.exception.code, "RUN_EXPIRED")
        saved = collection._read(collection._path(self.project, f"runs/{run['run_id']}.json"), {})
        self.assertEqual(saved["read_pages"], [])

    def test_status_projects_completed_envelope_without_writing_or_reexpiring(self):
        run, candidates = self.prepare()
        with patch.object(collection, "_save_state", side_effect=OSError("state interrupted")):
            with self.assertRaises(OSError):
                self.finish(run, candidates)
        state_path = collection._path(self.project, "state.json")
        before = state_path.read_bytes()
        with patch.object(collection, "_now", return_value=NOW + timedelta(hours=1)):
            status = collection.collection_status(self.pid, self.home)
        self.assertEqual(status["status"], "idle")
        self.assertEqual(status["failure_count"], 0)
        self.assertEqual(status["pending_count"], 0)
        self.assertEqual(status["last_updated_at"], NOW.isoformat())
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(self.plan(NOW + timedelta(hours=1))["runs"], [])


if __name__ == "__main__":
    unittest.main()
