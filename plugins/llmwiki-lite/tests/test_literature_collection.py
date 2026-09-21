"""Frozen evidence and finish replay tests; fixtures are not live source integration."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_literature_support import Fixture, MemoryProvider, NOW
from literature_catalog import LiteratureCatalogError
import literature_collection as collection


class CollectionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.settings()
        self.provider = MemoryProvider([self.source()])

    def plan(self, now=NOW):
        return collection.literature_plan(home=self.home, now=now, source_provider=self.provider)

    def prepare(self, now=NOW):
        run = self.plan(now)["runs"][0]
        page = collection.literature_sources(run["run_id"], home=self.home, now=now, source_provider=self.provider)
        items = page["items"]
        while page["next_cursor"]:
            page = collection.literature_sources(run["run_id"], page["next_cursor"], home=self.home, now=now, source_provider=self.provider)
            items.extend(page["items"])
        return run, items

    def finish(self, run, candidates=None, now=NOW, **kw):
        return collection.literature_finish(run["run_id"], "reviewed", candidates=candidates, home=self.home, now=now, source_provider=self.provider, **kw)

    def candidate(self, item, **kw):
        return {"locator": "10.1234/test", "source_ids": [item["id"]], "evidence": {item["id"]: item["text"][:1000]}, **kw}

    def test_saved_helpers_and_registered_mcp_end_to_end(self):
        # Real saved-record helpers, real MCP dispatcher; no provider injection.
        import mcp_server
        from research_records import write_record
        write_record(self.project["source_root"], "Saved fixture", "明确论文 https://doi.org/10.1234/test", project_id=self.pid,
                     recorded_at=NOW.isoformat(), state_root=self.project["state_root"])
        manual = Path(self.project["wiki_root"]) / "records/manual/fixture.md"
        manual.parent.mkdir(parents=True, exist_ok=True)
        manual.write_text(f"---\nproject_id: {self.pid}\nrecorded_at: {NOW.isoformat()}\nupdated_at: {NOW.isoformat()}\n---\n普通网站 https://example.com\n", encoding="utf-8")
        provider = collection.ReportSourceProvider()
        self.assertTrue(provider.available)
        inventory, gaps = provider.inventory(self.project, start_date="2026-08-01", now=NOW, home=self.home)
        self.assertEqual({item["kind"] for item in inventory}, {"record", "notebook"})
        self.assertTrue(any("对话未接入" in gap for gap in gaps))
        # Override only the clock, not a source or routing function.
        with patch.object(collection, "_now", return_value=NOW):
            result = mcp_server.dispatch("llmwiki_literature_plan", {"home": self.home})
            run = next(run for run in result["runs"] if run["project_id"] == self.pid)
            page = mcp_server.dispatch("llmwiki_literature_sources", {"run_id": run["run_id"], "home": self.home})
            self.assertIsNone(page["next_cursor"])
            source = next(item for item in page["items"] if item["kind"] == "record")
            candidate = {"locator": "10.1234/test", "source_ids": [source["id"]], "evidence": {source["id"]: "https://doi.org/10.1234/test"}}
            receipt = mcp_server.dispatch("llmwiki_literature_finish", {"run_id": run["run_id"], "outcome": "reviewed", "candidates": [candidate], "home": self.home})
        self.assertEqual(receipt["counts"]["created"], 1)
        manual_result = mcp_server.dispatch("llmwiki_literature_collect", {"project_id": self.pid, "locator": "10.1234/test", "request_id": "e" * 32, "home": self.home})
        self.assertEqual(manual_result["item_id"], receipt["results"][0]["item_id"])
        self.assertEqual(self.catalog.list_items()["count"], 1)

    def test_default_provider_gap_and_disabled_manual_independent(self):
        self.settings(literature_enabled=False)
        self.assertEqual(collection.literature_plan(self.home, now=NOW)["runs"], [])
        self.assertEqual(collection.collection_status(self.pid, self.home)["status"], "disabled")
        self.assertEqual(self.collect()["action"], "created")
        self.settings(runtime={})
        self.assertEqual(collection.collection_status(self.pid, self.home)["status"], "pending_connection")
        self.settings()
        with patch.object(collection, "ReportSourceProvider", return_value=MemoryProvider(gaps=[collection.SOURCE_GAP])):
            result = collection.literature_plan(self.home, now=NOW)
        self.assertEqual(result["runs"], [])
        self.assertTrue(any("未接通" in g["message"] for g in result["gaps"]))

    def test_failed_receipt_recovers_after_state_write_interruption(self):
        run, _ = self.prepare()
        with patch.object(collection, "_save_state", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                collection.literature_finish(run["run_id"], "failed", error_code="MODEL_FAILED", home=self.home, now=NOW, source_provider=self.provider)
        self.assertEqual(collection._state(self.project)["active_run"], run["run_id"])
        result = collection.literature_finish(run["run_id"], "failed", error_code="MODEL_FAILED", home=self.home, now=NOW, source_provider=self.provider)
        self.assertEqual(result["outcome"], "failed")
        state = collection._state(self.project)
        self.assertIsNone(state["active_run"])
        self.assertEqual(state["failure_count"], 1)
        self.assertEqual(state["checked_sources"], {})

    def test_plan_recovers_pending_failed_receipt_once(self):
        run, _ = self.prepare()
        with patch.object(collection, "_save_state", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                collection.literature_finish(run["run_id"], "failed", error_code="MODEL_FAILED", home=self.home, now=NOW, source_provider=self.provider)
        self.assertEqual(self.plan()["runs"], [])
        self.assertEqual(collection._state(self.project)["failure_count"], 1)
        self.assertIsNone(collection._state(self.project)["active_run"])

    def test_markdown_evidence_preserves_balanced_doi_parentheses(self):
        self.assertIn(("doi", "10.1234/test"), collection._evidence_keys("[paper](https://doi.org/10.1234/test)"))
        self.assertIn(("doi", "10.1234/test(2)"), collection._evidence_keys("[paper](https://doi.org/10.1234/test(2))"))

    def test_empty_reviewed_not_failure_and_fulltext_released(self):
        run, _ = self.prepare()
        result = self.finish(run, [])
        self.assertEqual(result["counts"]["created"], 0)
        self.assertEqual(collection._state(self.project)["last_result"], "no_change")
        saved = json.loads(collection._path(self.project, f"runs/{run['run_id']}.json").read_text(encoding="utf-8"))
        self.assertNotIn("items", saved)
        self.assertNotIn("text", saved["source_refs"][0])
        self.assertEqual(saved["source_refs"][0]["summary"], "")
        self.assertIsNone(collection._state(self.project)["last_updated_at"])

    def test_valid_candidate_receipt_idempotent_different_rejected(self):
        run, items = self.prepare()
        candidates = [self.candidate(items[0], title="Evidence title")]
        result = self.finish(run, candidates)
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(self.finish(run, candidates), result)
        self.assertEqual(len(self.catalog.list_items()["items"][0]["source_refs"]), 1)
        with self.assertRaises(LiteratureCatalogError) as ctx:
            self.finish(run, [])
        self.assertEqual(ctx.exception.code, "RECEIPT_CONFLICT")

    def test_all_evidence_validated_before_any_write(self):
        run, items = self.prepare()
        valid = self.candidate(items[0])
        forged = copy.deepcopy(valid)
        forged["evidence"][items[0]["id"]] = "invented https://doi.org/10.1234/fake"
        with self.assertRaises(LiteratureCatalogError) as ctx:
            self.finish(run, [valid, forged])
        self.assertEqual(ctx.exception.code, "INVALID_EVIDENCE")
        self.assertEqual(self.catalog.list_items()["count"], 0)
        self.assertEqual(collection._state(self.project)["checked_sources"], {})
        self.assertEqual(self.finish(run, [valid])["counts"]["created"], 1)

    def test_every_identifier_requires_evidence(self):
        run, items = self.prepare()
        for extra in ({"arxiv": "1803.12345"}, {"doi": "10.1234/forged"}, {"locator": "https://unmentioned.example/paper"}):
            with self.assertRaises(LiteratureCatalogError) as ctx:
                self.finish(run, [self.candidate(items[0], **extra)])
            self.assertEqual(ctx.exception.code, "INVALID_EVIDENCE")
        self.assertEqual(self.catalog.list_items()["count"], 0)

    def test_cross_project_source_id_and_missing_mapping_rejected(self):
        other = self.source(project_id=self.other["id"], locator="record:beta")
        self.provider.sources.append(other)
        runs = self.plan()["runs"]
        pages = {r["project_id"]: collection.literature_sources(r["run_id"], home=self.home, now=NOW, source_provider=self.provider) for r in runs}
        run = next(r for r in runs if r["project_id"] == self.pid)
        candidate = self.candidate(pages[self.other["id"]]["items"][0])
        with self.assertRaises(LiteratureCatalogError):
            self.finish(run, [candidate])
        candidate = self.candidate(pages[self.pid]["items"][0])
        candidate["source_ids"] *= 2
        with self.assertRaises(LiteratureCatalogError):
            self.finish(run, [candidate])

    def test_field_error_skips_one_other_valid_candidate_continues(self):
        run, items = self.prepare()
        candidates = [self.candidate(items[0], title=""), self.candidate(items[0])]
        result = self.finish(run, candidates)
        self.assertEqual((result["counts"]["skipped"], result["counts"]["created"]), (1, 1))
        self.assertEqual(collection._state(self.project)["last_result"], "partial")

    def test_conflicting_identity_skips_candidate_only(self):
        self.collect("10.1234/other", arxiv="1803.12345")
        self.provider.sources[0] = self.source("论文 doi:10.1234/test arXiv:1803.12345，另有论文 doi:10.1234/normal")
        run, items = self.prepare()
        result = self.finish(run, [self.candidate(items[0], arxiv="1803.12345"), self.candidate(items[0], locator="10.1234/normal")])
        self.assertEqual((result["counts"]["skipped"], result["counts"]["created"]), (1, 1))
        self.assertEqual(result["results"][0]["reason"], "IDENTITY_CONFLICT")

    def test_daily_cannot_bind_files_or_change_state(self):
        run, items = self.prepare()
        result = self.finish(run, [self.candidate(items[0], paper_file="p.pdf"), self.candidate(items[0], reading_status="read")])
        self.assertEqual(result["counts"]["skipped"], 2)
        self.assertEqual(self.catalog.list_items()["count"], 0)

    def test_revoke_before_sources_and_finish(self):
        run = self.plan()["runs"][0]
        self.provider.allowed = False
        with self.assertRaises(LiteratureCatalogError):
            collection.literature_sources(run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
        self.provider.allowed = True
        page = collection.literature_sources(run["run_id"], home=self.home, now=NOW, source_provider=self.provider)
        self.settings(literature_enabled=False)
        with self.assertRaises(LiteratureCatalogError):
            self.finish(run, [self.candidate(page["items"][0])])
        self.settings()
        self.provider.allowed = False
        with self.assertRaises(LiteratureCatalogError):
            self.finish(run, [self.candidate(page["items"][0])])
        self.assertEqual(self.catalog.list_items()["count"], 0)

    def test_late_finish_cannot_restore_manual_removal(self):
        a = self.collect()
        run, items = self.prepare()
        self.catalog.remove_item(a["item_id"], expected_item_revision=a["item_revision"])
        result = self.finish(run, [self.candidate(items[0])])
        self.assertEqual(result["results"][0]["reason"], "dismissed")
        self.assertEqual(self.catalog.list_items()["count"], 0)

    def test_partial_write_crash_replay_protects_human_and_no_duplicates(self):
        self.provider.sources[0] = self.source("论文 doi:10.1234/test 和 doi:10.1234/second")
        run, items = self.prepare()
        candidates = [self.candidate(items[0]), self.candidate(items[0], locator="10.1234/second")]
        original = collection.LiteratureCatalog.upsert
        calls = []
        def interrupted(catalog, *args, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("simulated write interruption")
            return original(catalog, *args, **kw)
        with patch.object(collection.LiteratureCatalog, "upsert", interrupted):
            with self.assertRaises(OSError):
                self.finish(run, candidates)
        self.assertEqual(self.catalog.list_items()["count"], 1)
        self.assertEqual(collection._state(self.project)["checked_sources"], {})
        a = self.catalog.list_items()["items"][0]
        from literature_catalog import item_revision
        self.catalog.update_item(a["id"], {"title": "Edited during recovery"}, expected_item_revision=item_revision(a))
        result = self.finish(run, candidates)
        self.assertEqual(result["counts"]["created"], 2)
        self.assertEqual(self.catalog.list_items()["count"], 2)
        self.assertEqual(self.catalog.get_item(a["id"])["title"], "Edited during recovery")
        self.assertEqual(len(self.catalog.get_item(a["id"])["source_refs"]), 1)

    def test_receipt_state_interruption_recovers_without_rewriting_catalog(self):
        run, items = self.prepare()
        candidates = [self.candidate(items[0])]
        with patch.object(collection, "_save_state", side_effect=OSError("state interrupted")):
            with self.assertRaises(OSError):
                self.finish(run, candidates)
        before = self.catalog.catalog_path.read_bytes()
        result = self.finish(run, candidates)
        self.assertEqual(result["counts"]["created"], 1)
        self.assertIsNone(collection._state(self.project)["active_run"])
        self.assertTrue(collection._state(self.project)["checked_sources"])
        self.assertEqual(self.catalog.catalog_path.read_bytes(), before)

    def test_failed_result_retains_old_catalog_retry_never_starts_worker(self):
        self.collect()
        before = self.catalog.catalog_path.read_bytes()
        run, _ = self.prepare()
        result = collection.literature_finish(run["run_id"], "failed", error_code="MODEL_FAILED", home=self.home, now=NOW, source_provider=self.provider)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(self.catalog.catalog_path.read_bytes(), before)
        state = collection.collection_status(self.pid, self.home)
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("text", json.dumps(state))
        with patch("subprocess.Popen", side_effect=AssertionError("worker")), patch.object(self.provider, "read", side_effect=AssertionError("read")):
            self.assertTrue(collection.collection_retry(self.pid, self.home)["accepted"])
        self.assertTrue(collection._state(self.project)["retry_requested"])

    def test_finish_size_and_safe_failure_code(self):
        run, _ = self.prepare()
        with self.assertRaises(LiteratureCatalogError) as ctx:
            self.finish(run, [{"title": "x" * (2 * 1024 * 1024)}])
        self.assertEqual(ctx.exception.status, 413)
        with self.assertRaises(LiteratureCatalogError):
            collection.literature_finish(run["run_id"], "failed", error_code="secret input text", home=self.home, now=NOW)


if __name__ == "__main__":
    unittest.main()
